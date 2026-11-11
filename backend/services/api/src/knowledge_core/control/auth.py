"""控制面会话认证模块。

提供管理员会话认证和权限检查功能：
- 基于签名 Cookie 的会话管理
- 管理员登录/登出
- 权限框架集成（RBAC）

安全特性：
- HttpOnly Cookie 防止 XSS
- 签名防止 Cookie 篡改
- 可配置的会话过期时间
"""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import Field

from knowledge_core.config import settings
from knowledge_core.control.permissions import (
    Permission,
    Role,
    UserIdentity,
    has_permission,
)
from knowledge_core.shared.errors import AuthenticationError, ForbiddenError
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSchema, ApiSuccess

router = APIRouter(prefix="/control/v1/session", tags=["控制面会话"])
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="aik-admin-session-v2")


class LoginRequest(ApiSchema):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    password: str


class AdminSessionResponse(ApiSchema):
    """已认证管理员会话的安全视图，Cookie 本体不会出现在 JSON 中。"""

    email: str
    role: str


class LogoutResponse(ApiSchema):
    """清除管理员会话 Cookie 后的最小确认信息。"""

    logged_out: bool


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
    """要求管理员会话的依赖函数。

    用于控制面路由的基础认证，验证会话 Cookie 有效性。

    Args:
        session_token: 会话 Cookie 值

    Returns:
        管理员邮箱

    Raises:
        AuthenticationError: 未登录或会话已失效
    """
    if not session_token:
        raise AuthenticationError("请先登录管理控制台")
    return _verify_session(session_token)


async def get_current_user(
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> UserIdentity:
    """获取当前用户身份的依赖函数。

    从会话 Cookie 中解析用户身份信息，包括角色和权限。
    当前实现中，已认证的管理员具有平台管理员角色。

    Args:
        session_token: 会话 Cookie 值

    Returns:
        用户身份对象

    Raises:
        AuthenticationError: 未登录或会话已失效
    """
    if not session_token:
        raise AuthenticationError("请先登录管理控制台")

    email = _verify_session(session_token)

    # 当前实现：已认证的管理员具有平台管理员角色
    # 未来可从数据库加载用户角色
    return UserIdentity(
        user_id=UUID(int=0),  # 占位 ID，未来从数据库获取
        email=email,
        name=email.split("@")[0],
        platform_roles=frozenset([Role.PLATFORM_ADMIN]),
        application_roles={},
    )


def _application_id_from_request(request: Request) -> UUID | None:
    """从路径参数读取应用 ID。没有该参数时按平台级权限检查。"""
    raw = request.path_params.get("application_id")
    if not raw:
        return None
    return UUID(str(raw))


def permission_for_method(
    method: str,
    read_permission: Permission,
    write_permission: Permission,
    delete_permission: Permission | None = None,
) -> Permission:
    """按 HTTP 方法选择读、写或删除权限。"""
    normalized = method.upper()
    if normalized in {"GET", "HEAD", "OPTIONS"}:
        return read_permission
    if normalized == "DELETE":
        return delete_permission or write_permission
    return write_permission


def require_permission(permission: Permission):
    """创建检查单一权限的依赖。应用 ID 只来自路径，不新增查询参数。"""

    async def _check_permission(
        request: Request,
        user: Annotated[UserIdentity, Depends(get_current_user)],
    ) -> UserIdentity:
        application_id = _application_id_from_request(request)
        if not has_permission(user, permission, application_id):
            raise ForbiddenError(f"需要 {permission.name} 权限")
        return user

    return _check_permission


def require_control_permission(
    read_permission: Permission,
    write_permission: Permission,
    *,
    delete_permission: Permission | None = None,
):
    """按请求方法检查控制面权限。

    已登录管理员当前映射为平台管理员，因此既有控制台调用仍然放行。
    只读角色会被写操作拒绝。应用级角色只在路径中的应用内生效。
    """

    async def _check_permission(
        request: Request,
        user: Annotated[UserIdentity, Depends(get_current_user)],
    ) -> UserIdentity:
        needed = permission_for_method(
            request.method,
            read_permission,
            write_permission,
            delete_permission,
        )
        application_id = _application_id_from_request(request)
        if not has_permission(user, needed, application_id):
            raise ForbiddenError(f"需要 {needed.name} 权限")
        return user

    return _check_permission


@router.post("/login", response_model=ApiSuccess[AdminSessionResponse])
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


@router.get("/me", response_model=ApiSuccess[AdminSessionResponse])
async def me(email: Annotated[str, Depends(require_admin)]) -> dict:
    return success({"email": email, "role": "admin"})


@router.delete("", response_model=ApiSuccess[LogoutResponse])
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return success({"loggedOut": True})
