"""Repository 层：封装数据库访问，所有方法接收 ProjectContext 并带 project_id 过滤。

对应 Task 6 / SubTask 6.1：项目隔离强制过滤。

设计理念
--------
1. 所有业务 Repository 继承 ``BaseRepository``，通用方法在 WHERE 中强制带
   ``project_id == ctx.project_id``，从物理层杜绝跨项目数据访问。
2. 全局表（``Project`` / ``ToolDefinition``）不继承 ``BaseRepository``，单独实现，
   但仍由对应的 Repository 类封装访问逻辑。
3. Service 层通过依赖注入获取 ``AsyncSession``，构造 Repository 实例，
   再将 ``ProjectContext`` 透传给 Repository 方法。

模块分组
--------
- base.py: BaseRepository 基类（通用 CRUD 与 project_id 过滤）
- project.py: ProjectRepository / ApiKeyRepository / ProjectSettingsRepository
- knowledge.py: KnowledgeBaseRepository / DocumentRepository / DocumentChunkRepository
- prompt.py: PromptRepository
- tool.py: ToolDefinitionRepository / ProjectToolRepository
- ingestion.py: IngestionJobRepository
- research.py: ResearchTaskRepository / ResearchEvidenceRepository /
  ResearchResultRepository / RetrievalLogRepository
- schedule.py: ScheduleRepository / ScheduleRunRepository
- crawler.py: CrawlSourceRepository / CrawlRunRepository / CrawlPageRepository /
  WebMaterialRepository / SourcePolicyRepository
- audit.py: FeedbackRepository / UsageLogRepository

使用示例
--------
    from app.db.repositories import KnowledgeBaseRepository, DocumentRepository

    async def some_service(ctx: ProjectContext, db: AsyncSession):
        kb_repo = KnowledgeBaseRepository(db)
        doc_repo = DocumentRepository(db)
        # 所有查询自动带 project_id 过滤
        kb = await kb_repo.get_by_code(ctx, "ai-fund-kb")
        docs, total = await doc_repo.list(ctx, kb_id=kb.id)
"""
from __future__ import annotations

from app.db.repositories.audit import FeedbackRepository, UsageLogRepository
from app.db.repositories.base import BaseRepository
from app.db.repositories.crawler import (
    CrawlPageRepository,
    CrawlRunRepository,
    CrawlSourceRepository,
    SourcePolicyRepository,
    WebMaterialRepository,
)
from app.db.repositories.ingestion import IngestionJobRepository
from app.db.repositories.knowledge import (
    DocumentChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
)
from app.db.repositories.project import (
    ApiKeyRepository,
    ProjectRepository,
    ProjectSettingsRepository,
)
from app.db.repositories.prompt import PromptRepository
from app.db.repositories.research import (
    ResearchEvidenceRepository,
    ResearchResultRepository,
    ResearchTaskRepository,
    RetrievalLogRepository,
)
from app.db.repositories.schedule import ScheduleRepository, ScheduleRunRepository
from app.db.repositories.tool import ProjectToolRepository, ToolDefinitionRepository

__all__ = [
    # 基类
    "BaseRepository",
    # 项目
    "ProjectRepository",
    "ApiKeyRepository",
    "ProjectSettingsRepository",
    # 知识库
    "KnowledgeBaseRepository",
    "DocumentRepository",
    "DocumentChunkRepository",
    # 提示词
    "PromptRepository",
    # 工具
    "ToolDefinitionRepository",
    "ProjectToolRepository",
    # 入库
    "IngestionJobRepository",
    # 研究
    "ResearchTaskRepository",
    "ResearchEvidenceRepository",
    "ResearchResultRepository",
    "RetrievalLogRepository",
    # 调度
    "ScheduleRepository",
    "ScheduleRunRepository",
    # 爬虫
    "CrawlSourceRepository",
    "CrawlRunRepository",
    "CrawlPageRepository",
    "WebMaterialRepository",
    "SourcePolicyRepository",
    # 审计
    "FeedbackRepository",
    "UsageLogRepository",
]
