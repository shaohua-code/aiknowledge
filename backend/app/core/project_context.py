"""项目上下文：通过 API Key 解析得到的只读身份信息。

对应 SubTask 5.1：定义 @dataclass(frozen=True) ProjectContext。

设计理念（务必阅读）
--------------------
1. 不可变（frozen=True）
   ProjectContext 一旦由服务端在鉴权依赖 ``get_project_context`` 中构造完成，
   即不可再修改。任何后续业务代码（Repository、Service）拿到的都是同一份只读快照，
   防止在请求处理链路中被误改或恶意篡改（例如把 project_id 改成别的项目）。

2. 由服务端解析
   ProjectContext 中的 project_id / project_code / scopes 等字段，
   全部来自数据库中 ``api_keys`` + ``projects`` 表的查询结果，
   严禁信任请求体（body）中客户端传入的 project_id / scopes 字段。
   客户端只能通过 ``X-Project-Code`` 头做一致性校验，不能覆盖服务端结论。

3. 禁止请求体覆盖
   任何接口的请求体 schema 中不应出现 ``project_id`` / ``api_key_id`` / ``scopes``
   等敏感字段；如确需传入，仅作为业务参数（如资源 ID），不能用于身份判定。
   Repository 层 MUST 在 WHERE 子句中带上 ``ctx.project_id``，实现物理隔离。

4. 单一入口
   ProjectContext 仅由 ``app.api.dependencies.get_project_context`` 依赖构造，
   其它代码不应自行 new 一个 ProjectContext，避免绕过鉴权。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NewType

# 使用 NewType 增强类型安全，避免 project_id 与其他 UUID 字符串混用
# 例如函数签名 ProjectId 与普通 str 区分，便于静态检查发现参数错位
ProjectId = NewType("ProjectId", str)


@dataclass(frozen=True)
class ProjectContext:
    """项目上下文：通过 API Key 解析得到的只读身份信息。

    所有 Repository 方法 MUST 接收此对象并在 WHERE 中带 project_id，
    实现项目级强隔离。``frozen=True`` 保证请求处理过程中不可被篡改。

    Attributes:
        project_id: 项目唯一标识（UUID 字符串），所有业务表的外键锚点。
        project_code: 项目代码（如 ai-fund、ai-resume），大小写不敏感，
            用于路由 ``/projects/{projectCode}`` 与一致性校验。
        environment: 运行环境（development / staging / production / collector），
            从 api_keys.environment 继承，便于灰度与采集器隔离。
        api_key_id: 调用方 API Key 的数据库主键（UUID），
            用于审计日志、last_used_at 更新、限流计数。
        scopes: API Key 授权的 Scope 列表（tuple 不可变），
            形如 ``("retrieval:read", "research:run")``，
            接口通过 ``require_scopes`` 依赖校验。
        allowed_tools: 工具白名单（tuple[str, ...]，默认空），
            用于后续 research 链路中对 LLM 工具调用的二次校验，
            避免某项目 Key 调用未授权的工具（如 ai-resume 调用 fund_market）。
            空表示未配置白名单（不限制）。
    """

    project_id: str
    project_code: str
    # 默认 development，生产环境通过 api_keys.environment 注入
    environment: str = "development"
    # api_key_id 可空（如管理密钥场景），业务接口依赖中应非空
    api_key_id: str | None = None
    # scopes 默认空 tuple，表示无任何权限（最严格默认）
    scopes: tuple[str, ...] = field(default_factory=tuple)
    # allowed_tools 默认空 tuple，表示未配置白名单（后续逻辑按"未配置=不限制"处理）
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)

    def has_scope(self, scope: str) -> bool:
        """判断当前上下文是否拥有某个 Scope。

        Args:
            scope: 待校验的 Scope 字符串，如 ``"retrieval:read"``。

        Returns:
            True 表示该 Scope 在 ``self.scopes`` 中；False 表示缺失。
            当 scopes 为空时返回 False（最小权限原则）。

        Example:
            >>> ctx.has_scope("retrieval:read")
            True
            >>> ctx.has_scope("knowledge:write")
            False
        """
        # 直接成员判断；tuple 的 in 操作是 O(n)，但 Scope 数量通常 < 20，无需哈希优化
        return scope in self.scopes

    def require_scopes(self, *scopes: str) -> bool:
        """判断当前上下文是否拥有全部指定 Scope（AND 语义）。

        用于接口级别的权限校验：只有当所有 required_scopes 都在 self.scopes 中时
        才返回 True。若需要 OR 语义，请调用方自行多次调用 has_scope。

        Args:
            *scopes: 需要校验的 Scope 列表，可变参数。
                例：``ctx.require_scopes("retrieval:read", "research:run")``。

        Returns:
            True 表示全部 Scope 都已授权；False 表示至少一个缺失。

        Note:
            当 scopes 为空参数时返回 True（无要求即放行），
            调用方应在 ``require_scopes()`` 依赖中保证至少传入一个 Scope。
        """
        # all() 对空可迭代对象返回 True，符合"无要求即放行"的语义
        return all(s in self.scopes for s in scopes)
