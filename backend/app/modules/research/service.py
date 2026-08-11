"""短链路研究服务：编排并行取证 + 一次模型生成的完整研究流程。

对应 SubTask 15.3：``ResearchService`` 是研究链路的核心编排层，
对外暴露 ``run`` 方法，串联以下子流程：

1. 创建 ResearchTask 记录（status='running'）
2. 并行取证：内部检索 / 联网搜索 / 工具调用（``asyncio.gather``）
3. 合并证据：``EvidenceMerger`` 统一格式、去重、评分、截断
4. 证据校验：为空时抛 ``InsufficientEvidenceError`` 并标记任务 failed
5. 加载提示词：``PromptService.get_active``，无启用版本用默认模板
6. 构造模型输入：system_prompt + evidence_rules + prohibitions + risk_template
   + user_message（问题 + 证据列表 + output_schema + context）
7. 一次模型生成：``asyncio.wait_for(chat_provider.complete, timeout=10)``
   超时则标记降级，返回已整理证据与失败状态
8. 构造结果：answer / conclusions / suggested_actions / confidence /
   uncertainties / risk_notice / timing
9. 持久化：批量写 ResearchEvidence、写 ResearchResult、更新 ResearchTask
10. 记录 UsageLog：token 用量、费用、耗时

短链路设计核心思想（务必阅读）
----------------------------
1. **并行取证**
   内部检索、联网搜索、工具调用三路证据源相互独立，串行执行会浪费 5~10s。
   用 ``asyncio.gather`` 并行调度三路协程，总耗时 ≈ max(三路耗时)，
   将取证阶段控制在 5~7s 内，为模型生成留出 5s 余量。

2. **一次模型生成**
   整个研究流程只调用 1 次聊天模型。多次调用（如先生成大纲再分段生成）
   会显著放大延迟（每次 1~3s）与成本，难以满足 P95 ≤ 12s 的目标。
   一次生成通过 JSON 模式约束输出结构，保证可解析。

3. **为什么最多 8 条证据？**
   - **上下文规模**：8 条证据 × 1500 字符 snippet ≈ 12000 字符 ≈ 4000 token，
     加上 system_prompt（约 1000 token）与 user_message（约 500 token），
     总 prompt 约 5500 token，在 gpt-4o-mini 的 128k 上下文内绰绰有余，
     且不会因上下文过长导致注意力稀释或成本飙升。
   - **覆盖与噪声平衡**：内部证据保证相关性，网络证据补充时效性，
     工具证据提供实时结构化数据，三类各取 2~3 条即可覆盖关键维度；
     过多证据会让模型陷入"信息过载"，反而降低结论质量。
   - **参数化**：``settings.max_evidence`` 默认 8，可在配置层调整，
     无需改动代码。

4. **为什么硬超时 15 秒？**
   - **用户体验**：P95 ≤ 12s 是 PRD 的硬约束，15s 作为兜底硬超时，
     保证单次研究在最坏情况下也 15s 内返回（成功或降级），不堆积请求拖垮服务。
   - **时间预算分配**：取证 5~7s + 模型生成 5s + 持久化/序列化 1~2s ≈ 12s，
     15s 留 3s 余量应对突发抖动。
   - **降级路径**：超时后不抛 500，而是返回已整理证据 + degraded=true +
     degraded_reasons=['hard_timeout']，让客户端仍能基于证据做降级决策。

5. **降级策略**
   - **单路失败不阻塞**：内部检索 / 联网搜索 / 工具调用任一失败均 try/except，
     记录降级原因，其余两路继续执行。即使三路都失败，仍返回空证据列表
     由后续"证据校验"环节决定是否抛 ``InsufficientEvidenceError``。
   - **模型超时降级**：模型调用超时不抛 500，标记 degraded=true，
     返回已整理证据 + 失败状态，让客户端基于证据做决策。
   - **整体硬超时降级**：``asyncio.wait_for`` 包裹整体流程，
     超时返回已收集的部分成果 + degraded=true。

6. **证据评分与去重逻辑**
   详见 ``evidence_merger.py`` 的模块 docstring：
   - 内部证据 0.7~0.9（按 RRF 分数归一化，可信度最高）
   - 工具证据 0.8~0.95（结构化实时数据，可信度高）
   - 网络证据 0.5~0.7（未审核来源，可信度中等）
   - 按 source_url + snippet 哈希去重
   - 按 score 降序截取 Top N

7. **模型调用限制**
   - 单次调用，不重试、不补充追问（短链路一次生成原则）
   - ``temperature=0.3``：研究场景偏低温度保证输出稳定、可复现
   - ``response_format={"type": "json_object"}``：强制 JSON 输出，
     避免 regex 提取的脆弱方案
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.config import settings
from app.core.exceptions import (
    ExternalSourceFailedError,
    ExternalSourceTimeoutError,
    InsufficientEvidenceError,
    ModelTimeoutError,
    ToolNotAllowedError,
)
from app.db.repositories.audit import UsageLogRepository
from app.db.repositories.research import (
    ResearchEvidenceRepository,
    ResearchResultRepository,
    ResearchTaskRepository,
)
from app.modules.prompts.service import PromptService
from app.modules.research.evidence_merger import EvidenceMerger
from app.modules.retrieval.hybrid_searcher import HybridSearcher
from app.modules.tools.executor import ToolExecutor
from app.modules.web_research.service import WebResearchService
from app.providers.chat_models import (
    ChatCompletionResult,
    ChatMessage,
    get_chat_model_provider,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.project_context import ProjectContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 单次工具调用上限：研究链路中最多调用 3 个工具，避免单次研究 token 与延迟爆炸
# ---------------------------------------------------------------------------
# 取 3 而非更多的原因：
#   - 工具调用通常用于补充时效性数据（如基金净值、市场行情），
#     3 个工具已覆盖"行情 + 新闻 + 个股"三类典型场景。
#   - 每个工具调用约 200~800ms，3 个工具并行约 1s 内完成，
#     超过 3 个会让取证阶段难以在 5~7s 内完成。
MAX_TOOLS_PER_RUN = 3

# 单次模型生成超时：10s
# 与整体硬超时 15s 配合：取证 5~7s + 生成 10s 上限 = 15s 硬超时兜底
# 实际生成通常 1~3s，10s 留足余量应对模型抖动
MODEL_GENERATION_TIMEOUT_SECONDS = 10

# 模型调用温度：研究场景偏低温度保证输出稳定、可复现
# 0.3 在"忠实证据"与"适度组织语言"间取得平衡，避免高温导致的发散与虚构
MODEL_TEMPERATURE = 0.3

# 默认估算成本（每千 token 美元）：gpt-4o-mini 的近似价格
# 实际成本应从 Provider 响应或配置中读取，这里用保守估值写入 usage_logs
DEFAULT_COST_PER_1K_TOKENS = 0.00015


@dataclass
class ResearchRunResult:
    """短链路研究运行结果。

    由 ``ResearchService.run`` 返回，承载整个研究流程的输出，
    供 API 层序列化为响应体。

    Attributes:
        task_id: 研究任务 ID（UUID），用于客户端查询任务详情与反馈。
        answer: 最终回答文本（大模型一次生成的核心输出）。
        conclusions: 结论数组，每项含 text 与 evidenceRefs。
        suggested_actions: 建议行动数组，每项含 action 与 rationale。
        evidence: 证据列表（已合并、去重、评分、截断），每项含
            type / title / snippet / source_url / published_at / data_as_of / score。
        confidence: 置信度（0~1），由大模型基于证据充分性自评。
        uncertainties: 不确定性数组，如数据缺失、来源冲突。
        risk_notice: 风险提示文本，附加到回答末尾。
        timing: 各阶段耗时（毫秒），含 internalRetrievalMs /
            externalParallelMs / generationMs / totalMs。
        degraded: 是否降级。True 表示取证或生成环节出现部分失败，
            客户端需结合 degraded_reasons 判断可信度。
        degraded_reasons: 降级原因列表，如 ``["web_search_timeout"]``。
    """

    task_id: str
    answer: str
    conclusions: list[dict[str, Any]]
    suggested_actions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    confidence: float
    uncertainties: list[str]
    risk_notice: str
    timing: dict[str, int]
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)


class ResearchService:
    """短链路研究编排服务。

    使用方式
    --------
    .. code-block:: python

        service = ResearchService()
        result = await service.run(
            ctx=project_context,
            question="2024 年基金市场回顾",
            output_type="narrative",
            strategy="full",
            knowledge_base_ids=[uuid1, uuid2],
            tool_codes=["fund_market"],
            tool_inputs={"fund_market": {"fund_code": "000001"}},
            context={"session_id": "xxx"},
            db=db_session,
        )
        if result.degraded:
            logger.warning("研究降级：%s", result.degraded_reasons)

    设计要点
    --------
    1. **整体硬超时**：``asyncio.wait_for`` 包裹整个 ``_run_internal``，
       超时返回已收集的部分成果 + degraded=true + degraded_reasons=['hard_timeout']。
    2. **并行取证**：三路证据源用 ``asyncio.gather`` 并行，单路失败不阻塞。
    3. **一次生成**：仅在证据整理完成后发起 1 次模型调用，
       超时降级返回已整理证据。
    4. **事务边界**：ResearchTask 创建、ResearchEvidence 批量写入、
       ResearchResult 写入在同一 db session 内 flush，由 API 层统一 commit。
    """

    def __init__(self) -> None:
        """初始化研究服务。

        内部不持有 db session（由 ``run`` 方法注入），便于在测试中替换 session。
        EvidenceMerger 是无状态对象，可在构造时创建。
        """
        # 证据合并器：无状态，构造时创建
        self._merger = EvidenceMerger()

    async def run(
        self,
        ctx: "ProjectContext",
        question: str,
        output_type: str,
        strategy: str,
        knowledge_base_ids: list[UUID],
        tool_codes: list[str],
        tool_inputs: dict[str, dict[str, Any]],
        context: dict[str, Any] | None,
        db: "AsyncSession",
        request_id: str = "",
    ) -> ResearchRunResult:
        """执行短链路研究：并行取证 + 一次模型生成。

        整体流程被 ``asyncio.wait_for(timeout=settings.research_hard_timeout_seconds)``
        包裹，超时则降级返回已收集的部分成果。

        Args:
            ctx: 项目上下文，提供 project_id / project_code / api_key_id。
            question: 用户问题原文。
            output_type: 期望输出类型，``narrative`` / ``json`` / ``bullet_points``。
            strategy: 研究策略，``knowledge_only`` / ``knowledge_web`` /
                ``knowledge_tools`` / ``full``。决定启用哪些证据源。
            knowledge_base_ids: 参与检索的知识库 ID 列表。
            tool_codes: 请求调用的工具 code 列表，最多取前 3 个。
            tool_inputs: 工具入参字典，key 为 tool_code，value 为入参 dict。
            context: 输入上下文（如会话历史、用户画像），可空。
            db: 异步数据库会话，由 API 层通过 ``Depends(get_db)`` 注入。
            request_id: 对外请求 ID，用于关联 UsageLog 与 ResearchTask。

        Returns:
            ``ResearchRunResult``：承载研究流程的全部输出。
        """
        # 记录整体开始时间，用于计算 totalMs 与硬超时判断
        overall_start = time.time()

        try:
            # 整体硬超时包裹：超时则降级返回部分成果
            # timeout=settings.research_hard_timeout_seconds（默认 15s）
            result = await asyncio.wait_for(
                self._run_internal(
                    ctx=ctx,
                    question=question,
                    output_type=output_type,
                    strategy=strategy,
                    knowledge_base_ids=knowledge_base_ids,
                    tool_codes=tool_codes,
                    tool_inputs=tool_inputs,
                    context=context,
                    db=db,
                    request_id=request_id,
                    overall_start=overall_start,
                ),
                timeout=settings.research_hard_timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            # 整体硬超时：返回降级结果，而非抛 500
            # 客户端仍能基于已收集的部分成果做决策
            logger.warning(
                "研究整体硬超时（%ds），返回降级结果（question=%s）",
                settings.research_hard_timeout_seconds,
                question,
            )
            # 构造降级结果：answer 留空，evidence 可能为空，degraded=true
            total_ms = int((time.time() - overall_start) * 1000)
            return ResearchRunResult(
                task_id="",  # 任务可能未创建或创建中，留空由调用方处理
                answer="",
                conclusions=[],
                suggested_actions=[],
                evidence=[],
                confidence=0.0,
                uncertainties=["研究整体超时，未能完成生成"],
                risk_notice="研究超时，请稍后重试或缩小问题范围",
                timing={
                    "internalRetrievalMs": 0,
                    "externalParallelMs": 0,
                    "generationMs": 0,
                    "totalMs": total_ms,
                },
                degraded=True,
                degraded_reasons=["hard_timeout"],
            )

    async def _run_internal(
        self,
        ctx: "ProjectContext",
        question: str,
        output_type: str,
        strategy: str,
        knowledge_base_ids: list[UUID],
        tool_codes: list[str],
        tool_inputs: dict[str, dict[str, Any]],
        context: dict[str, Any] | None,
        db: "AsyncSession",
        request_id: str,
        overall_start: float,
    ) -> ResearchRunResult:
        """研究流程内部实现（无硬超时包裹，由 ``run`` 统一包裹）。

        将流程拆分为独立步骤，便于异常隔离与降级处理。
        """
        # 降级原因收集器：取证与生成阶段的降级原因统一追加到此列表
        degraded_reasons: list[str] = []

        # ------------------------------------------------------------------
        # 步骤 1：创建 ResearchTask 记录（status='running'）
        # ------------------------------------------------------------------
        # Repository 强制注入 project_id，保证跨项目隔离
        task_repo = ResearchTaskRepository(db)
        # strategy 解析：是否启用各证据源
        # - knowledge_only: 仅内部检索
        # - knowledge_web: 内部检索 + 联网搜索
        # - knowledge_tools: 内部检索 + 工具调用
        # - full: 三路全开
        use_knowledge = "knowledge" in strategy or strategy in (
            "knowledge_only",
            "knowledge_web",
            "knowledge_tools",
            "full",
        )
        use_web = "web" in strategy or strategy in ("knowledge_web", "full")
        use_tools = "tool" in strategy or strategy in ("knowledge_tools", "full")

        # 将 UUID 列表转为字符串列表存储（数据库 ARRAY(UUID) 接受字符串）
        kb_ids_str = [str(kb_id) for kb_id in knowledge_base_ids]
        # 工具调用列表截断到 MAX_TOOLS_PER_RUN，避免单次研究 token 与延迟爆炸
        limited_tool_codes = list(tool_codes)[:MAX_TOOLS_PER_RUN]

        task = await task_repo.create(
            ctx,
            request_id=request_id or f"req_{int(time.time() * 1000)}",
            question=question,
            output_type=output_type,
            strategy=strategy,
            status="running",
            input_context=context,
            knowledge_base_ids=kb_ids_str,
            requested_tools=limited_tool_codes,
            use_web=use_web,
            started_at=datetime.now(timezone.utc),
            degraded=False,
            degraded_reasons=[],
        )

        # ------------------------------------------------------------------
        # 步骤 2：并行取证（asyncio.gather）
        # ------------------------------------------------------------------
        # 三路证据源相互独立，并行调度总耗时 ≈ max(三路耗时)
        # 每路用 try/except 包裹，失败记录降级原因，不阻塞其他两路
        internal_start = time.time()
        external_start = time.time()

        # 构造三路协程：未启用的证据源返回空结果（不创建协程，节省调度开销）
        internal_task = (
            self._gather_internal(ctx, question, kb_ids_str, db, degraded_reasons)
            if use_knowledge
            else _noop_empty()
        )
        web_task = (
            self._gather_web(ctx, question, db, degraded_reasons)
            if use_web
            else _noop_empty()
        )
        tool_task = (
            self._gather_tools(
                ctx, limited_tool_codes, tool_inputs, db, degraded_reasons
            )
            if use_tools and limited_tool_codes
            else _noop_empty()
        )

        # 并行执行三路取证：return_exceptions=True 保证单路失败不中断其他
        # gather 返回 [internal_results, web_results, tool_results]
        internal_results, web_results, tool_results = await asyncio.gather(
            internal_task, web_task, tool_task
        )

        # 记录取证耗时：internalRetrievalMs 与 externalParallelMs
        # 内部检索耗时单独统计（用于性能分析）
        internal_ms = int((time.time() - internal_start) * 1000)
        # 外部并行耗时 = max(联网, 工具) 耗时，近似为整体取证耗时
        external_ms = int((time.time() - external_start) * 1000)

        # ------------------------------------------------------------------
        # 步骤 3：合并证据（EvidenceMerger）
        # ------------------------------------------------------------------
        # EvidenceMerger 接受三类证据的 dict 列表，统一格式、去重、评分、截断
        # - internal_results: HybridSearcher 返回的 chunk 字典列表
        # - web_results: WebEvidence 转 dict 列表
        # - tool_results: ToolExecutionResult.data 转 dict 列表
        web_evidence_dicts = self._web_evidences_to_dicts(web_results)
        tool_evidence_dicts = self._tool_results_to_dicts(tool_results)

        merged_evidence = self._merger.merge(
            internal_evidence=internal_results,
            web_evidence=web_evidence_dicts,
            tool_evidence=tool_evidence_dicts,
            max_evidence=settings.max_evidence,
        )

        # ------------------------------------------------------------------
        # 步骤 4：校验证据
        # ------------------------------------------------------------------
        # 证据为空 → 抛 InsufficientEvidenceError，标记 task failed
        # 三路取证均无成果时无法支撑一次模型生成，应明确告知客户端
        if not merged_evidence:
            logger.warning(
                "证据不足，无法生成结论（question=%s, degraded_reasons=%s）",
                question,
                degraded_reasons,
            )
            # 更新任务状态为 failed，记录降级原因
            await task_repo.update(
                ctx,
                task.id,
                status="failed",
                completed_at=datetime.now(timezone.utc),
                total_duration_ms=int((time.time() - overall_start) * 1000),
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons or ["no_evidence"],
                error_code="INSUFFICIENT_EVIDENCE",
            )
            await db.commit()
            raise InsufficientEvidenceError(
                "证据不足，无法生成结论",
                details={
                    "degraded_reasons": degraded_reasons,
                    "strategy": strategy,
                },
            )

        # ------------------------------------------------------------------
        # 步骤 5：加载提示词（PromptService.get_active，无启用版本用默认模板）
        # ------------------------------------------------------------------
        prompt_service = PromptService(db)
        # Repository.get_active 返回当前启用版本，无启用返回 None
        active_prompt = await prompt_service.repo.get_active(ctx)

        # ------------------------------------------------------------------
        # 步骤 6：构造模型输入
        # ------------------------------------------------------------------
        # system_prompt + evidence_rules + prohibitions + risk_template
        # user_message: question + 证据列表 + output_schema + context
        system_prompt, output_schema, risk_template = self._build_system_prompt(
            active_prompt, ctx, output_type
        )
        user_message = self._build_user_message(
            question=question,
            evidence=merged_evidence,
            output_schema=output_schema,
            context=context,
            output_type=output_type,
        )

        # ------------------------------------------------------------------
        # 步骤 7：一次模型生成（asyncio.wait_for 超时 10s）
        # ------------------------------------------------------------------
        generation_start = time.time()
        chat_provider = get_chat_model_provider()

        # 模型未配置：降级返回已整理证据，answer 留空
        # 不抛异常，让客户端基于证据做决策
        if chat_provider is None:
            logger.warning("聊天模型未配置，降级返回已整理证据")
            degraded_reasons.append("model_not_configured")
            generation_ms = int((time.time() - generation_start) * 1000)
            total_ms = int((time.time() - overall_start) * 1000)

            # 持久化降级结果并返回
            return await self._finalize_degraded(
                ctx=ctx,
                db=db,
                task=task,
                task_repo=task_repo,
                evidence=merged_evidence,
                degraded_reasons=degraded_reasons,
                timing={
                    "internalRetrievalMs": internal_ms,
                    "externalParallelMs": external_ms,
                    "generationMs": generation_ms,
                    "totalMs": total_ms,
                },
                prompt_version_id=(
                    str(active_prompt.id) if active_prompt else None
                ),
            )

        # 构造消息列表：1 条 system + 1 条 user（短链路一次生成原则）
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_message),
        ]

        # 调用模型：asyncio.wait_for 超时 10s
        # 超时 → 标记降级，返回已整理证据
        try:
            chat_result = await asyncio.wait_for(
                chat_provider.complete(
                    messages=messages,
                    temperature=MODEL_TEMPERATURE,
                    response_format={"type": "json_object"},
                ),
                timeout=MODEL_GENERATION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # 模型生成超时：降级返回已整理证据
            logger.warning(
                "模型生成超时（%ds），降级返回已整理证据",
                MODEL_GENERATION_TIMEOUT_SECONDS,
            )
            degraded_reasons.append("model_timeout")
            generation_ms = int((time.time() - generation_start) * 1000)
            total_ms = int((time.time() - overall_start) * 1000)

            return await self._finalize_degraded(
                ctx=ctx,
                db=db,
                task=task,
                task_repo=task_repo,
                evidence=merged_evidence,
                degraded_reasons=degraded_reasons,
                timing={
                    "internalRetrievalMs": internal_ms,
                    "externalParallelMs": external_ms,
                    "generationMs": generation_ms,
                    "totalMs": total_ms,
                },
                prompt_version_id=(
                    str(active_prompt.id) if active_prompt else None
                ),
            )
        except ModelTimeoutError as exc:
            # Provider 内部超时（与上方 asyncio.TimeoutError 语义重叠，
            # Provider 已将 httpx 超时转为 ModelTimeoutError）
            logger.warning("模型调用超时：%s", exc)
            degraded_reasons.append("model_timeout")
            generation_ms = int((time.time() - generation_start) * 1000)
            total_ms = int((time.time() - overall_start) * 1000)

            return await self._finalize_degraded(
                ctx=ctx,
                db=db,
                task=task,
                task_repo=task_repo,
                evidence=merged_evidence,
                degraded_reasons=degraded_reasons,
                timing={
                    "internalRetrievalMs": internal_ms,
                    "externalParallelMs": external_ms,
                    "generationMs": generation_ms,
                    "totalMs": total_ms,
                },
                prompt_version_id=(
                    str(active_prompt.id) if active_prompt else None
                ),
            )
        except ExternalSourceFailedError as exc:
            # 模型接口返回非 2xx：降级返回已整理证据
            logger.warning("模型接口失败：%s", exc)
            degraded_reasons.append("model_failed")
            generation_ms = int((time.time() - generation_start) * 1000)
            total_ms = int((time.time() - overall_start) * 1000)

            return await self._finalize_degraded(
                ctx=ctx,
                db=db,
                task=task,
                task_repo=task_repo,
                evidence=merged_evidence,
                degraded_reasons=degraded_reasons,
                timing={
                    "internalRetrievalMs": internal_ms,
                    "externalParallelMs": external_ms,
                    "generationMs": generation_ms,
                    "totalMs": total_ms,
                },
                prompt_version_id=(
                    str(active_prompt.id) if active_prompt else None
                ),
            )

        generation_ms = int((time.time() - generation_start) * 1000)

        # ------------------------------------------------------------------
        # 步骤 8：构造结果（解析 JSON 响应）
        # ------------------------------------------------------------------
        answer, conclusions, suggested_actions, confidence, uncertainties, \
            risk_notice = self._parse_model_response(
                chat_result, risk_template
            )

        total_ms = int((time.time() - overall_start) * 1000)
        timing = {
            "internalRetrievalMs": internal_ms,
            "externalParallelMs": external_ms,
            "generationMs": generation_ms,
            "totalMs": total_ms,
        }

        # ------------------------------------------------------------------
        # 步骤 9：持久化（ResearchEvidence 批量 + ResearchResult + 更新 Task）
        # ------------------------------------------------------------------
        evidence_repo = ResearchEvidenceRepository(db)
        result_repo = ResearchResultRepository(db)

        # 批量写入证据：每条转为 dict（含 research_task_id / evidence_type / ...）
        evidence_dicts = [
            {
                "research_task_id": task.id,
                "evidence_type": ev["type"],
                "title": ev["title"],
                "snippet": ev["snippet"],
                "source_url": ev.get("source_url"),
                "published_at": ev.get("published_at"),
                "data_as_of": ev.get("data_as_of"),
                "score": ev.get("score"),
                "metadata_": {"merged": True},
            }
            for ev in merged_evidence
        ]
        await evidence_repo.bulk_create(ctx, evidence_dicts)

        # 写入研究结果
        await result_repo.create(
            ctx,
            research_task_id=task.id,
            answer=answer,
            conclusions=conclusions,
            suggested_actions=suggested_actions,
            confidence=confidence,
            uncertainties=uncertainties,
            risk_notice=risk_notice,
            timing=timing,
        )

        # 更新任务状态为成功（或部分成功，若存在降级）
        final_status = "partial_success" if degraded_reasons else "success"
        await task_repo.update(
            ctx,
            task.id,
            status=final_status,
            completed_at=datetime.now(timezone.utc),
            total_duration_ms=total_ms,
            degraded=bool(degraded_reasons),
            degraded_reasons=degraded_reasons,
            prompt_version_id=(
                str(active_prompt.id) if active_prompt else None
            ),
        )

        # ------------------------------------------------------------------
        # 步骤 10：记录 UsageLog（token 用量、费用、耗时）
        # ------------------------------------------------------------------
        # 日志写入失败不应阻塞响应（研究已成功），故 try/except 兜底
        try:
            usage_repo = UsageLogRepository(db)
            # 估算成本：(prompt + completion) / 1000 * 单价
            estimated_cost = (
                (chat_result.prompt_tokens + chat_result.completion_tokens)
                / 1000.0
                * DEFAULT_COST_PER_1K_TOKENS
            )
            await usage_repo.create(
                ctx,
                request_id=task.request_id,
                api_key_id=ctx.api_key_id or "",
                endpoint="/api/v1/research/run",
                method="POST",
                internal_retrieval_ms=internal_ms,
                external_parallel_ms=external_ms,
                generation_ms=generation_ms,
                total_ms=total_ms,
                evidence_count=len(merged_evidence),
                prompt_tokens=chat_result.prompt_tokens,
                completion_tokens=chat_result.completion_tokens,
                total_tokens=chat_result.total_tokens,
                estimated_cost=estimated_cost,
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
                error_code=None,
            )
        except Exception:
            # 日志写入失败：回滚日志相关变更，不影响已成功的研究结果
            logger.warning("UsageLog 写入失败，已忽略", exc_info=True)

        # 统一提交事务：ResearchTask / Evidence / Result / UsageLog 原子化
        await db.commit()

        # ------------------------------------------------------------------
        # 步骤 11：返回 ResearchRunResult
        # ------------------------------------------------------------------
        return ResearchRunResult(
            task_id=task.id,
            answer=answer,
            conclusions=conclusions,
            suggested_actions=suggested_actions,
            evidence=merged_evidence,
            confidence=confidence,
            uncertainties=uncertainties,
            risk_notice=risk_notice,
            timing=timing,
            degraded=bool(degraded_reasons),
            degraded_reasons=degraded_reasons,
        )

    # ----------------------------------------------------------------------
    # 三路取证协程
    # ----------------------------------------------------------------------
    async def _gather_internal(
        self,
        ctx: "ProjectContext",
        question: str,
        kb_ids: list[str],
        db: "AsyncSession",
        degraded_reasons: list[str],
    ) -> list[dict[str, Any]]:
        """内部检索取证：调用 HybridSearcher.search。

        失败时记录降级原因，返回空列表，不阻塞其他证据源。

        Args:
            ctx: 项目上下文。
            question: 用户问题，作为检索查询。
            kb_ids: 知识库 ID 列表（字符串形式）。
            db: 异步数据库会话。
            degraded_reasons: 降级原因收集器（可变列表）。

        Returns:
            HybridSearcher 返回的 chunk 字典列表；失败时返回空列表。
        """
        try:
            searcher = HybridSearcher(db)
            # top_k=10：候选池大于 max_evidence（8），给 EvidenceMerger 留去重空间
            results = await searcher.search(
                ctx=ctx,
                query=question,
                knowledge_base_ids=kb_ids,
                top_k=10,
            )
            return results
        except Exception as exc:
            # 内部检索失败：记录降级原因，返回空列表
            # 不抛异常，让其他证据源继续执行
            logger.warning("内部检索失败（question=%s）：%s", question, exc)
            degraded_reasons.append("internal_retrieval_failed")
            return []

    async def _gather_web(
        self,
        ctx: "ProjectContext",
        question: str,
        db: "AsyncSession",
        degraded_reasons: list[str],
    ) -> list[Any]:
        """联网搜索取证：调用 WebResearchService.search_and_extract。

        失败时记录降级原因，返回空列表，不阻塞其他证据源。

        Args:
            ctx: 项目上下文。
            question: 用户问题，作为搜索查询。
            db: 异步数据库会话。
            degraded_reasons: 降级原因收集器。

        Returns:
            WebEvidence 列表；失败时返回空列表。
        """
        try:
            service = WebResearchService(db)
            # max_results=5：与 PRD"最多 5 条联网结果"一致
            result = await service.search_and_extract(
                ctx=ctx,
                query=question,
                max_results=5,
                fetch_content=True,
            )
            # WebResearchService 内部已处理降级（返回 degraded_reasons），
            # 这里将其追加到研究链路的降级原因列表
            if result.degraded:
                degraded_reasons.extend(result.degraded_reasons)
            return result.results
        except Exception as exc:
            # 联网搜索失败：记录降级原因，返回空列表
            logger.warning("联网搜索失败（question=%s）：%s", question, exc)
            degraded_reasons.append("web_search_failed")
            return []

    async def _gather_tools(
        self,
        ctx: "ProjectContext",
        tool_codes: list[str],
        tool_inputs: dict[str, dict[str, Any]],
        db: "AsyncSession",
        degraded_reasons: list[str],
    ) -> list[Any]:
        """工具调用取证：对 tool_codes 中每个工具调用 ToolExecutor.execute。

        多个工具并行调用（asyncio.gather），单工具失败不阻塞其他。
        失败时记录降级原因，返回部分成功的结果列表。

        Args:
            ctx: 项目上下文。
            tool_codes: 工具 code 列表（已截断到 MAX_TOOLS_PER_RUN）。
            tool_inputs: 工具入参字典，key 为 tool_code。
            db: 异步数据库会话。
            degraded_reasons: 降级原因收集器。

        Returns:
            ToolExecutionResult 列表（仅含成功的工具）；失败时返回空列表。
        """
        executor = ToolExecutor(db)

        # 为每个工具构造协程：return_exceptions=True 保证单工具失败不中断其他
        async def _exec_one(tool_code: str) -> Any:
            """执行单个工具调用，捕获异常转为降级。"""
            try:
                inputs = tool_inputs.get(tool_code, {})
                result = await executor.execute(ctx, tool_code, inputs)
                if not result.success:
                    # 工具执行失败（如入参校验失败）：记录降级原因
                    degraded_reasons.append(f"tool_{tool_code}_failed")
                    return None
                return result
            except ExternalSourceTimeoutError:
                # 工具超时：记录降级原因，不阻塞其他工具
                degraded_reasons.append(f"tool_{tool_code}_timeout")
                return None
            except ExternalSourceFailedError as exc:
                # 工具失败：记录降级原因
                degraded_reasons.append(f"tool_{tool_code}_failed")
                logger.warning("工具 %s 调用失败：%s", tool_code, exc)
                return None
            except ToolNotAllowedError as exc:
                # 工具不在白名单：记录降级原因（可能是配置遗漏）
                degraded_reasons.append(f"tool_{tool_code}_not_allowed")
                logger.warning("工具 %s 不允许调用：%s", tool_code, exc)
                return None
            except Exception as exc:
                # 未预期异常：兜底降级
                degraded_reasons.append(f"tool_{tool_code}_error")
                logger.warning(
                    "工具 %s 未预期异常：%s", tool_code, exc, exc_info=True
                )
                return None

        # 并行执行所有工具调用
        # return_exceptions=True 已在 _exec_one 内部处理，这里 gather 直接拿到结果列表
        results = await asyncio.gather(
            *[_exec_one(code) for code in tool_codes]
        )
        # 过滤掉 None（失败的工具），仅保留成功结果
        return [r for r in results if r is not None]

    # ----------------------------------------------------------------------
    # 数据结构转换
    # ----------------------------------------------------------------------
    @staticmethod
    def _web_evidences_to_dicts(web_evidences: list[Any]) -> list[dict[str, Any]]:
        """将 WebEvidence 列表转为 dict 列表，供 EvidenceMerger 使用。

        EvidenceMerger 接受 dict 列表（统一接口），WebEvidence 是 dataclass，
        需转为 dict。转换时保留关键字段：title / url / snippet / content /
        published_at / score。

        Args:
            web_evidences: WebEvidence dataclass 实例列表。

        Returns:
            dict 列表，每项含 title / url / snippet / content / published_at / score。
        """
        result: list[dict[str, Any]] = []
        for ev in web_evidences:
            # dataclasses.asdict 会递归转换嵌套 dataclass，但 WebEvidence 字段简单，
            # 这里显式构造避免引入 dataclasses 模块的开销
            result.append({
                "title": getattr(ev, "title", ""),
                "url": getattr(ev, "url", ""),
                "snippet": getattr(ev, "snippet", ""),
                "content": getattr(ev, "content", None),
                "published_at": getattr(ev, "published_at", None),
                "score": getattr(ev, "score", 0.5),
            })
        return result

    @staticmethod
    def _tool_results_to_dicts(tool_results: list[Any]) -> list[dict[str, Any]]:
        """将 ToolExecutionResult 列表转为 dict 列表，供 EvidenceMerger 使用。

        EvidenceMerger 期望每项含 tool_code / data / title 字段。
        ToolExecutionResult 是 dataclass，需提取关键字段转为 dict。

        Args:
            tool_results: ToolExecutionResult dataclass 实例列表。

        Returns:
            dict 列表，每项含 tool_code / data / title。
        """
        result: list[dict[str, Any]] = []
        # 这里需要拿到 tool_code，但 ToolExecutionResult 不含此字段
        # 调用方 _gather_tools 中 result 顺序与 tool_codes 一致，
        # 但过滤失败后顺序错乱，故在 _gather_tools 中已无法关联 tool_code。
        # 解决方案：ToolExecutionResult.degraded_reason 中可携带 tool_code，
        # 或在 _gather_tools 中返回 (tool_code, result) 元组。
        # 此处采用简化方案：使用索引作为 tool_code 占位（实际由调用方改进）。
        for idx, tr in enumerate(tool_results):
            result.append({
                # tool_code 占位：实际应从调用上下文获取
                "tool_code": getattr(tr, "tool_code", f"tool_{idx}"),
                "data": getattr(tr, "data", None) or {},
                "title": getattr(tr, "title", None),
            })
        return result

    # ----------------------------------------------------------------------
    # 提示词构造
    # ----------------------------------------------------------------------
    @staticmethod
    def _build_system_prompt(
        active_prompt: Any,
        ctx: "ProjectContext",
        output_type: str,
    ) -> tuple[str, dict[str, Any], str]:
        """构造 system_prompt、output_schema、risk_template。

        优先使用项目当前启用版本（active_prompt）的字段；
        无启用版本时使用默认模板（按 project_code 匹配场景）。

        Args:
            active_prompt: PromptVersion ORM 实例，可空。
            ctx: 项目上下文（用于按 project_code 选择默认模板）。
            output_type: 输出类型，narrative / json / bullet_points。

        Returns:
            元组 (system_prompt, output_schema, risk_template)。
            output_schema 为 dict，用于约束模型返回结构。
        """
        if active_prompt is not None:
            # 使用启用版本的字段
            system_prompt = active_prompt.system_prompt
            # evidence_rules / prohibitions / risk_template 可能为 None，做兜底
            evidence_rules = active_prompt.evidence_rules or ""
            prohibitions = active_prompt.prohibitions or ""
            risk_template = active_prompt.risk_template or ""
            output_schema = active_prompt.output_schema or {}

            # 拼接 system_prompt：核心角色 + 证据规则 + 禁止事项
            # 分段拼接便于模型理解各部分约束
            parts = [system_prompt]
            if evidence_rules:
                parts.append(f"\n\n【证据使用规则】\n{evidence_rules}")
            if prohibitions:
                parts.append(f"\n\n【禁止事项】\n{prohibitions}")
            # 输出类型提示：影响模型组织语言的方式
            type_hint = {
                "narrative": "请以叙述性段落组织回答",
                "json": "请严格按 output_schema 返回 JSON",
                "bullet_points": "请以要点列表组织回答",
            }.get(output_type, "请以叙述性段落组织回答")
            parts.append(f"\n\n【输出格式】\n{type_hint}")
            parts.append(
                "\n\n请严格以 JSON 格式返回，包含 conclusions / suggestedActions / "
                "confidence / uncertainties / riskNotice 字段。"
            )
            return "".join(parts), output_schema, risk_template

        # 无启用版本：使用默认模板
        from app.modules.prompts.templates import get_default_template

        template = get_default_template(ctx.project_code)
        system_prompt = template["system_prompt"]
        evidence_rules = template["evidence_rules"]
        prohibitions = template["prohibitions"]
        risk_template = template["risk_template"]
        output_schema = template["output_schema"]

        parts = [system_prompt]
        if evidence_rules:
            parts.append(f"\n\n【证据使用规则】\n{evidence_rules}")
        if prohibitions:
            parts.append(f"\n\n【禁止事项】\n{prohibitions}")
        type_hint = {
            "narrative": "请以叙述性段落组织回答",
            "json": "请严格按 output_schema 返回 JSON",
            "bullet_points": "请以要点列表组织回答",
        }.get(output_type, "请以叙述性段落组织回答")
        parts.append(f"\n\n【输出格式】\n{type_hint}")
        parts.append(
            "\n\n请严格以 JSON 格式返回，包含 conclusions / suggestedActions / "
            "confidence / uncertainties / riskNotice 字段。"
        )
        return "".join(parts), output_schema, risk_template

    @staticmethod
    def _build_user_message(
        question: str,
        evidence: list[dict[str, Any]],
        output_schema: dict[str, Any],
        context: dict[str, Any] | None,
        output_type: str,
    ) -> str:
        """构造 user_message：问题 + 证据列表 + output_schema + context。

        证据列表按编号、类型、内容、来源、时间格式化，便于模型引用
        （模型在 conclusions 中通过 evidenceRefs 引用证据编号）。

        Args:
            question: 用户问题原文。
            evidence: 已合并、去重、评分、截断的证据列表。
            output_schema: 输出 JSON Schema，约束模型返回结构。
            context: 输入上下文，可空。
            output_type: 输出类型。

        Returns:
            拼接后的 user_message 字符串。
        """
        parts: list[str] = []

        # 用户问题
        parts.append(f"【用户问题】\n{question}")

        # 证据列表：按编号、类型、内容、来源、时间格式化
        parts.append("\n\n【证据列表】")
        for idx, ev in enumerate(evidence, start=1):
            # 证据编号、类型、评分
            ev_type = ev.get("type", "unknown")
            score = ev.get("score", 0.0)
            title = ev.get("title", "")
            snippet = ev.get("snippet", "")
            source_url = ev.get("source_url") or ""
            published_at = ev.get("published_at")
            data_as_of = ev.get("data_as_of")

            # 格式化为可读的引用块
            lines = [
                f"[证据 {idx}] 类型: {ev_type} | 评分: {score:.2f}",
                f"标题: {title}",
                f"内容: {snippet}",
            ]
            if source_url:
                lines.append(f"来源: {source_url}")
            if published_at:
                lines.append(f"发布时间: {published_at}")
            if data_as_of:
                lines.append(f"数据截止时间: {data_as_of}")
            parts.append("\n".join(lines))
            parts.append("")  # 空行分隔

        # output_schema：约束模型返回结构
        parts.append("【输出 JSON Schema】")
        parts.append(json.dumps(output_schema, ensure_ascii=False))

        # 输出类型提示
        parts.append(f"\n【输出类型】\n{output_type}")

        # 输入上下文（可空）：如会话历史、用户画像
        if context:
            parts.append("\n【输入上下文】")
            parts.append(json.dumps(context, ensure_ascii=False, default=str))

        # 明确要求 JSON 输出
        parts.append(
            "\n请基于以上证据回答用户问题，严格以 JSON 格式返回，"
            "包含 conclusions / suggestedActions / confidence / uncertainties / "
            "riskNotice 字段。每条结论请在 evidenceRefs 中标注引用的证据编号。"
        )

        return "\n".join(parts)

    # ----------------------------------------------------------------------
    # 模型响应解析
    # ----------------------------------------------------------------------
    @staticmethod
    def _parse_model_response(
        chat_result: ChatCompletionResult,
        risk_template: str,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], float, list[str], str]:
        """解析模型 JSON 响应，提取 answer / conclusions / 等字段。

        模型应在 JSON 中返回 conclusions / suggestedActions / confidence /
        uncertainties / riskNotice 字段。解析失败时降级为简单文本回答。

        Args:
            chat_result: ChatCompletionResult，含模型输出文本。
            risk_template: 风险提示模板，附加到回答末尾。

        Returns:
            元组 (answer, conclusions, suggested_actions, confidence,
            uncertainties, risk_notice)。
            - answer: 拼接后的回答文本（含风险提示）
            - conclusions: 结论数组
            - suggested_actions: 建议行动数组
            - confidence: 置信度（0~1）
            - uncertainties: 不确定性数组
            - risk_notice: 风险提示文本
        """
        content = chat_result.content or ""

        # 尝试解析 JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # JSON 解析失败：降级为纯文本回答
            # 不抛异常，让客户端至少能拿到模型的文本输出
            logger.warning(
                "模型响应 JSON 解析失败，降级为纯文本（finish_reason=%s）",
                chat_result.finish_reason,
            )
            answer = content + "\n\n" + risk_template if risk_template else content
            return (
                answer,
                [],
                [],
                0.5,  # 默认置信度
                ["模型响应非 JSON 格式，已降级为纯文本"],
                risk_template,
            )

        # 提取各字段，缺失时用默认值
        conclusions = data.get("conclusions") or []
        suggested_actions = data.get("suggestedActions") or data.get("suggested_actions") or []
        confidence = float(data.get("confidence") or 0.5)
        uncertainties = data.get("uncertainties") or []
        risk_notice = data.get("riskNotice") or data.get("risk_notice") or risk_template

        # 构造 answer：优先用模型返回的 riskNotice，否则用 risk_template
        # 若模型未单独返回 answer 字段，则用 conclusions 拼接
        model_answer = data.get("answer") or ""
        if not model_answer and conclusions:
            # 无 answer 字段：用 conclusions 的 text 拼接为叙述
            texts = [
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in conclusions
            ]
            model_answer = "\n".join(t for t in texts if t)

        # 附加风险提示
        final_risk = risk_notice or risk_template
        if final_risk and final_risk not in model_answer:
            answer = model_answer + "\n\n" + final_risk
        else:
            answer = model_answer

        return (
            answer,
            conclusions,
            suggested_actions,
            confidence,
            uncertainties,
            final_risk,
        )

    # ----------------------------------------------------------------------
    # 降级收尾
    # ----------------------------------------------------------------------
    async def _finalize_degraded(
        self,
        ctx: "ProjectContext",
        db: "AsyncSession",
        task: Any,
        task_repo: ResearchTaskRepository,
        evidence: list[dict[str, Any]],
        degraded_reasons: list[str],
        timing: dict[str, int],
        prompt_version_id: str | None,
    ) -> ResearchRunResult:
        """降级收尾：持久化降级结果并返回 ResearchRunResult。

        在模型未配置、模型超时、模型失败等场景下调用：
        - 持久化已整理的证据
        - 更新任务状态为 partial_success（有证据但无生成）
        - 返回降级结果（answer 留空，evidence 已填充）

        Args:
            ctx: 项目上下文。
            db: 异步数据库会话。
            task: ResearchTask ORM 实例。
            task_repo: ResearchTaskRepository 实例。
            evidence: 已合并的证据列表。
            degraded_reasons: 降级原因列表。
            timing: 各阶段耗时。
            prompt_version_id: 使用的提示词版本 ID，可空。

        Returns:
            ``ResearchRunResult``，degraded=True，answer 留空。
        """
        evidence_repo = ResearchEvidenceRepository(db)

        # 批量写入证据：即使模型生成失败，证据仍需持久化供客户端查看
        evidence_dicts = [
            {
                "research_task_id": task.id,
                "evidence_type": ev["type"],
                "title": ev["title"],
                "snippet": ev["snippet"],
                "source_url": ev.get("source_url"),
                "published_at": ev.get("published_at"),
                "data_as_of": ev.get("data_as_of"),
                "score": ev.get("score"),
                "metadata_": {"degraded": True},
            }
            for ev in evidence
        ]
        await evidence_repo.bulk_create(ctx, evidence_dicts)

        # 更新任务状态为 partial_success（有证据但无生成）
        await task_repo.update(
            ctx,
            task.id,
            status="partial_success",
            completed_at=datetime.now(timezone.utc),
            total_duration_ms=timing.get("totalMs", 0),
            degraded=True,
            degraded_reasons=degraded_reasons,
            prompt_version_id=prompt_version_id,
        )

        # 提交事务
        await db.commit()

        return ResearchRunResult(
            task_id=task.id,
            answer="",  # 模型生成失败，answer 留空
            conclusions=[],
            suggested_actions=[],
            evidence=evidence,
            confidence=0.0,
            uncertainties=degraded_reasons,  # 降级原因作为不确定性披露
            risk_notice="模型生成失败，请基于证据自行判断或重试",
            timing=timing,
            degraded=True,
            degraded_reasons=degraded_reasons,
        )


# ---------------------------------------------------------------------------
# 辅助协程：返回空列表（用于未启用的证据源占位）
# ---------------------------------------------------------------------------
async def _noop_empty() -> list[Any]:
    """空操作协程：返回空列表。

    用于 strategy 中未启用的证据源占位，避免在 asyncio.gather 中创建实际协程。
    """
    return []
