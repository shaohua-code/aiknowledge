from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.domains.intelligence.policy import (
    AnswerMode,
    EvidenceSufficiency,
    PolicyDecision,
    decide_answer_mode,
)
from knowledge_core.domains.intelligence.repository import IntelligenceRepository
from knowledge_core.domains.intelligence.retrieval import RetrievalHit, RetrievalService
from knowledge_core.domains.intelligence.schemas import AnswerRequest
from knowledge_core.domains.operations.repository import OperationRepository
from knowledge_core.infrastructure.providers import (
    WebSearchHit,
    get_chat_provider,
    get_web_search_provider,
    parse_json_object,
)
from knowledge_core.shared.context import ApplicationContext
from knowledge_core.shared.errors import (
    NotFoundError,
    ProviderUnavailableError,
    StructuredOutputError,
)
from knowledge_core.shared.request_id import get_request_id


class AnswerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.intelligence = IntelligenceRepository(session)
        self.operations = OperationRepository(session)

    async def answer(self, context: ApplicationContext, payload: AnswerRequest) -> dict[str, Any]:
        started = time.perf_counter()
        profile = await self.intelligence.get_answer_profile_by_code(context, payload.profile)
        if not profile:
            raise NotFoundError("回答策略不存在或未启用")
        retrieval_profile = await self.intelligence.get_retrieval_profile(
            context.application_id, context.environment_id, profile.retrieval_profile_id
        )
        if not retrieval_profile:
            raise NotFoundError("回答策略关联的检索策略不存在")

        request_id = get_request_id()
        trace = await self.operations.create_trace(
            context,
            request_id=request_id,
            operation="answer",
            profile_code=profile.code,
            query_digest=hashlib.sha256(payload.query.encode("utf-8")).hexdigest(),
        )
        await self.session.commit()

        try:
            retrieval_started = time.perf_counter()
            hits = await RetrievalService(self.session).search(
                context, retrieval_profile, payload.query
            )
            retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000)
            chat = get_chat_provider()
            web = get_web_search_provider()
            decision = decide_answer_mode(
                hits,
                profile,
                model_available=chat.available,
                web_available=web.available,
            )
            web_hits: list[WebSearchHit] = []
            warnings: list[str] = []
            degraded_reasons: list[str] = []
            input_tokens = 0
            output_tokens = 0
            result: dict[str, Any]

            if decision.mode == AnswerMode.WEB_GROUNDED:
                try:
                    web_hits = await web.search(payload.query, limit=5)
                except ProviderUnavailableError:
                    degraded_reasons.append("WEB_SEARCH_PROVIDER_UNAVAILABLE")
                if not web_hits:
                    decision = PolicyDecision(
                        EvidenceSufficiency.NONE,
                        AnswerMode.INSUFFICIENT_EVIDENCE,
                        False,
                        "联网搜索没有返回可引用证据",
                    )

            evidence: list[RetrievalHit | WebSearchHit] = [*hits, *web_hits]

            if decision.mode == AnswerMode.INSUFFICIENT_EVIDENCE:
                result = {
                    "answerMode": decision.mode.value,
                    "answer": "当前专属知识没有提供足够证据，无法可靠回答这个问题。",
                    "structuredOutput": {},
                    "confidence": 0.1,
                    "warnings": [decision.reason],
                    "missingEvidence": ["与问题直接相关且达到质量门槛的知识证据"],
                }
            elif not chat.available:
                excerpts = "\n\n".join(
                    f"[{index + 1}] {hit.content}" for index, hit in enumerate(evidence)
                )
                result = {
                    "answerMode": AnswerMode.DEGRADED.value,
                    "answer": excerpts or "知识和模型服务当前都不可用。",
                    "structuredOutput": {},
                    "confidence": round(max((hit.score for hit in evidence), default=0.1) * 0.6, 2),
                    "warnings": ["Chat Provider 未配置，已返回可验证的知识片段"],
                }
                degraded_reasons.append("CHAT_PROVIDER_UNAVAILABLE")
            else:
                evidence_text = "\n\n".join(
                    f"[{index + 1}] 标题：{hit.title}\n内容：{hit.content}"
                    for index, hit in enumerate(evidence)
                )
                source_rule = {
                    AnswerMode.KNOWLEDGE_GROUNDED: "只能基于证据回答，事实必须引用证据编号。",
                    AnswerMode.HYBRID: "区分证据事实与模型分析，不得把推断伪装成知识事实。",
                    AnswerMode.MODEL_ONLY: "当前没有知识证据，明确说明回答来自模型通用能力。",
                    AnswerMode.WEB_GROUNDED: "只能基于联网证据回答，每个事实必须可追溯到 URL。",
                }.get(decision.mode, "明确说明信息来源和不确定性。")
                messages = [
                    {
                        "role": "system",
                        "content": (
                            f"{profile.system_prompt}\n{source_rule}\n"
                            "必须输出 JSON 对象，字段包括 answer、structuredOutput、warnings。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": payload.query,
                                "temporaryInputs": payload.inputs,
                                "evidence": evidence_text,
                                "answerMode": decision.mode.value,
                                "outputSchema": profile.output_schema,
                                "authorizedTools": profile.tool_codes,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
                model_result = await chat.generate(messages)
                parsed = parse_json_object(model_result.content)
                input_tokens = model_result.input_tokens
                output_tokens = model_result.output_tokens
                warnings.extend(str(item) for item in parsed.get("warnings", []) if item)
                structured_output = parsed.get("structuredOutput") or {}
                if profile.output_schema:
                    try:
                        Draft202012Validator(profile.output_schema).validate(structured_output)
                    except ValidationError as exc:
                        path = ".".join(str(item) for item in exc.absolute_path)
                        raise StructuredOutputError(
                            "模型结果没有通过回答策略的 JSON Schema 校验",
                            details={"path": path, "reason": exc.message},
                            suggestion="调整回答提示词或输出 Schema 后重试",
                        ) from exc
                if decision.mode == AnswerMode.MODEL_ONLY:
                    warnings.insert(0, "当前知识库未命中相关内容，本回答由模型通用能力生成")
                confidence_base = max((hit.score for hit in evidence), default=0.55)
                confidence_factor = 1.0 if evidence else 0.65
                result = {
                    "answerMode": decision.mode.value,
                    "answer": str(parsed.get("answer") or ""),
                    "structuredOutput": structured_output,
                    "confidence": round(min(confidence_base * confidence_factor, 0.98), 2),
                    "warnings": warnings,
                }

            total_ms = round((time.perf_counter() - started) * 1000)
            knowledge_citations = [
                hit.citation | {"title": hit.title, "score": hit.score} for hit in hits
            ]
            await self.operations.replace_evidence(context, trace, evidence)
            await self.operations.finish_trace(
                trace,
                status="succeeded",
                answer_mode=result["answerMode"],
                confidence=result["confidence"],
                evidence_count=len(evidence),
                degraded=bool(degraded_reasons),
                degraded_reasons=degraded_reasons,
                total_ms=total_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            await self.session.commit()
            return {
                "requestId": request_id,
                **result,
                "knowledge": {
                    "used": bool(hits),
                    "hitCount": len(hits),
                    "citations": knowledge_citations if payload.options.include_citations else [],
                    "evidence": [
                        {
                            "title": hit.title,
                            "content": hit.content,
                            "score": hit.score,
                            "citation": hit.citation,
                        }
                        for hit in hits
                    ]
                    if payload.options.include_evidence
                    else [],
                },
                "web": {
                    "used": bool(web_hits),
                    "hitCount": len(web_hits),
                    "citations": [
                        hit.citation | {"title": hit.title, "score": hit.score} for hit in web_hits
                    ],
                },
                "modelSupplement": {
                    "used": result["answerMode"] in {"HYBRID", "MODEL_ONLY"},
                    "reason": decision.reason,
                },
                "degraded": bool(degraded_reasons),
                "degradedReasons": degraded_reasons,
                "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
                "timing": {"retrievalMs": retrieval_ms, "totalMs": total_ms},
            }
        except Exception as exc:
            await self.session.rollback()
            # 使用独立、重新加载的轨迹记录失败，避免原事务损坏后丢失诊断信息。
            failed_trace = await self.session.get(type(trace), trace.id)
            if failed_trace:
                await self.operations.finish_trace(
                    failed_trace,
                    status="failed",
                    error_code=exc.code
                    if isinstance(exc, ProviderUnavailableError)
                    else type(exc).__name__,
                    total_ms=round((time.perf_counter() - started) * 1000),
                )
                await self.session.commit()
            raise
