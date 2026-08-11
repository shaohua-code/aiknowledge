"""爬虫相关模型：CrawlSource / CrawlRun / CrawlPage / WebMaterial / SourcePolicy。

对应 SubTask 4.1：网络采集源、运行记录、页面、待审核资料池、来源策略。

设计要点
--------
1. ``CrawlSource`` 定义采集源（类型/起始 URL/域名限制/入库策略），
   ``code`` 在项目内唯一（复合唯一索引）。
2. ``CrawlRun`` 记录每次采集运行，``CrawlPage`` 记录单个页面抓取结果，
   ``canonical_url_hash`` 用于 URL 去重（复合唯一索引）。
3. ``WebMaterial`` 是网络待审核资料池，``review_required`` 策略下采集结果先入此表，
   人工审核通过后才入库到知识库。
4. ``SourcePolicy`` 记录可信/禁用来源域名，供采集与联网搜索共用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin


class CrawlSource(Base, ProjectOwnedMixin, TimestampMixin):
    """采集源表：定义网络采集任务。

    表名: crawl_sources

    复合唯一约束
    ------------
    ``uq_crawl_sources_project_code (project_id, code)``：
    采集源 code 在项目内唯一。

    字段说明
    --------
    code:
        采集源编码，项目内唯一。
    name:
        采集源名称。
    type:
        采集类型：single_page=单页 / url_list=URL 列表 /
        rss=RSS / sitemap=站点地图 / list_page=列表页。
    start_urls:
        起始 URL 数组。
    allowed_domains:
        允许采集域名数组（SSRF 防护）。
    blocked_paths:
        屏蔽路径数组。
    destination_knowledge_base_id:
        采集结果入库目标知识库 ID，可空（evidence_only 策略时为空）。
    extract_rules:
        正文提取规则（JSONB），如 CSS 选择器、字段映射。
    import_policy:
        入库策略：review_required=需审核（默认）/ auto_import=自动入库 /
        evidence_only=仅作证据。
    limits:
        采集限制（JSONB）：maxPagesPerRun / maxDepth / requestIntervalMs / concurrencyPerDomain。
    status:
        采集源状态：active / disabled。
    """

    __tablename__ = "crawl_sources"
    __table_args__ = (
        # 复合唯一约束：采集源 code 在项目内唯一
        UniqueConstraint(
            "project_id",
            "code",
            name="uq_crawl_sources_project_code",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="采集源 ID",
    )
    # 采集源编码：项目内唯一
    code: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="采集源编码（项目内唯一）"
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="采集源名称")
    # 采集类型：single_page / url_list / rss / sitemap / list_page
    type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="采集类型：single_page / url_list / rss / sitemap / list_page",
    )
    # 起始 URL 数组
    start_urls: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="起始 URL 数组"
    )
    # 允许采集域名数组（SSRF 防护）
    allowed_domains: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="允许采集域名数组"
    )
    # 屏蔽路径数组
    blocked_paths: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, comment="屏蔽路径数组"
    )
    # 入库目标知识库 ID：可空（evidence_only 策略时为空）
    destination_knowledge_base_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="入库目标知识库 ID"
    )
    # 正文提取规则
    extract_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="正文提取规则（JSONB）"
    )
    # 入库策略：review_required / auto_import / evidence_only
    import_policy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'review_required'"),
        comment="入库策略：review_required / auto_import / evidence_only",
    )
    # 采集限制
    limits: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="采集限制（JSONB）"
    )
    # 状态：active / disabled
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'active'"),
        comment="状态：active / disabled",
    )


class CrawlRun(Base, ProjectOwnedMixin, TimestampMixin):
    """采集运行记录表：记录每次采集执行。

    表名: crawl_runs

    字段说明
    --------
    crawl_source_id:
        关联采集源 ID（同项目）。
    status:
        运行状态：pending / running / success / failed。
    discovered_count:
        发现页面数。
    success_count:
        成功抓取数。
    duplicate_count:
        重复页面数（URL 去重）。
    failed_count:
        失败页面数。
    imported_count:
        入库数（审核通过或自动入库）。
    started_at / completed_at:
        运行开始与完成时间。
    error_code:
        失败错误码，可空。
    """

    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="采集运行 ID",
    )
    crawl_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联采集源 ID"
    )
    # 运行状态：pending / running / success / failed
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="运行状态：pending / running / success / failed",
    )
    # 发现页面数
    discovered_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="发现页面数",
    )
    # 成功抓取数
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="成功抓取数",
    )
    # 重复页面数
    duplicate_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="重复页面数",
    )
    # 失败页面数
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="失败页面数",
    )
    # 入库数
    imported_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="入库数",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="运行开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="运行完成时间"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="错误码"
    )


class CrawlPage(Base, ProjectOwnedMixin, TimestampMixin):
    """采集页面表：记录单个页面抓取结果。

    表名: crawl_pages

    复合唯一约束
    ------------
    ``uq_crawl_page_url (project_id, crawl_source_id, canonical_url_hash)``：
    保证同一采集源内 URL 去重（基于规范化 URL 的 SHA-256）。

    字段说明
    --------
    crawl_source_id / crawl_run_id:
        关联采集源与运行（同项目）。
    url / canonical_url / canonical_url_hash:
        原始 URL、规范化 URL、规范化 URL 哈希（去重键）。
    title / content_hash:
        页面标题与正文哈希（正文去重）。
    published_at / fetched_at:
        页面发布时间与抓取时间。
    http_status:
        HTTP 状态码。
    status:
        页面状态：discovered=已发现 / fetched=已抓取 / imported=已入库 /
        review=待审核 / failed=失败 / source_unavailable=源不可用。
    document_id:
        入库后关联的文档 ID，可空。
    error_code:
        失败错误码，可空。
    metadata_:
        页面元数据（JSONB）。
    """

    __tablename__ = "crawl_pages"
    __table_args__ = (
        # 复合唯一约束：同一采集源内 URL 去重
        UniqueConstraint(
            "project_id",
            "crawl_source_id",
            "canonical_url_hash",
            name="uq_crawl_page_url",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="采集页面 ID",
    )
    crawl_source_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联采集源 ID"
    )
    crawl_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="关联采集运行 ID"
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="原始 URL")
    # 规范化 URL：去除查询参数、统一协议等
    canonical_url: Mapped[str] = mapped_column(
        Text, nullable=False, comment="规范化 URL"
    )
    # 规范化 URL 哈希：去重键（SHA-256）
    canonical_url_hash: Mapped[str] = mapped_column(
        CHAR(64), nullable=False, comment="规范化 URL 哈希（去重键）"
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True, comment="页面标题")
    # 正文哈希：正文去重
    content_hash: Mapped[str | None] = mapped_column(
        CHAR(64), nullable=True, comment="正文哈希（SHA-256）"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="页面发布时间"
    )
    fetched_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="抓取时间"
    )
    # HTTP 状态码
    http_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="HTTP 状态码"
    )
    # 页面状态：discovered / fetched / imported / review / failed / source_unavailable
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'discovered'"),
        comment="页面状态：discovered / fetched / imported / review / failed / source_unavailable",
    )
    # 入库后关联的文档 ID
    document_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="入库后关联的文档 ID"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="错误码"
    )
    # 元数据
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, comment="元数据（JSONB）"
    )


class WebMaterial(Base, ProjectOwnedMixin, TimestampMixin):
    """网络待审核资料池表。

    表名: web_materials

    设计原因
    --------
    ``review_required`` 策略下，采集结果先入此表，人工审核通过后才入库到知识库。
    ``auto_import`` 策略下，采集结果直接入库，不经过此表。

    字段说明
    --------
    crawl_source_id / crawl_page_id:
        关联采集源与页面，可空（手动添加时为空）。
    title / content / source_url:
        资料标题、正文、来源 URL。
    status:
        审核状态：pending=待审核 / adopted=已采用 / rejected=已拒绝 / expired=已过期。
    knowledge_base_id:
        采用后入库的目标知识库 ID，可空。
    reviewed_at:
        审核时间，可空。
    metadata_:
        资料元数据（JSONB）。
    """

    __tablename__ = "web_materials"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="网络资料 ID",
    )
    crawl_source_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="关联采集源 ID"
    )
    crawl_page_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="关联采集页面 ID"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="资料标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="资料正文")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, comment="来源 URL")
    # 审核状态：pending / adopted / rejected / expired
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'pending'"),
        comment="审核状态：pending / adopted / rejected / expired",
    )
    # 采用后入库的目标知识库 ID
    knowledge_base_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, comment="采用后入库的目标知识库 ID"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, comment="审核时间"
    )
    # 元数据
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True, comment="元数据（JSONB）"
    )


class SourcePolicy(Base, ProjectOwnedMixin, TimestampMixin):
    """来源策略表：可信/禁用来源域名。

    表名: source_policies

    供采集与联网搜索共用，实现域名级白名单/黑名单。

    字段说明
    --------
    policy_type:
        策略类型：allow=允许 / block=禁用。
    domain:
        域名（如 example.com）。
    pattern:
        匹配模式（如路径正则），可空。
    """

    __tablename__ = "source_policies"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        comment="来源策略 ID",
    )
    # 策略类型：allow / block
    policy_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="策略类型：allow / block"
    )
    domain: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="域名"
    )
    # 匹配模式：可空
    pattern: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="匹配模式"
    )
