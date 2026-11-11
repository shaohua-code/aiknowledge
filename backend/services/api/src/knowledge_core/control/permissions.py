"""角色与权限框架。

提供基于角色的访问控制 (RBAC) 框架，支持：
- 平台级角色：平台管理员、平台只读
- 应用级角色：应用维护者、应用只读观察者
- 细粒度权限：按资源类型和操作定义

设计原则：
- 默认拒绝：未明确授权的操作一律拒绝
- 最小权限：角色只包含必要的权限
- 隔离边界：应用级角色无法跨应用访问

使用示例：
    # 检查权限
    if not has_permission(user, Permission.KNOWLEDGE_WRITE, application_id):
        raise ForbiddenError("无权写入知识")

    # 装饰器方式
    @require_permission(Permission.PROFILE_PUBLISH)
    async def publish_profile(context: ApplicationContext, ...):
        ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from functools import wraps
from typing import Any, Callable
from uuid import UUID


class Permission(Enum):
    """权限枚举。

    命名规则：{资源类型}_{操作}
    """

    # 应用管理
    APPLICATION_READ = auto()
    APPLICATION_WRITE = auto()
    APPLICATION_DELETE = auto()

    # 环境管理
    ENVIRONMENT_READ = auto()
    ENVIRONMENT_WRITE = auto()
    ENVIRONMENT_DELETE = auto()

    # API Key 管理
    API_KEY_READ = auto()
    API_KEY_WRITE = auto()
    API_KEY_REVOKE = auto()

    # 知识管理
    KNOWLEDGE_READ = auto()
    KNOWLEDGE_WRITE = auto()
    KNOWLEDGE_DELETE = auto()
    KNOWLEDGE_ARCHIVE = auto()

    # 策略管理
    PROFILE_READ = auto()
    PROFILE_WRITE = auto()
    PROFILE_PUBLISH = auto()
    PROFILE_ROLLBACK = auto()

    # 评测管理
    EVALUATION_READ = auto()
    EVALUATION_WRITE = auto()
    EVALUATION_RUN = auto()

    # 运行追踪
    OPERATIONS_READ = auto()
    OPERATIONS_EXPORT = auto()

    # 模型配置
    MODEL_READ = auto()
    MODEL_WRITE = auto()

    # 平台维护
    PLATFORM_ADMIN = auto()
    PLATFORM_MAINTENANCE = auto()


class Role(Enum):
    """角色枚举。"""

    # 平台级角色
    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_READONLY = "platform_readonly"

    # 应用级角色
    APPLICATION_OWNER = "application_owner"
    APPLICATION_MAINTAINER = "application_maintainer"
    APPLICATION_READONLY = "application_readonly"


# 角色权限映射
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(Permission),  # 所有权限

    Role.PLATFORM_READONLY: frozenset([
        Permission.APPLICATION_READ,
        Permission.ENVIRONMENT_READ,
        Permission.KNOWLEDGE_READ,
        Permission.PROFILE_READ,
        Permission.EVALUATION_READ,
        Permission.OPERATIONS_READ,
        Permission.MODEL_READ,
    ]),

    Role.APPLICATION_OWNER: frozenset([
        Permission.APPLICATION_READ,
        Permission.APPLICATION_WRITE,
        Permission.ENVIRONMENT_READ,
        Permission.ENVIRONMENT_WRITE,
        Permission.ENVIRONMENT_DELETE,
        Permission.API_KEY_READ,
        Permission.API_KEY_WRITE,
        Permission.API_KEY_REVOKE,
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_WRITE,
        Permission.KNOWLEDGE_DELETE,
        Permission.KNOWLEDGE_ARCHIVE,
        Permission.PROFILE_READ,
        Permission.PROFILE_WRITE,
        Permission.PROFILE_PUBLISH,
        Permission.PROFILE_ROLLBACK,
        Permission.EVALUATION_READ,
        Permission.EVALUATION_WRITE,
        Permission.EVALUATION_RUN,
        Permission.OPERATIONS_READ,
        Permission.OPERATIONS_EXPORT,
        Permission.MODEL_READ,
        Permission.MODEL_WRITE,
    ]),

    Role.APPLICATION_MAINTAINER: frozenset([
        Permission.APPLICATION_READ,
        Permission.ENVIRONMENT_READ,
        Permission.KNOWLEDGE_READ,
        Permission.KNOWLEDGE_WRITE,
        Permission.KNOWLEDGE_ARCHIVE,
        Permission.PROFILE_READ,
        Permission.PROFILE_WRITE,
        Permission.PROFILE_PUBLISH,
        Permission.PROFILE_ROLLBACK,
        Permission.EVALUATION_READ,
        Permission.EVALUATION_WRITE,
        Permission.EVALUATION_RUN,
        Permission.OPERATIONS_READ,
        Permission.MODEL_READ,
    ]),

    Role.APPLICATION_READONLY: frozenset([
        Permission.APPLICATION_READ,
        Permission.ENVIRONMENT_READ,
        Permission.KNOWLEDGE_READ,
        Permission.PROFILE_READ,
        Permission.EVALUATION_READ,
        Permission.OPERATIONS_READ,
        Permission.MODEL_READ,
    ]),
}


@dataclass(slots=True)
class UserIdentity:
    """用户身份信息。"""

    user_id: UUID
    email: str
    name: str

    # 平台级角色
    platform_roles: frozenset[Role] = field(default_factory=frozenset)

    # 应用级角色：application_id -> roles
    application_roles: dict[UUID, frozenset[Role]] = field(default_factory=dict)

    def has_platform_role(self, role: Role) -> bool:
        """检查是否具有平台级角色。"""
        return role in self.platform_roles

    def has_application_role(self, application_id: UUID, role: Role) -> bool:
        """检查是否具有应用级角色。"""
        roles = self.application_roles.get(application_id, frozenset())
        return role in roles

    def get_permissions(self, application_id: UUID | None = None) -> frozenset[Permission]:
        """获取用户在指定上下文的权限集合。

        Args:
            application_id: 应用 ID，None 表示平台级

        Returns:
            权限集合
        """
        permissions: set[Permission] = set()

        # 平台级权限
        for role in self.platform_roles:
            permissions.update(ROLE_PERMISSIONS.get(role, frozenset()))

        # 应用级权限
        if application_id:
            for role in self.application_roles.get(application_id, frozenset()):
                permissions.update(ROLE_PERMISSIONS.get(role, frozenset()))

        return frozenset(permissions)


def has_permission(
    user: UserIdentity,
    permission: Permission,
    application_id: UUID | None = None,
) -> bool:
    """检查用户是否具有指定权限。

    Args:
        user: 用户身份
        permission: 要检查的权限
        application_id: 应用 ID（应用级权限检查时必需）

    Returns:
        是否具有权限
    """
    # 平台管理员拥有所有权限
    if Role.PLATFORM_ADMIN in user.platform_roles:
        return True

    permissions = user.get_permissions(application_id)
    return permission in permissions


def has_any_permission(
    user: UserIdentity,
    permissions: list[Permission],
    application_id: UUID | None = None,
) -> bool:
    """检查用户是否具有任一权限。"""
    return any(
        has_permission(user, p, application_id)
        for p in permissions
    )


def has_all_permissions(
    user: UserIdentity,
    permissions: list[Permission],
    application_id: UUID | None = None,
) -> bool:
    """检查用户是否具有所有权限。"""
    return all(
        has_permission(user, p, application_id)
        for p in permissions
    )


class PermissionError(Exception):
    """权限错误。"""

    def __init__(
        self,
        permission: Permission,
        message: str | None = None,
    ):
        self.permission = permission
        self.message = message or f"需要 {permission.name} 权限"
        super().__init__(self.message)


def require_permission(
    permission: Permission,
    get_application_id: Callable[..., UUID | None] | None = None,
):
    """权限检查装饰器。

    Args:
        permission: 要求的权限
        get_application_id: 从参数中提取 application_id 的函数

    使用示例：
        @require_permission(Permission.KNOWLEDGE_WRITE)
        async def create_document(context: ApplicationContext, ...):
            ...

        @require_permission(
            Permission.PROFILE_PUBLISH,
            get_application_id=lambda ctx, **_: ctx.application_id,
        )
        async def publish_profile(ctx, profile_id, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 从参数中获取 user
            user = kwargs.get("user")
            if user is None:
                for arg in args:
                    if isinstance(arg, UserIdentity):
                        user = arg
                        break

            if user is None:
                raise PermissionError(permission, "未提供用户身份")

            # 获取 application_id
            application_id = None
            if get_application_id:
                application_id = get_application_id(*args, **kwargs)

            if not has_permission(user, permission, application_id):
                raise PermissionError(permission)

            return await func(*args, **kwargs)
        return wrapper
    return decorator


@dataclass(slots=True)
class RoleAssignment:
    """角色分配记录。"""

    id: UUID
    user_id: UUID
    role: Role

    # 平台级角色时为 None，应用级角色时为应用 ID
    application_id: UUID | None = None

    # 分配信息
    assigned_by: UUID | None = None
    assigned_at: str | None = None
    expires_at: str | None = None

    def is_platform_level(self) -> bool:
        """是否为平台级角色。"""
        return self.application_id is None

    def is_expired(self) -> bool:
        """是否已过期。"""
        if self.expires_at is None:
            return False
        from datetime import datetime
        return datetime.fromisoformat(self.expires_at) < datetime.now()


def get_role_description(role: Role) -> str:
    """获取角色的中文描述。"""
    descriptions = {
        Role.PLATFORM_ADMIN: "平台管理员：拥有所有权限，可管理所有应用和平台配置",
        Role.PLATFORM_READONLY: "平台只读：可查看所有应用的基本信息，但不能修改",
        Role.APPLICATION_OWNER: "应用所有者：完全管理指定应用，包括环境、密钥、发布和删除",
        Role.APPLICATION_MAINTAINER: "应用维护者：维护应用的知识、策略和评测，不能管理密钥或删除应用",
        Role.APPLICATION_READONLY: "应用只读：查看应用的运行指标和发布记录，不能修改任何配置",
    }
    return descriptions.get(role, role.value)


def get_permission_description(permission: Permission) -> str:
    """获取权限的中文描述。"""
    descriptions = {
        Permission.APPLICATION_READ: "查看应用信息",
        Permission.APPLICATION_WRITE: "修改应用设置",
        Permission.APPLICATION_DELETE: "删除应用",
        Permission.ENVIRONMENT_READ: "查看环境信息",
        Permission.ENVIRONMENT_WRITE: "创建和修改环境",
        Permission.ENVIRONMENT_DELETE: "删除环境",
        Permission.API_KEY_READ: "查看 API Key 列表",
        Permission.API_KEY_WRITE: "创建 API Key",
        Permission.API_KEY_REVOKE: "吊销 API Key",
        Permission.KNOWLEDGE_READ: "查看知识集合和文档",
        Permission.KNOWLEDGE_WRITE: "创建和更新知识",
        Permission.KNOWLEDGE_DELETE: "删除知识",
        Permission.KNOWLEDGE_ARCHIVE: "归档知识",
        Permission.PROFILE_READ: "查看策略配置",
        Permission.PROFILE_WRITE: "创建和修改策略",
        Permission.PROFILE_PUBLISH: "发布策略",
        Permission.PROFILE_ROLLBACK: "回滚策略",
        Permission.EVALUATION_READ: "查看评测集和结果",
        Permission.EVALUATION_WRITE: "创建和修改评测集",
        Permission.EVALUATION_RUN: "执行评测",
        Permission.OPERATIONS_READ: "查看运行追踪",
        Permission.OPERATIONS_EXPORT: "导出运行数据",
        Permission.MODEL_READ: "查看模型配置",
        Permission.MODEL_WRITE: "修改模型配置",
        Permission.PLATFORM_ADMIN: "平台管理权限",
        Permission.PLATFORM_MAINTENANCE: "平台维护操作",
    }
    return descriptions.get(permission, permission.name)


def list_role_permissions(role: Role) -> list[dict[str, Any]]:
    """列出角色包含的所有权限及描述。"""
    permissions = ROLE_PERMISSIONS.get(role, frozenset())
    return [
        {
            "permission": p.name,
            "description": get_permission_description(p),
        }
        for p in sorted(permissions, key=lambda x: x.name)
    ]
