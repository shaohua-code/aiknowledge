"""SQLAlchemy 2 ORM 模型集合。

对应 Task 4：所有核心表的 ORM 模型定义。

导入此包即可获得所有模型类与 ``Base`` 元数据，供 Alembic 与业务代码使用：
    from app.db.models import Base, Project, KnowledgeBase, ...

模型分组
--------
- base.py: DeclarativeBase + 通用 Mixin（TimestampMixin / ProjectOwnedMixin）
- project.py: Project / ApiKey / ProjectSettings
- knowledge.py: KnowledgeBase / Document / DocumentChunk
- prompt.py: PromptVersion
- tool.py: ToolDefinition / ProjectTool
- ingestion.py: IngestionJob
- research.py: ResearchTask / ResearchEvidence / ResearchResult / RetrievalLog
- schedule.py: Schedule / ScheduleRun
- crawler.py: CrawlSource / CrawlRun / CrawlPage / WebMaterial / SourcePolicy
- audit.py: Feedback / UsageLog
"""
from __future__ import annotations

from app.db.models.base import Base, ProjectOwnedMixin, TimestampMixin
from app.db.models.project import ApiKey, Project, ProjectSettings
from app.db.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.db.models.prompt import PromptVersion
from app.db.models.tool import ProjectTool, ToolDefinition
from app.db.models.ingestion import IngestionJob
from app.db.models.research import (
    ResearchEvidence,
    ResearchResult,
    ResearchTask,
    RetrievalLog,
)
from app.db.models.schedule import Schedule, ScheduleRun
from app.db.models.crawler import (
    CrawlPage,
    CrawlRun,
    CrawlSource,
    SourcePolicy,
    WebMaterial,
)
from app.db.models.audit import Feedback, UsageLog

__all__ = [
    # 基础
    "Base",
    "ProjectOwnedMixin",
    "TimestampMixin",
    # 项目
    "Project",
    "ApiKey",
    "ProjectSettings",
    # 知识库
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    # 提示词
    "PromptVersion",
    # 工具
    "ToolDefinition",
    "ProjectTool",
    # 入库
    "IngestionJob",
    # 研究
    "ResearchTask",
    "ResearchEvidence",
    "ResearchResult",
    "RetrievalLog",
    # 调度
    "Schedule",
    "ScheduleRun",
    # 爬虫
    "CrawlSource",
    "CrawlRun",
    "CrawlPage",
    "WebMaterial",
    "SourcePolicy",
    # 审计
    "Feedback",
    "UsageLog",
]
