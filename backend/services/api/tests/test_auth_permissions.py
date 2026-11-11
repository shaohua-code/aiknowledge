"""认证与权限集成测试。

验证 auth.py 与 permissions.py 的集成。
"""

from __future__ import annotations

from uuid import uuid4

from knowledge_core.control.permissions import (
    Permission,
    Role,
    UserIdentity,
    has_permission,
)


class TestAuthPermissionsIntegration:
    """认证权限集成测试。"""

    def test_platform_admin_has_all_permissions(self) -> None:
        """平台管理员应具有所有权限。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="admin@example.com",
            name="Admin",
            platform_roles=frozenset([Role.PLATFORM_ADMIN]),
        )

        # 测试各种权限
        assert has_permission(user, Permission.KNOWLEDGE_WRITE)
        assert has_permission(user, Permission.PROFILE_PUBLISH)
        assert has_permission(user, Permission.PLATFORM_MAINTENANCE)
        assert has_permission(user, Permission.API_KEY_REVOKE)

    def test_application_maintainer_scope(self) -> None:
        """应用维护者应在其应用范围内有权限。"""
        app_id = uuid4()
        other_app_id = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="maintainer@example.com",
            name="Maintainer",
            application_roles={
                app_id: frozenset([Role.APPLICATION_MAINTAINER]),
            },
        )

        # 在其应用内有知识写入权限
        assert has_permission(user, Permission.KNOWLEDGE_WRITE, app_id)

        # 在其他应用内无权限
        assert not has_permission(user, Permission.KNOWLEDGE_WRITE, other_app_id)

        # 无平台级权限
        assert not has_permission(user, Permission.PLATFORM_MAINTENANCE)

    def test_readonly_user_cannot_modify(self) -> None:
        """只读用户不能修改资源。"""
        app_id = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="viewer@example.com",
            name="Viewer",
            application_roles={
                app_id: frozenset([Role.APPLICATION_READONLY]),
            },
        )

        # 有读取权限
        assert has_permission(user, Permission.KNOWLEDGE_READ, app_id)
        assert has_permission(user, Permission.PROFILE_READ, app_id)
        assert has_permission(user, Permission.OPERATIONS_READ, app_id)

        # 无写入权限
        assert not has_permission(user, Permission.KNOWLEDGE_WRITE, app_id)
        assert not has_permission(user, Permission.PROFILE_PUBLISH, app_id)

    def test_combined_roles(self) -> None:
        """用户可同时拥有平台级和应用级角色。"""
        app_id = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="power@example.com",
            name="Power User",
            platform_roles=frozenset([Role.PLATFORM_READONLY]),
            application_roles={
                app_id: frozenset([Role.APPLICATION_OWNER]),
            },
        )

        # 平台只读权限
        permissions = user.get_permissions()
        assert Permission.APPLICATION_READ in permissions

        # 应用所有者权限
        app_permissions = user.get_permissions(app_id)
        assert Permission.API_KEY_WRITE in app_permissions
        assert Permission.KNOWLEDGE_DELETE in app_permissions


class TestUserIdentityCreation:
    """用户身份创建测试。"""

    def test_empty_roles(self) -> None:
        """无角色的用户应无权限。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="nobody@example.com",
            name="Nobody",
        )

        assert not has_permission(user, Permission.KNOWLEDGE_READ)
        assert not has_permission(user, Permission.APPLICATION_READ)

    def test_multiple_application_roles(self) -> None:
        """用户可在多个应用中有不同角色。"""
        app1 = uuid4()
        app2 = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="multi@example.com",
            name="Multi App User",
            application_roles={
                app1: frozenset([Role.APPLICATION_OWNER]),
                app2: frozenset([Role.APPLICATION_READONLY]),
            },
        )

        # 在 app1 中是所有者
        assert has_permission(user, Permission.API_KEY_REVOKE, app1)

        # 在 app2 中只是只读
        assert not has_permission(user, Permission.API_KEY_REVOKE, app2)
        assert has_permission(user, Permission.KNOWLEDGE_READ, app2)
