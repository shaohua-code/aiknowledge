"""SQLAlchemy 2 声明式基类与通用 Mixin。

对应 SubTask 4.1 / 4.2：所有业务模型共用的 Base、时间戳混入、项目归属混入。

设计要点
--------
1. 使用 SQLAlchemy 2.0 风格的 ``DeclarativeBase``，配合 ``Mapped[T]`` / ``mapped_column()``
   以获得更好的类型推导与 IDE 支持。
2. 所有时间字段统一使用 ``TIMESTAMP(timezone=True)``（PostgreSQL ``timestamptz``），
   保证多时区部署下时间可比较、可还原。
3. ``ProjectOwnedMixin`` 强制业务表带 ``project_id``，是项目级强隔离的物理基础。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    """所有 ORM 模型的统一声明基类。

    使用 ``DeclarativeBase``（SQLAlchemy 2.0 推荐）替代旧版
    ``declarative_base()`` 工厂函数，便于在 ``Mapped[]`` 类型注解下
    进行静态类型检查。
    """


class TimestampMixin:
    """时间戳混入：为模型添加创建/更新时间字段。

    字段
    ----
    created_at:
        记录创建时间，数据库层默认 ``now()``，应用层无需显式赋值。
    updated_at:
        记录最近更新时间，默认 ``now()``，每次 UPDATE 自动刷新（``onupdate=now()``）。

    设计原因
    --------
    所有业务表都需要审计时间，集中通过 Mixin 复用避免遗漏；
    使用 ``server_default`` 让默认值在数据库层生效，即使裸 SQL 插入也能正确填充。
    """

    # 创建时间：数据库默认 now()，插入时无需应用层赋值
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="记录创建时间（带时区）",
    )
    # 更新时间：默认 now()，每次 UPDATE 时数据库自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="记录最近更新时间（带时区，自动刷新）",
    )


class ProjectOwnedMixin:
    """项目归属混入：为业务表添加 project_id 字段。

    字段
    ----
    project_id:
        所属项目 ID（UUID），NOT NULL，建立 B-tree 索引。
        所有业务表必须包含此字段，作为项目级强隔离的物理基础。

    设计原因
    --------
    PRD 强制要求项目级强隔离：所有查询、向量检索、对象存储都必须以
    ``project_id`` 前置过滤。将此字段抽到 Mixin 中，配合复合外键
    （如 ``ForeignKeyConstraint(["project_id", "knowledge_base_id"], ...)``）
    可避免子表引用父表时跨项目污染。

    索引
    ----
    单独为 ``project_id`` 建立 B-tree 索引，加速按项目过滤的全表扫描。
    """

    # 项目 ID：所有业务表的强隔离主键，禁止为空，建立索引加速按项目过滤
    project_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        nullable=False,
        index=True,
        comment="所属项目 ID，业务表强隔离主键",
    )
