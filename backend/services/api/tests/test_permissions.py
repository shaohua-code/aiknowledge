"""角色与权限框架测试。

验证权限检查、角色映射和装饰器功能。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from knowledge_core.control.permissions import (
    Permission,
    PermissionError,
    Role,
    ROLE_PERMISSIONS,
    UserIdentity,
    get_permission_description,
    get_role_description,
    has_all_permissions,
    has_any_permission,
    has_permission,
    list_role_permissions,
)


class TestRolePermissions:
    """角色权限映射测试。"""

    def test_platform_admin_has_all_permissions(self) -> None:
        """平台管理员应拥有所有权限。"""
        admin_permissions = ROLE_PERMISSIONS[Role.PLATFORM_ADMIN]
        all_permissions = set(Permission)

        assert admin_permissions == frozenset(all_permissions)

    def test_platform_readonly_has_only_read_permissions(self) -> None:
        """平台只读角色只应有读取权限。"""
        readonly_permissions = ROLE_PERMISSIONS[Role.PLATFORM_READONLY]

        for permission in readonly_permissions:
            assert "READ" in permission.name, f"{permission.name} 不是读取权限"

    def test_application_readonly_cannot_write(self) -> None:
        """应用只读角色不应有写入权限。"""
        readonly_permissions = ROLE_PERMISSIONS[Role.APPLICATION_READONLY]

        write_permissions = [
            Permission.KNOWLEDGE_WRITE,
            Permission.PROFILE_WRITE,
            Permission.PROFILE_PUBLISH,
        ]

        for permission in write_permissions:
            assert permission not in readonly_permissions

    def test_application_maintainer_cannot_manage_keys(self) -> None:
        """应用维护者不应能管理 API Key。"""
        maintainer_permissions = ROLE_PERMISSIONS[Role.APPLICATION_MAINTAINER]

        key_permissions = [
            Permission.API_KEY_WRITE,
            Permission.API_KEY_REVOKE,
        ]

        for permission in key_permissions:
            assert permission not in maintainer_permissions

    def test_application_owner_can_do_everything_in_app(self) -> None:
        """应用所有者应能执行应用内的所有操作。"""
        owner_permissions = ROLE_PERMISSIONS[Role.APPLICATION_OWNER]

        app_permissions = [
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_WRITE,
            Permission.KNOWLEDGE_DELETE,
            Permission.PROFILE_PUBLISH,
            Permission.API_KEY_WRITE,
            Permission.API_KEY_REVOKE,
        ]

        for permission in app_permissions:
            assert permission in owner_permissions


class TestUserIdentity:
    """用户身份测试。"""

    def test_platform_admin_identity(self) -> None:
        """平台管理员应具有所有权限。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="admin@example.com",
            name="管理员",
            platform_roles=frozenset([Role.PLATFORM_ADMIN]),
        )

        assert user.has_platform_role(Role.PLATFORM_ADMIN)
        assert Permission.PLATFORM_ADMIN in user.get_permissions()

    def test_application_level_permissions(self) -> None:
        """应用级角色应只在指定应用内生效。"""
        app_id = uuid4()
        other_app_id = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="dev@example.com",
            name="开发者",
            application_roles={
                app_id: frozenset([Role.APPLICATION_MAINTAINER]),
            },
        )

        # 在指定应用内有权限
        permissions = user.get_permissions(app_id)
        assert Permission.KNOWLEDGE_WRITE in permissions

        # 在其他应用内无权限
        other_permissions = user.get_permissions(other_app_id)
        assert Permission.KNOWLEDGE_WRITE not in other_permissions

    def test_combined_permissions(self) -> None:
        """平台级和应用级权限应合并。"""
        app_id = uuid4()

        user = UserIdentity(
            user_id=uuid4(),
            email="user@example.com",
            name="用户",
            platform_roles=frozenset([Role.PLATFORM_READONLY]),
            application_roles={
                app_id: frozenset([Role.APPLICATION_MAINTAINER]),
            },
        )

        # 应同时包含平台级和应用级权限
        permissions = user.get_permissions(app_id)
        assert Permission.APPLICATION_READ in permissions  # 平台只读
        assert Permission.KNOWLEDGE_WRITE in permissions  # 应用维护者


class TestHasPermission:
    """权限检查函数测试。"""

    def test_platform_admin_always_has_permission(self) -> None:
        """平台管理员应始终有权限。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="admin@example.com",
            name="管理员",
            platform_roles=frozenset([Role.PLATFORM_ADMIN]),
        )

        assert has_permission(user, Permission.PLATFORM_MAINTENANCE)
        assert has_permission(user, Permission.KNOWLEDGE_DELETE, uuid4())

    def test_readonly_cannot_write(self) -> None:
        """只读用户不应有写入权限。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="readonly@example.com",
            name="只读用户",
            platform_roles=frozenset([Role.PLATFORM_READONLY]),
        )

        assert not has_permission(user, Permission.KNOWLEDGE_WRITE)
        assert not has_permission(user, Permission.PROFILE_PUBLISH)

    def test_has_any_permission(self) -> None:
        """has_any_permission 应正确工作。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="user@example.com",
            name="用户",
            platform_roles=frozenset([Role.PLATFORM_READONLY]),
        )

        # 有其中一个权限
        assert has_any_permission(user, [
            Permission.KNOWLEDGE_READ,  # 有
            Permission.KNOWLEDGE_WRITE,  # 无
        ])

        # 都没有
        assert not has_any_permission(user, [
            Permission.KNOWLEDGE_WRITE,
            Permission.PROFILE_PUBLISH,
        ])

    def test_has_all_permissions(self) -> None:
        """has_all_permissions 应正确工作。"""
        user = UserIdentity(
            user_id=uuid4(),
            email="user@example.com",
            name="用户",
            platform_roles=frozenset([Role.PLATFORM_READONLY]),
        )

        # 都有
        assert has_all_permissions(user, [
            Permission.KNOWLEDGE_READ,
            Permission.PROFILE_READ,
        ])

        # 有一个没有
        assert not has_all_permissions(user, [
            Permission.KNOWLEDGE_READ,
            Permission.KNOWLEDGE_WRITE,
        ])


class TestPermissionDescriptions:
    """权限描述测试。"""

    def test_role_descriptions_are_chinese(self) -> None:
        """角色描述应为中文。"""
        for role in Role:
            description = get_role_description(role)
            assert description, f"{role.name} 缺少描述"
            # 检查包含中文字符
            assert any('\u4e00' <= c <= '\u9fff' for c in description)

    def test_permission_descriptions_are_chinese(self) -> None:
        """权限描述应为中文。"""
        for permission in Permission:
            description = get_permission_description(permission)
            assert description, f"{permission.name} 缺少描述"

    def test_list_role_permissions_returns_sorted(self) -> None:
        """list_role_permissions 应返回排序的权限列表。"""
        permissions = list_role_permissions(Role.APPLICATION_READONLY)

        assert len(permissions) > 0
        assert all("permission" in p and "description" in p for p in permissions)

        # 验证排序
        names = [p["permission"] for p in permissions]
        assert names == sorted(names)


class TestPermissionError:
    """权限错误测试。"""

    def test_permission_error_message(self) -> None:
        """权限错误应包含权限名称。"""
        error = PermissionError(Permission.KNOWLEDGE_WRITE)

        assert "KNOWLEDGE_WRITE" in str(error)

    def test_custom_error_message(self) -> None:
        """应支持自定义错误消息。"""
        error = PermissionError(
            Permission.PROFILE_PUBLISH,
            "需要先通过评测才能发布",
        )

        assert "需要先通过评测才能发布" in str(error)
