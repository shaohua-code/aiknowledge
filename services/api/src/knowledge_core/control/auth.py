from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import Field

from knowledge_core.config import settings
from knowledge_core.shared.errors import AuthenticationError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSchema

router = APIRouter(prefix="/control/v1/session", tags=["控制面会话"])
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="aik-admin-session-v2")


class LoginRequest(ApiSchema):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    password: str


def _verify_session(token: str) -> str:
    try:
        payload = _serializer.loads(token, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired) as exc:
        raise AuthenticationError("管理员会话已失效，请重新登录") from exc
    if not isinstance(payload, dict) or payload.get("role") != "admin":
        raise AuthenticationError("管理员会话无效")
    return str(payload.get("email", ""))


async def require_admin(
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> str:
    if not session_token:
        raise AuthenticationError("请先登录管理控制台")
    return _verify_session(session_token)


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    if not settings.admin_password:
        raise AuthenticationError(
            "管理员密码尚未配置",
            suggestion="请在服务端配置 ADMIN_PASSWORD 后重试",
        )
    email_ok = secrets.compare_digest(payload.email.lower(), settings.admin_email.lower())
    password_ok = secrets.compare_digest(payload.password, settings.admin_password)
    if not (email_ok and password_ok):
        raise AuthenticationError("邮箱或密码错误")

    token = _serializer.dumps({"email": settings.admin_email, "role": "admin"})
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return success({"email": settings.admin_email, "role": "admin"})


@router.get("/me")
async def me(email: Annotated[str, Depends(require_admin)]) -> dict:
    return success({"email": email, "role": "admin"})


@router.delete("")
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return success({"loggedOut": True})
