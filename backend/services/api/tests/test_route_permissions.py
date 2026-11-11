"""控制面路由权限依赖测试。

用最小 FastAPI 应用验证读、写和删除权限，不连接数据库。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from knowledge_core.control.auth import (
    get_current_user,
    permission_for_method,
    require_control_permission,
)
from knowledge_core.control.permissions import Permission, Role, UserIdentity
from knowledge_core.shared.errors import CoreError


def _install_error_handler(app: FastAPI) -> None:
    """把权限错误转成与正式应用一致的 HTTP 状态。"""

    @app.exception_handler(CoreError)
    async def _handle_core_error(_request, exc: CoreError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.http_status, content={"message": exc.message})


def _client(user: UserIdentity) -> TestClient:
    """构造只包含一条受权限保护路由的应用。"""
    router = APIRouter()
    guard = require_control_permission(
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_WRITE,
        delete_permission=Permission.KNOWLEDGE_ARCHIVE,
    )

    @router.get("/apps/{application_id}/docs")
    async def read_docs(_user=Depends(guard)) -> dict:
        return {"ok": True}

    @router.post("/apps/{application_id}/docs")
    async def write_docs(_user=Depends(guard)) -> dict:
        return {"ok": True}

    @router.delete("/apps/{application_id}/docs")
    async def archive_docs(_user=Depends(guard)) -> dict:
        return {"ok": True}

    app = FastAPI()
    _install_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _user(role: Role, application_id) -> UserIdentity:
    return UserIdentity(
        user_id=uuid4(),
        email="member@example.com",
        name="Member",
        application_roles={application_id: frozenset([role])},
    )


class TestPermissionSelection:
    """方法到权限的映射。"""

    def test_method_mapping(self) -> None:
        """读、写、删除应分别落到声明的权限。"""
        assert (
            permission_for_method("GET", Permission.KNOWLEDGE_READ, Permission.KNOWLEDGE_WRITE)
            is Permission.KNOWLEDGE_READ
        )
        assert (
            permission_for_method("POST", Permission.KNOWLEDGE_READ, Permission.KNOWLEDGE_WRITE)
            is Permission.KNOWLEDGE_WRITE
        )
        assert (
            permission_for_method(
                "DELETE",
                Permission.KNOWLEDGE_READ,
                Permission.KNOWLEDGE_WRITE,
                Permission.KNOWLEDGE_ARCHIVE,
            )
            is Permission.KNOWLEDGE_ARCHIVE
        )


class TestRoutePermissionChecks:
    """路由依赖按角色拒绝或放行。"""

    def test_readonly_can_read_but_not_write(self) -> None:
        """应用只读角色可以读取，不能写入或归档。"""
        application_id = uuid4()
        client = _client(_user(Role.APPLICATION_READONLY, application_id))

        assert client.get(f"/apps/{application_id}/docs").status_code == 200
        assert client.post(f"/apps/{application_id}/docs").status_code == 403
        assert client.delete(f"/apps/{application_id}/docs").status_code == 403

    def test_maintainer_can_archive_inside_application(self) -> None:
        """维护者在所属应用内可以归档。"""
        application_id = uuid4()
        client = _client(_user(Role.APPLICATION_MAINTAINER, application_id))

        assert client.delete(f"/apps/{application_id}/docs").status_code == 200

    def test_role_does_not_cross_applications(self) -> None:
        """应用角色不能访问另一个应用。"""
        owned = uuid4()
        other = uuid4()
        client = _client(_user(Role.APPLICATION_MAINTAINER, owned))

        assert client.get(f"/apps/{other}/docs").status_code == 403
