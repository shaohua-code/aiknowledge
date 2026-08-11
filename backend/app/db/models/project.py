"""项目相关模型：Project / ApiKey / ProjectSettings。

对应 SubTask 4.1：项目主表与环境密钥、独立设置表。

设计要点
--------
1. ``Project`` 是平台主表，自身不含 ``project_id``（避免自引用）；
   其余所有业务表通过 ``ProjectOwnedMixin`` 反向归属到本项目。
2. ``code`` 使用 CIText 大小写不敏感，作为对外稳定标识，创建后不可改；
   ``ApiKey.key_hash`` 只存哈希、不存明文，符合密钥安全规范。
3. ``ProjectSettings`` 独立成表（而非合并到 ``Project.settings`` JSONB），
   便于强类型校验与后台单独编辑。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
# CIText 在 SQLAlchemy 2.0+ 需自定义（见 app.db.models.types）
from app.db.models.types import CIText
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class Project(Base, TimestampMixin):
    """项目主表：平台多租户顶层实体。

    每个项目对应一个业务方（如 ai-fund、ai-resume），其下挂载知识库、
    API Key、采集源、定时任务等所有资源。项目级强隔离以本表 ``id`` 为锚点。

    表名: projects
    """

    __tablename__ = "projects"

    # 主键 UUID：由 PostgreSQL pgcrypto 提供的 gen_random_uuid() 生成
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="项目 ID（UUID，数据库生成）",
    )
    # 项目编码：CIText 大小写不敏感，UNIQUE，对外稳定标识，创建后不可改
    # 例：ai-fund、ai-resume，路由 /projects/{projectCode} 使用
    code: Mapped[str] = mapped_column(
        CIText(),
        unique=True,
        nullable=False,
        comment="项目编码（大小写不敏感，唯一，创建后不可改）",
    )
    # 项目显示名：用于后台与日志展示
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="项目显示名")
    # 项目描述：可空，便于后台备注
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="项目描述")
    # 项目状态：active=启用 / disabled=停用，停用后 API Key 校验失败
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
        comment="项目状态：active / disabled",
    )
    # 扩展设置：JSONB，存储模型偏好、超时、证据数等运行时配置（可被 ProjectSettings 覆盖）
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="项目扩展设置（JSONB）"
    )


class ApiKey(Base, ProjectOwnedMixin, TimestampMixin):
    """项目 API Key 表：用于对外 API 鉴权与 Scope 控制。

    设计原则
    --------
    1. 只存 ``key_hash``（Argon2 哈希），明文仅在创建时返回一次。
    2. ``key_prefix`` 用于后台识别 Key 归属（如 ``ikh_live_`` 前缀），不参与鉴权。
    3. ``scopes`` 使用 ARRAY(TEXT)，列举允许的 API 范围，避免单 Key 拥有全部权限。
    4. ``environment`` 区分 dev/staging/production/collector，便于灰度与采集器隔离。

    表名: api_keys
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="API Key ID",
    )
    # 环境：dev=开发 / staging=预发 / production=生产 / collector=采集器专用
    environment: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="环境：dev / staging / production / collector"
    )
    # Key 显示名：便于后台管理
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="Key 显示名")
    # Key 前缀：用于后台识别（如 ikh_live_），不参与鉴权计算
    key_prefix: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="Key 前缀，用于后台识别"
    )
    # Key 哈希：仅存哈希（Argon2），不可逆
    key_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="Key 哈希（Argon2），仅存哈希"
    )
    # 权限范围：列举允许的 API，如 ['retrieval:read','research:run']
    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="权限范围数组"
    )
    # 最近使用时间：可空，用于后台展示与清理
    last_used_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="最近使用时间"
    )
    # 过期时间：可空，空表示永不过期
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True, comment="过期时间")
    # 状态：active=启用 / revoked=已吊销
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
        comment="状态：active / revoked",
    )


class ProjectSettings(Base, ProjectOwnedMixin, TimestampMixin):
    """项目设置表：模型/超时/证据数等可配置项。

    PRD 要求独立成表（而非合并到 ``Project.settings`` JSONB），便于：
    1. 字段强类型校验（避免 JSONB 自由结构带来的脏数据）。
    2. 后台单独编辑与审计。
    3. 与项目主表解耦，避免修改设置触发项目行锁。

    表名: project_settings
    """

    __tablename__ = "project_settings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="设置 ID",
    )
    # 聊天模型名称：覆盖全局默认 chat_model
    chat_model: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="项目聊天模型名称"
    )
    # Embedding 模型名称：覆盖全局默认 embedding_model
    embedding_model: Mapped[str | None] = mapped_column(
        String(120), nullable=True, comment="项目 Embedding 模型名称"
    )
    # 是否启用联网搜索：默认 false，关闭时 research 策略降级为 knowledge_only
    web_search_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否启用联网搜索",
    )
    # 允许域名白名单：联网搜索/采集时仅允许这些域名
    allowed_domains: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="允许域名白名单"
    )
    # 禁用域名黑名单：联网搜索/采集时屏蔽这些域名
    blocked_domains: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="禁用域名黑名单"
    )
    # 单次研究最大证据数：默认 8，对应 PRD 链路限制
    max_evidence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("8"),
        comment="单次研究最大证据数",
    )
    # 大模型最大 token 数：可空，空表示使用模型默认
    max_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="大模型最大 token 数"
    )
    # 单次研究整体超时秒数：默认 15s，对应 PRD 链路硬超时
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("15"),
        comment="研究整体超时秒数",
    )
