"""文档处理任务：解析 → 清洗 → 切割 → Embedding → 写入 document_chunks。

对应 SubTask 9.2：实现 ``process_document`` 完整入库流程。

任务签名说明
------------
``process_document`` 接受三个参数：
- ``project_id``：项目 ID，Worker 执行时再次校验归属，避免数据库状态变更后越权
- ``document_id``：待处理文档主键
- ``ingestion_job_id``：关联的入库任务 ID，用于更新 IngestionJob 状态机
  （pending → parsing → chunking → embedding → ready / failed）

整体流程
--------
1. **二次校验项目归属**：查询 Document 与 KnowledgeBase，确认都属于 project_id，
   防止调度器或任务参数被篡改后越权处理其他项目文档。
2. **解析阶段**（status=parsing）：按 source_type 分发：
   - file：从对象存储读取，按 mime_type 用 PyMuPDF / python-docx / 直接 decode
   - manual：从 Document.metadata_["content"] 读取（约定）
   - url：用 httpx 抓取网页，Trafilatura 提取正文
3. **清洗阶段**：去除多余空白、控制字符，统一换行符。
4. **切割阶段**（status=chunking）：调用 ``chunker.chunk_text`` 按段落策略切割。
5. **Embedding 阶段**（status=embedding）：调用 Embedding Provider 批量向量化。
6. **写入阶段**（status=ready）：``DocumentChunkRepository.bulk_create`` 批量入库。

错误处理与重试策略
------------------
- **网络类错误**（httpx.ConnectError / TimeoutException / Embedding 接口 5xx）：
  抛出 ``_RetryTaskError``，由同步入口 ``process_document`` 捕获后调用 ``self.retry()``，
  最多重试 3 次，间隔 60 秒。重试时任务从头执行（解析/切割/向量化均幂等）。
- **业务错误**（格式错误、内容为空、维度不匹配、项目归属不一致）：
  标记 IngestionJob.status=failed + error_code + error_message，不重试。
- **解析失败**：error_code='PARSE_FAILED'。
- **向量化失败**：error_code='EMBEDDING_FAILED'。
- **项目归属不一致**：error_code='PROJECT_SCOPE_MISMATCH'。
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from celery import Task

from app.core.project_context import ProjectContext
from app.db.repositories.ingestion import IngestionJobRepository
from app.db.repositories.knowledge import (
    DocumentChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
)
from app.db.session import AsyncSessionFactory
from app.modules.ingestion.chunker import chunk_text
from app.providers.embeddings import get_embedding_provider
from app.providers.object_storage import get_object_storage
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 错误码常量：统一标识失败原因，便于监控与降级判断
# ---------------------------------------------------------------------------
# 项目归属不一致：Document 或 KnowledgeBase 不属于传入的 project_id
ERROR_PROJECT_SCOPE_MISMATCH = "PROJECT_SCOPE_MISMATCH"
# 解析失败：文件格式错误、内容为空、URL 抓取失败等
ERROR_PARSE_FAILED = "PARSE_FAILED"
# 向量化失败：Embedding 接口不可用、维度不匹配等
ERROR_EMBEDDING_FAILED = "EMBEDDING_FAILED"
# 文档不存在：Document 查询返回 None（可能已被删除）
ERROR_DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"

# URL 抓取超时与大小限制
_URL_FETCH_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_URL_MAX_BYTES = 10 * 1024 * 1024  # 10MB，防止抓取超大页面拖垮 Worker


class _RetryTaskError(Exception):
    """可重试错误包装异常。

    异步逻辑 ``_process_document_async`` 捕获网络类错误后抛出本异常，
    同步入口 ``process_document`` 捕获后调用 ``self.retry(exc=...)`` 触发 Celery 重试。

    为什么需要包装？
        ``self.retry`` 是 Celery Task 同步方法，不能在 asyncio 事件循环内直接调用
        （会阻塞事件循环）。通过包装异常，将重试决策上抛到同步层处理。
    """


# ---------------------------------------------------------------------------
# 同步入口：Celery 任务
# ---------------------------------------------------------------------------
@celery_app.task(
    name="app.workers.ingestion_tasks.process_document",
    queue="ingestion",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def process_document(
    self: Task,
    project_id: str,
    document_id: str,
    ingestion_job_id: str | None = None,
) -> None:
    """文档处理主任务：解析 → 清洗 → 切割 → Embedding → 写入 document_chunks。

    使用 ``asyncio.run()`` 在 Celery 同步任务中运行异步逻辑：
        Celery Worker 是同步执行模型（基于 prefork/gevent），但数据库访问、
        httpx 请求、Embedding 调用都是异步 API。``asyncio.run()`` 创建临时事件循环
        执行异步逻辑，结束后关闭循环。每次任务调用创建独立循环，避免跨任务状态污染。

    Args:
        self: Celery Task 实例（``bind=True`` 注入），用于 ``self.retry()``。
        project_id: 项目 ID，Worker 执行时再次校验归属。
        document_id: 待处理文档主键。
        ingestion_job_id: 关联的入库任务 ID；可空（向后兼容），
            为空时不更新 IngestionJob 状态。

    重试策略：
        - 网络类错误（连接失败/超时/Embedding 5xx）：``self.retry()`` 最多 3 次，
          间隔 60 秒（``default_retry_delay``）。
        - 业务错误（格式/内容/归属）：标记 failed，不重试。
    """
    try:
        # 在独立事件循环中执行异步入库逻辑
        asyncio.run(
            _process_document_async(self, project_id, document_id, ingestion_job_id)
        )
    except _RetryTaskError as exc:
        # 可重试错误：交由 Celery 调度重试
        # exc=__cause__ 保留原始异常栈，便于排查
        logger.warning(
            "文档 %s 处理遇到可重试错误，准备重试（第 %d/%d 次）：%s",
            document_id,
            self.request.retries + 1,
            self.max_retries,
            exc.__cause__ or exc,
        )
        raise self.retry(exc=exc.__cause__ or exc)
    except Exception:
        # 其他未预期异常：记录日志，不再重试（业务错误已在异步层标记 failed）
        logger.exception("文档 %s 处理失败（不可重试）", document_id)


# ---------------------------------------------------------------------------
# 异步主逻辑
# ---------------------------------------------------------------------------
async def _process_document_async(
    task: Task,
    project_id: str,
    document_id: str,
    ingestion_job_id: str | None,
) -> None:
    """文档处理异步主逻辑：串联解析/清洗/切割/向量化/写入各阶段。

    本函数在 ``asyncio.run()`` 中执行，所有数据库与网络操作均为异步。
    任一阶段失败时通过 ``_mark_failed`` 标记 IngestionJob，网络错误抛
    ``_RetryTaskError`` 触发重试。

    状态机流转：pending → parsing → chunking → embedding → ready / failed
    每个阶段开始前更新 Document 与 IngestionJob 状态，提交事务保证可见性；
    阶段失败时标记 failed 并回滚未提交的中间状态。

    Args:
        task: Celery Task 实例，用于重试计数判断。
        project_id: 项目 ID。
        document_id: 文档 ID。
        ingestion_job_id: 入库任务 ID，可空（向后兼容）。

    Raises:
        _RetryTaskError: 网络类错误，需上层重试。
    """
    # 构造 Worker 场景的项目上下文：仅含 project_id，project_code 留空
    # Repository 仅依赖 ctx.project_id 做项目过滤，project_code 在 Worker 场景无意义
    ctx = _build_ctx(project_id)

    # AsyncSessionFactory 创建独立会话，任务结束自动关闭
    async with AsyncSessionFactory() as session:
        # 初始化各 Repository：共享同一会话，保证事务一致性
        doc_repo = DocumentRepository(session)
        chunk_repo = DocumentChunkRepository(session)
        job_repo = IngestionJobRepository(session)
        kb_repo = KnowledgeBaseRepository(session)

        try:
            # ----------------------------------------------------------
            # 阶段 1：二次校验项目归属
            # ----------------------------------------------------------
            # 为什么 Worker 要再次校验？防止调度器或任务参数被篡改后越权处理其他项目文档
            try:
                doc, kb = await _verify_project_scope(
                    ctx, doc_repo, kb_repo, project_id, document_id
                )
            except ValueError as exc:
                # 归属校验失败：业务错误，标记 failed 不重试
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_PROJECT_SCOPE_MISMATCH,
                    str(exc),
                )
                # 同时标记 Document failed（若文档存在）
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                logger.warning("文档 %s 二次校验失败：%s", document_id, exc)
                return

            # ----------------------------------------------------------
            # 阶段 2：解析文档（status=parsing）
            # ----------------------------------------------------------
            await _set_status(
                ctx, doc_repo, job_repo, document_id, ingestion_job_id, "parsing"
            )
            await session.commit()

            try:
                parsed_blocks = await _parse_document(ctx, doc)
            except _RetryTaskError:
                # URL 抓取网络错误：向上抛出触发重试
                raise
            except ValueError as exc:
                # 解析失败：业务错误，标记 failed 不重试
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_PARSE_FAILED,
                    str(exc),
                )
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                return

            # 解析结果为空：业务错误，标记 failed 不重试
            if not parsed_blocks:
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_PARSE_FAILED,
                    "解析后内容为空",
                )
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                return

            # ----------------------------------------------------------
            # 阶段 3：清洗文本（去除多余空白、控制字符、统一换行）
            # ----------------------------------------------------------
            cleaned_blocks = [_clean_block(b) for b in parsed_blocks]
            if not any(b["text"].strip() for b in cleaned_blocks):
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_PARSE_FAILED,
                    "清洗后内容为空",
                )
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                return

            # ----------------------------------------------------------
            # 阶段 4：切割（status=chunking）
            # ----------------------------------------------------------
            await _set_status(
                ctx, doc_repo, job_repo, document_id, ingestion_job_id, "chunking"
            )
            await session.commit()

            chunk_dicts = _build_chunks(cleaned_blocks)
            if not chunk_dicts:
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_PARSE_FAILED,
                    "切割后无有效分块",
                )
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                return

            # ----------------------------------------------------------
            # 阶段 5：Embedding（status=embedding）
            # ----------------------------------------------------------
            await _set_status(
                ctx, doc_repo, job_repo, document_id, ingestion_job_id, "embedding"
            )
            await session.commit()

            try:
                embeddings = await _embed_chunks(chunk_dicts)
            except _RetryTaskError:
                # 网络错误：向上抛出触发重试，不在此处标记 failed
                # 重试时任务从头执行，状态会重新流转
                raise
            except Exception as exc:
                # Embedding 业务错误（维度不匹配/4xx）：标记 failed 不重试
                await _mark_failed(
                    ctx,
                    job_repo,
                    ingestion_job_id,
                    ERROR_EMBEDDING_FAILED,
                    str(exc),
                )
                await doc_repo.set_processing_status(ctx, document_id, "failed")
                await session.commit()
                return

            # ----------------------------------------------------------
            # 阶段 6：写入 chunks（status=ready）
            # ----------------------------------------------------------
            # 组装最终 chunk 字段：注入 embedding 向量
            # zip strict=True 保证向量数与 chunk 数一致，否则抛错
            for chunk_dict, embedding in zip(chunk_dicts, embeddings, strict=True):
                chunk_dict["embedding"] = embedding

            # 批量写入 document_chunks
            await chunk_repo.bulk_create(ctx, chunk_dicts)

            # 更新 Document 与 IngestionJob 为 ready
            await doc_repo.set_processing_status(ctx, document_id, "ready")
            if ingestion_job_id is not None:
                await job_repo.update_status(
                    ctx,
                    ingestion_job_id,
                    "ready",
                    stage="done",
                    completed_at=datetime.now(timezone.utc),
                )

            # 提交事务：chunks + Document.status + IngestionJob.status 一起落库
            await session.commit()

            logger.info(
                "文档 %s 处理完成，写入 %d 个分块", document_id, len(chunk_dicts)
            )

        except _RetryTaskError:
            # 网络错误：回滚当前会话未提交的中间状态，向上抛出触发重试
            await session.rollback()
            raise
        except Exception:
            # 未预期异常：回滚并记录，避免脏数据残留
            await session.rollback()
            logger.exception("文档 %s 处理遇到未预期异常", document_id)
            # 尝试标记 failed（新会话，避免当前会话已损坏）
            await _safe_mark_failed(ctx, job_repo, ingestion_job_id, document_id)
            raise


# ---------------------------------------------------------------------------
# 阶段实现：二次校验
# ---------------------------------------------------------------------------
async def _verify_project_scope(
    ctx: ProjectContext,
    doc_repo: DocumentRepository,
    kb_repo: KnowledgeBaseRepository,
    project_id: str,
    document_id: str,
) -> tuple[Any, Any]:
    """二次校验项目归属：Document 与关联 KnowledgeBase 必须都属于 project_id。

    为什么 Worker 要再次校验？
        API 层在 Task 8 已校验过项目归属，但任务从入队到执行可能间隔数秒甚至数分钟，
        期间数据库状态可能变化：
        1. **调度器或任务参数被篡改**：恶意或误操作修改了 Celery 任务参数，
           传入其他项目的 document_id，Worker 必须再次校验防止越权。
        2. **文档/知识库被删除或迁移**：API 层校验通过后，文档可能被其他请求删除，
           或知识库被迁移到其他项目（虽然当前不支持迁移，但防御性校验仍有必要）。
        3. **复合外键一致性**：Document.knowledge_base_id 必须指向同项目的 KnowledgeBase，
           即使数据库层有复合外键约束，Worker 仍校验以提供清晰的错误码而非数据库异常。

    Args:
        ctx: 项目上下文。
        doc_repo: 文档 Repository。
        kb_repo: 知识库 Repository。
        project_id: 期望的项目 ID。
        document_id: 文档 ID。

    Returns:
        元组 ``(document, knowledge_base)``，校验通过的文档与知识库实例。

    Raises:
        _RetryTaskError: 不触发（归属错误是业务错误，不重试）。
        ValueError: 校验失败（文档不存在/归属不一致），调用方标记 failed。
    """
    # 查询 Document：Repository 已带 project_id 过滤，不存在或属于其他项目返回 None
    doc = await doc_repo.get_by_id(ctx, document_id)
    if doc is None:
        # 文档不存在或属于其他项目：标记 failed，错误码 DOCUMENT_NOT_FOUND
        logger.warning(
            "二次校验失败：文档 %s 在项目 %s 下不存在", document_id, project_id
        )
        raise ValueError(
            f"文档 {document_id} 不存在或不属于项目 {project_id}"
        )

    # 查询关联的 KnowledgeBase：再次带 project_id 过滤，校验复合外键一致性
    # 即使 Document 查询通过（project_id 匹配），仍需校验 knowledge_base_id 指向的
    # 知识库属于同一项目，防止数据不一致
    kb = await kb_repo.get_by_id(ctx, doc.knowledge_base_id)
    if kb is None:
        # 知识库不存在或不属于当前项目：复合外键不一致
        logger.warning(
            "二次校验失败：文档 %s 的知识库 %s 不属于项目 %s",
            document_id,
            doc.knowledge_base_id,
            project_id,
        )
        raise ValueError(
            f"文档 {document_id} 关联的知识库 {doc.knowledge_base_id} "
            f"不属于项目 {project_id}"
        )

    return doc, kb


# ---------------------------------------------------------------------------
# 阶段实现：解析
# ---------------------------------------------------------------------------
async def _parse_document(ctx: ProjectContext, doc: Any) -> list[dict[str, Any]]:
    """解析文档：按 source_type 分发到具体解析器。

    返回结构
    --------
    ``list[dict]``，每项含：
    - ``text``：解析后的文本片段
    - ``page_number``：页码（PDF 使用，其他类型为 None）
    - ``section``：章节（暂未实现结构化提取，固定 None）

    Args:
        ctx: 项目上下文。
        doc: Document 实例。

    Returns:
        解析后的文本块列表。

    Raises:
        ValueError: 解析失败（格式错误/内容为空/手动内容缺失）。
        _RetryTaskError: URL 抓取网络错误，需重试。
    """
    source_type = doc.source_type

    if source_type == "file":
        # 文件类型：从对象存储读取，按 mime_type 分发
        return await _parse_file(ctx, doc)
    if source_type == "manual":
        # 手动录入：从 metadata_["content"] 读取
        # 约定：Task 8 API 层应将 manual 的原始文本存入 metadata_["content"]
        # 当前 API 实现未存储，此处获取不到时标记 PARSE_FAILED
        return _parse_manual(doc)
    if source_type == "url":
        # URL 类型：httpx 抓取 + Trafilatura 提取正文
        return await _parse_url(doc)

    # 未知 source_type：业务错误，不重试
    raise ValueError(f"不支持的 source_type：{source_type}")


async def _parse_file(ctx: ProjectContext, doc: Any) -> list[dict[str, Any]]:
    """解析上传文件：从对象存储读取后按 mime_type 分发。

    Args:
        ctx: 项目上下文。
        doc: Document 实例，需含 ``storage_key`` 与 ``mime_type``。

    Returns:
        解析后的文本块列表。

    Raises:
        ValueError: storage_key 缺失、格式不支持、解析失败。
    """
    if not doc.storage_key:
        raise ValueError("文件类型文档缺少 storage_key")

    # 从对象存储读取文件字节（同步 API，在异步函数中直接调用）
    # LocalStorageClient.read 是本地文件 IO，阻塞时间短，无需包装为异步
    storage = get_object_storage()
    content = storage.read(doc.storage_key)

    mime_type = (doc.mime_type or "").lower()

    # 按 MIME 类型分发到对应解析器
    if mime_type == "application/pdf" or doc.storage_key.lower().endswith(".pdf"):
        return _parse_pdf(content)
    if (
        mime_type
        in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        )
        or doc.storage_key.lower().endswith((".docx", ".doc"))
    ):
        return _parse_docx(content)
    if mime_type.startswith("text/") or doc.storage_key.lower().endswith(
        (".txt", ".md", ".markdown", ".csv", ".json", ".log")
    ):
        # 文本类：尝试 UTF-8 解码，失败回退到 latin-1（避免乱码直接报错）
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")
        return [{"text": text, "page_number": None, "section": None}]

    # 不支持的格式：业务错误，不重试
    raise ValueError(f"不支持的文件类型：mime_type={mime_type}")


def _parse_pdf(content: bytes) -> list[dict[str, Any]]:
    """解析 PDF：用 PyMuPDF（fitz）逐页提取文本，保留页码。

    为什么用 PyMuPDF？
        - 速度快（C 实现），无需外部依赖（如 poppler）
        - 保留页码信息，便于检索结果定位原文页码
        - 对中文 PDF 支持较好

    Args:
        content: PDF 文件字节数据。

    Returns:
        文本块列表，每页一个块，含 ``page_number``。

    Raises:
        ValueError: PDF 解析失败或无文本。
    """
    import fitz  # PyMuPDF

    try:
        # 从内存字节流打开 PDF，避免写临时文件
        pdf_doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF 打开失败：{exc}") from exc

    blocks: list[dict[str, Any]] = []
    try:
        # 逐页提取文本：page_num 从 1 开始（符合阅读习惯）
        for page_idx in range(len(pdf_doc)):
            page = pdf_doc[page_idx]
            text = page.get_text("text")  # 提取纯文本，保留段落换行
            if text and text.strip():
                blocks.append(
                    {
                        "text": text,
                        "page_number": page_idx + 1,  # 页码从 1 开始
                        "section": None,
                    }
                )
    finally:
        # 显式关闭 PDF 文档，释放资源
        pdf_doc.close()

    if not blocks:
        raise ValueError("PDF 解析后无文本（可能是扫描件，需 OCR）")

    return blocks


def _parse_docx(content: bytes) -> list[dict[str, Any]]:
    """解析 DOCX：用 python-docx 逐段落提取文本。

    Args:
        content: DOCX 文件字节数据。

    Returns:
        文本块列表，DOCX 无页码概念，``page_number`` 固定 None。

    Raises:
        ValueError: DOCX 解析失败或无文本。
    """
    import docx

    try:
        # python-docx 接受类文件对象，用 BytesIO 包装字节数据
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"DOCX 打开失败：{exc}") from exc

    # 逐段落提取文本，过滤空段落
    paragraphs = [p.text for p in document.paragraphs if p.text and p.text.strip()]

    if not paragraphs:
        raise ValueError("DOCX 解析后无文本")

    # DOCX 无页码概念，将所有段落合并为一个块（切割阶段会按段落再分）
    return [
        {
            "text": "\n\n".join(paragraphs),  # 双换行分隔，便于切割器识别段落边界
            "page_number": None,
            "section": None,
        }
    ]


def _parse_manual(doc: Any) -> list[dict[str, Any]]:
    """解析手动录入文档：从 ``metadata_["content"]`` 读取原始文本。

    约定：Task 8 API 层将 manual 类型的原始文本存入 ``Document.metadata_["content"]``。
    当前 API 实现未存储此字段，此处获取不到时抛 ValueError，
    由调用方标记 PARSE_FAILED。

    Args:
        doc: Document 实例。

    Returns:
        文本块列表，单元素。

    Raises:
        ValueError: metadata 中缺少 content 字段。
    """
    # metadata_ 可能是 None，做防御性处理
    metadata = doc.metadata_ or {}
    content = metadata.get("content")

    if not content or not str(content).strip():
        # 手动内容缺失：业务错误，不重试
        raise ValueError(
            "手动录入文档缺少 content（应存储在 metadata_.content）"
        )

    return [{"text": str(content), "page_number": None, "section": None}]


async def _parse_url(doc: Any) -> list[dict[str, Any]]:
    """解析 URL 文档：httpx 抓取网页 + Trafilatura 提取正文。

    流程：
        1. 用 httpx GET 抓取网页 HTML（限制最大 10MB，超时 20s）
        2. 用 Trafilatura 从 HTML 提取正文（去除导航/广告/脚本等噪音）
        3. 返回单元素文本块

    Args:
        doc: Document 实例，需含 ``source_url``。

    Returns:
        文本块列表，单元素。

    Raises:
        ValueError: URL 缺失、抓取失败（4xx/内容过大）、正文为空。
        _RetryTaskError: 网络错误（连接失败/超时），需重试。
    """
    if not doc.source_url:
        raise ValueError("URL 类型文档缺少 source_url")

    # 抓取网页：网络错误包装为 _RetryTaskError 触发重试
    try:
        async with httpx.AsyncClient(timeout=_URL_FETCH_TIMEOUT) as client:
            response = await client.get(
                doc.source_url,
                follow_redirects=True,  # 跟随重定向
                headers={"User-Agent": "KnowledgeHub/1.0 (+ingestion)"},  # 标识身份
            )
            response.raise_for_status()
    except httpx.ConnectError as exc:
        # 连接失败：可重试
        raise _RetryTaskError("URL 抓取连接失败") from exc
    except httpx.TimeoutException as exc:
        # 超时：可重试
        raise _RetryTaskError("URL 抓取超时") from exc
    except httpx.HTTPStatusError as exc:
        # HTTP 4xx/5xx：业务错误（页面不存在/服务器错误），不重试
        # 注意：5xx 理论上可重试，但网页抓取场景下重试意义不大，直接失败
        raise ValueError(
            f"URL 抓取返回 {exc.response.status_code}：{exc.response.text[:200]}"
        ) from exc

    # 大小校验：防止抓取超大页面拖垮 Worker
    if len(response.content) > _URL_MAX_BYTES:
        raise ValueError(
            f"URL 内容过大：{len(response.content)} 字节，超过限制 {_URL_MAX_BYTES}"
        )

    html = response.text

    # Trafilatura 提取正文：自动去除导航/广告/脚本/样式等噪音
    import trafilatura

    extracted = trafilatura.extract(
        html,
        include_comments=False,  # 不提取评论
        include_tables=True,  # 提取表格内容
        favor_precision=True,  # 偏好精度，减少噪音
    )

    if not extracted or not extracted.strip():
        raise ValueError("URL 正文提取为空（可能是纯 JS 渲染页面）")

    return [{"text": extracted, "page_number": None, "section": None}]


# ---------------------------------------------------------------------------
# 阶段实现：清洗
# ---------------------------------------------------------------------------
def _clean_block(block: dict[str, Any]) -> dict[str, Any]:
    """清洗单个文本块：去除多余空白与控制字符，统一换行符。

    清洗规则
    --------
    1. 统一换行符：``\\r\\n`` / ``\\r`` → ``\\n``，避免 Windows/Mac 换行混用。
    2. 去除控制字符：保留 ``\\n`` / ``\\t``，去除其他 ASCII 控制字符（如 ``\\x00``）。
    3. 压缩多余空白：
       - 行尾空白（含全角空格 ``　``）去除
       - 连续 3 个以上换行压缩为 2 个（段落分隔符 ``\\n\\n``）
       - 行内连续空格压缩为单个空格（保留缩进结构）
    4. 去除首尾空白。

    Args:
        block: 原始文本块，含 ``text`` / ``page_number`` / ``section``。

    Returns:
        清洗后的文本块，结构相同。
    """
    text = block["text"]

    # 1. 统一换行符：Windows \r\n 与旧 Mac \r 统一为 \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. 去除控制字符：保留 \n \t，去除其他 ASCII 控制字符（0x00-0x08, 0x0B-0x1F, 0x7F）
    # \v (0x0B) 与 \f (0x0C) 也去除，避免影响排版
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)

    # 3. 行尾空白去除（含全角空格　与普通空格）
    # 逐行处理：按 \n 分割，每行 rstrip，再拼接
    lines = [line.rstrip(" 　\t") for line in text.split("\n")]
    text = "\n".join(lines)

    # 4. 压缩连续 3+ 换行为 2 个（段落分隔标准为 \n\n）
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 5. 行内连续空格压缩为单个（不处理行首缩进，保留代码块结构）
    # 仅对每行内部压缩，避免跨行误操作
    lines = [re.sub(r"[ \t]{2,}", " ", line) for line in text.split("\n")]
    text = "\n".join(lines)

    # 6. 去除首尾空白
    text = text.strip()

    return {
        "text": text,
        "page_number": block.get("page_number"),
        "section": block.get("section"),
    }


# ---------------------------------------------------------------------------
# 阶段实现：切割
# ---------------------------------------------------------------------------
def _build_chunks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将清洗后的文本块列表切割为 chunk 字典列表。

    流程：
        1. 对每个文本块调用 ``chunk_text`` 切割
        2. 注入 ``page_number`` / ``section``（来自原文本块）
        3. 分配全局 ``chunk_index``（跨文本块连续递增）
        4. 保留 ``token_count``（由 chunker 估算）

    Args:
        blocks: 清洗后的文本块列表。

    Returns:
        chunk 字典列表，每项含 ``content`` / ``chunk_index`` / ``page_number``
        / ``section`` / ``token_count``，待 Embedding 后注入 ``embedding``。
    """
    chunk_dicts: list[dict[str, Any]] = []
    global_index = 0  # 跨文本块的全局 chunk 序号

    for block in blocks:
        text = block["text"]
        if not text.strip():
            continue

        # 调用切割器：返回 [{"content", "page_number", "section", "token_count"}]
        sub_chunks = chunk_text(text, target_length=600, overlap=100)

        for sub in sub_chunks:
            chunk_dicts.append(
                {
                    "content": sub["content"],
                    "chunk_index": global_index,
                    # 注入原文本块的页码（PDF 多页场景）
                    "page_number": block.get("page_number"),
                    # 组装 metadata：含 section 层级与 token_count
                    "metadata_": {
                        "section": block.get("section"),
                        "token_count": sub["token_count"],
                    },
                    "token_count": sub["token_count"],
                }
            )
            global_index += 1

    return chunk_dicts


# ---------------------------------------------------------------------------
# 阶段实现：Embedding
# ---------------------------------------------------------------------------
async def _embed_chunks(chunk_dicts: list[dict[str, Any]]) -> list[list[float]]:
    """对 chunk 内容批量生成 Embedding 向量。

    流程：
        1. 通过工厂获取 Embedding Provider 单例
        2. 提取所有 chunk 的 content 列表
        3. 调用 ``embed_texts`` 批量向量化（Provider 内部按 batch_size 分批）
        4. 返回向量列表，顺序与 chunk_dicts 一致

    错误处理：
        - Provider 内部网络错误（ConnectError/Timeout/5xx）：抛 ``_RetryTaskError``
          交由上层重试。
        - 维度不匹配/4xx：抛普通异常，上层标记 failed 不重试。

    Args:
        chunk_dicts: chunk 字典列表，读取 ``content`` 字段向量化。

    Returns:
        向量列表，与 ``chunk_dicts`` 等长且顺序一致。

    Raises:
        _RetryTaskError: 网络错误，需重试。
        Exception: 业务错误（维度不匹配等），不重试。
    """
    # 提取待向量化的文本列表
    texts = [c["content"] for c in chunk_dicts]

    # 获取 Provider 单例：lru_cache 缓存，httpx 客户端复用
    provider = get_embedding_provider()

    try:
        # 批量调用：Provider 内部按 BATCH_SIZE 分批，含重试逻辑
        embeddings = await provider.embed_texts(texts)
    except httpx.ConnectError as exc:
        # 连接失败：可重试（Provider 内部已重试 2 次仍失败，上抛触发 Celery 重试）
        raise _RetryTaskError("Embedding 接口连接失败") from exc
    except httpx.TimeoutException as exc:
        # 超时：可重试
        raise _RetryTaskError("Embedding 接口超时") from exc
    except RuntimeError as exc:
        # Provider 抛 RuntimeError：可能是重试耗尽的网络错误，也可能是 4xx 业务错误
        # 判断错误信息：含"不可重试"则直接抛，否则视为可重试
        msg = str(exc)
        if "不可重试" in msg:
            raise  # 业务错误，上层标记 failed
        # 重试耗尽的网络错误：上抛触发 Celery 重试
        raise _RetryTaskError(msg) from exc

    # 长度校验：返回向量数必须与输入一致
    if len(embeddings) != len(chunk_dicts):
        raise RuntimeError(
            f"Embedding 返回数量 {len(embeddings)} 与 chunk 数 {len(chunk_dicts)} 不一致"
        )

    return embeddings


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _build_ctx(project_id: str) -> ProjectContext:
    """构造 Worker 场景的项目上下文。

    Worker 无 API 鉴权链路，手动构造只含 ``project_id`` 的 ProjectContext。
    ``project_code`` 留空（Repository 仅依赖 project_id 做项目过滤）。

    Args:
        project_id: 项目 ID。

    Returns:
        ProjectContext 实例。
    """
    return ProjectContext(
        project_id=project_id,
        project_code="",  # Worker 场景无 project_code，Repository 不依赖此字段
        environment="worker",
    )


async def _set_status(
    ctx: ProjectContext,
    doc_repo: DocumentRepository,
    job_repo: IngestionJobRepository,
    document_id: str,
    ingestion_job_id: str | None,
    status: str,
) -> None:
    """更新 Document 与 IngestionJob 的处理状态（状态机流转）。

    状态机：pending → parsing → chunking → embedding → ready / failed

    为什么每个阶段都要更新状态？
        1. **可观测性**：前端轮询 IngestionJob.status 展示进度条，用户感知处理阶段。
        2. **故障定位**：Worker 崩溃后，最后的状态表明卡在哪个阶段，便于排查。
        3. **重试判断**：重试时根据当前状态决定是否从头开始或断点续传（当前实现从头开始）。

    Args:
        ctx: 项目上下文。
        doc_repo: 文档 Repository。
        job_repo: 入库任务 Repository。
        document_id: 文档 ID。
        ingestion_job_id: 入库任务 ID，可空（为空时仅更新 Document 状态）。
        status: 目标状态（parsing / chunking / embedding / ready）。
    """
    # 更新 Document.processing_status
    await doc_repo.set_processing_status(ctx, document_id, status)

    # ingestion_job_id 为空时跳过 IngestionJob 更新（向后兼容）
    if ingestion_job_id is None:
        return

    # 更新 IngestionJob：status + stage + started_at（首次进入处理阶段时记录）
    extra: dict[str, Any] = {"stage": status}
    if status == "parsing":
        # 首次进入 parsing 阶段记录开始时间
        extra["started_at"] = datetime.now(timezone.utc)

    await job_repo.update_status(ctx, ingestion_job_id, status, **extra)


async def _mark_failed(
    ctx: ProjectContext,
    job_repo: IngestionJobRepository,
    ingestion_job_id: str | None,
    error_code: str,
    error_message: str,
) -> None:
    """标记入库任务为 failed，记录错误码与错误信息。

    Args:
        ctx: 项目上下文。
        job_repo: 入库任务 Repository。
        ingestion_job_id: 入库任务 ID，可空（为空时仅记录日志）。
        error_code: 错误码（如 PARSE_FAILED / EMBEDDING_FAILED）。
        error_message: 错误详情。
    """
    logger.warning(
        "入库任务 %s 标记失败：error_code=%s, message=%s",
        ingestion_job_id,
        error_code,
        error_message,
    )

    if ingestion_job_id is None:
        # 向后兼容：无 ingestion_job_id 时仅记录日志
        return

    # 更新 IngestionJob：status=failed + error_code + error_message + completed_at
    await job_repo.update_status(
        ctx,
        ingestion_job_id,
        "failed",
        error_code=error_code,
        error_message=error_message,
        completed_at=datetime.now(timezone.utc),
    )


async def _safe_mark_failed(
    ctx: ProjectContext,
    job_repo: IngestionJobRepository,
    ingestion_job_id: str | None,
    document_id: str,
) -> None:
    """安全标记失败：用于异常处理路径，避免标记失败本身抛异常掩盖原始错误。

    在新会话中尝试标记 failed，若仍失败则仅记录日志。

    Args:
        ctx: 项目上下文。
        job_repo: 原会话的 Repository（可能已损坏，本函数新建会话）。
        ingestion_job_id: 入库任务 ID。
        document_id: 文档 ID（用于日志）。
    """
    try:
        # 新建独立会话：原会话可能因异常处于损坏状态
        async with AsyncSessionFactory() as session:
            safe_job_repo = IngestionJobRepository(session)
            safe_doc_repo = DocumentRepository(session)
            # 标记 IngestionJob failed
            if ingestion_job_id is not None:
                await safe_job_repo.update_status(
                    ctx,
                    ingestion_job_id,
                    "failed",
                    error_code="UNEXPECTED_ERROR",
                    error_message="处理过程中遇到未预期异常",
                    completed_at=datetime.now(timezone.utc),
                )
            # 标记 Document failed
            await safe_doc_repo.set_processing_status(ctx, document_id, "failed")
            await session.commit()
    except Exception:
        # 标记失败本身失败：仅记录日志，不掩盖原始异常
        logger.exception(
            "文档 %s 标记 failed 时再次失败，可能需人工介入", document_id
        )
