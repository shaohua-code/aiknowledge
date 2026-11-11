"""Webhook 事件通知模块。

提供 Webhook 事件的创建、签名、发送和重试机制。
支持的事件类型：
- ingestion.completed：入库任务成功完成
- ingestion.failed：入库任务失败
- profile.published：策略发布成功
- profile.rolled_back：策略回滚
- evaluation.completed：评测运行完成

签名格式：
    X-Webhook-Signature: sha256={hmac_hex}
    X-Webhook-Timestamp: {unix_timestamp}

验证方式：
    signed_payload = f"{timestamp}.{payload_json}"
    expected = hmac.new(secret.encode(), signed_payload.encode(), 'sha256').hexdigest()
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger(__name__)


class WebhookEventType(StrEnum):
    """Webhook 事件类型枚举。"""

    INGESTION_COMPLETED = "ingestion.completed"
    INGESTION_FAILED = "ingestion.failed"
    PROFILE_PUBLISHED = "profile.published"
    PROFILE_ROLLED_BACK = "profile.rolled_back"
    EVALUATION_COMPLETED = "evaluation.completed"


@dataclass(slots=True)
class WebhookEvent:
    """Webhook 事件。"""

    id: str
    type: WebhookEventType
    application_id: UUID
    environment_id: UUID
    created_at: datetime
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化的字典。"""
        return {
            "id": self.id,
            "type": self.type.value,
            "applicationId": str(self.application_id),
            "environmentId": str(self.environment_id),
            "createdAt": self.created_at.isoformat(),
            "data": self.data,
        }

    def to_json(self) -> str:
        """转换为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(slots=True)
class WebhookConfig:
    """Webhook 配置。"""

    id: UUID
    application_id: UUID
    environment_id: UUID
    url: str
    secret: str
    events: list[WebhookEventType]
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_subscribed(self, event_type: WebhookEventType) -> bool:
        """检查是否订阅了指定事件类型。"""
        return event_type in self.events


@dataclass(slots=True)
class WebhookDelivery:
    """Webhook 投递记录。"""

    id: str
    webhook_id: UUID
    event_id: str
    attempt: int
    status: str  # pending, succeeded, failed
    status_code: int | None = None
    error_message: str | None = None
    delivered_at: datetime | None = None
    next_retry_at: datetime | None = None


def create_event(
    event_type: WebhookEventType,
    application_id: UUID,
    environment_id: UUID,
    data: dict[str, Any],
) -> WebhookEvent:
    """创建 Webhook 事件。

    Args:
        event_type: 事件类型
        application_id: 应用 ID
        environment_id: 环境 ID
        data: 事件数据

    Returns:
        WebhookEvent 实例
    """
    return WebhookEvent(
        id=f"evt_{uuid4().hex[:16]}",
        type=event_type,
        application_id=application_id,
        environment_id=environment_id,
        created_at=datetime.now(UTC),
        data=data,
    )


def sign_payload(payload: str, secret: str, timestamp: int) -> str:
    """生成 Webhook 签名。

    使用 HMAC-SHA256 签名，格式为 sha256={hex_digest}。

    Args:
        payload: 事件 JSON 字符串
        secret: Webhook 密钥
        timestamp: Unix 时间戳

    Returns:
        签名字符串，格式为 "sha256={hex}"
    """
    signed_payload = f"{timestamp}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def verify_signature(
    payload: str,
    signature: str,
    timestamp: str,
    secret: str,
    tolerance_seconds: int = 300,
) -> bool:
    """验证 Webhook 签名。

    Args:
        payload: 事件 JSON 字符串
        signature: 请求头中的签名
        timestamp: 请求头中的时间戳
        secret: Webhook 密钥
        tolerance_seconds: 时间戳容差（秒），默认 5 分钟

    Returns:
        签名是否有效
    """
    try:
        ts = int(timestamp)
    except ValueError:
        return False

    # 检查时间戳是否在容差范围内
    now = int(datetime.now(UTC).timestamp())
    if abs(now - ts) > tolerance_seconds:
        return False

    expected = sign_payload(payload, secret, ts)
    return hmac.compare_digest(expected, signature)


class WebhookSender:
    """Webhook 发送器。

    支持异步发送、自动重试和错误处理。
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delays: list[float] | None = None,
        immediate_retry_ceiling_seconds: float = 2.0,
        post: Any | None = None,
        sleep: Any | None = None,
    ):
        """初始化发送器。

        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大尝试次数，含首次
            retry_delays: 失败后的等待秒数，默认 [60, 300, 3600]
            immediate_retry_ceiling_seconds: 主流程内允许睡眠的上限。
                更长的间隔只记录 next_retry_at，避免入库或发布被长时间堵住。
            post: 可替换的发送函数，测试用来模拟 HTTP
            sleep: 可替换的异步睡眠，测试用来避免真实等待
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delays = retry_delays or [60.0, 300.0, 3600.0]
        self.immediate_retry_ceiling_seconds = immediate_retry_ceiling_seconds
        self._post = post
        self._sleep = sleep or asyncio.sleep

    async def send(
        self,
        config: WebhookConfig,
        event: WebhookEvent,
    ) -> WebhookDelivery:
        """发送 Webhook 事件。

        Args:
            config: Webhook 配置
            event: 要发送的事件

        Returns:
            投递记录
        """
        if not config.enabled:
            return WebhookDelivery(
                id=f"dlv_{uuid4().hex[:16]}",
                webhook_id=config.id,
                event_id=event.id,
                attempt=0,
                status="skipped",
                error_message="Webhook 已禁用",
            )

        if not config.is_subscribed(event.type):
            return WebhookDelivery(
                id=f"dlv_{uuid4().hex[:16]}",
                webhook_id=config.id,
                event_id=event.id,
                attempt=0,
                status="skipped",
                error_message=f"未订阅事件类型 {event.type.value}",
            )

        return await self._deliver_once(config, event, attempt=1)

    async def deliver_with_retries(
        self,
        config: WebhookConfig,
        event: WebhookEvent,
    ) -> WebhookDelivery:
        """在主流程允许的短间隔内重试。

        下一次等待超过 immediate_retry_ceiling_seconds 时停止，
        并把 next_retry_at 留给后续投递任务，不在当前请求里睡眠几分钟。
        禁用或未订阅的配置直接跳过，不发起 HTTP。
        """
        if not config.enabled or not config.is_subscribed(event.type):
            return await self.send(config, event)
        delivery = await self._deliver_once(config, event, attempt=1)
        attempt = 1
        while delivery.status == "failed" and attempt < self.max_retries:
            delay = self._delay_seconds(attempt)
            delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            if delay > self.immediate_retry_ceiling_seconds:
                return delivery
            if delay > 0:
                await self._sleep(delay)
            attempt += 1
            delivery = await self._deliver_once(config, event, attempt=attempt)
        if delivery.status == "failed" and attempt >= self.max_retries:
            delivery.next_retry_at = None
        return delivery

    async def _deliver_once(
        self,
        config: WebhookConfig,
        event: WebhookEvent,
        *,
        attempt: int,
    ) -> WebhookDelivery:
        """执行一次 HTTP 投递并记录结果。"""
        payload = event.to_json()
        timestamp = int(datetime.now(UTC).timestamp())
        signature = sign_payload(payload, config.secret, timestamp)
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "X-Webhook-Timestamp": str(timestamp),
            "X-Webhook-Event-Type": event.type.value,
            "X-Webhook-Event-Id": event.id,
        }
        delivery = WebhookDelivery(
            id=f"dlv_{uuid4().hex[:16]}",
            webhook_id=config.id,
            event_id=event.id,
            attempt=attempt,
            status="pending",
        )
        try:
            response = await self._post_payload(config.url, payload, headers)
            delivery.status_code = response.status_code
            if 200 <= response.status_code < 300:
                delivery.status = "succeeded"
                delivery.delivered_at = datetime.now(UTC)
            else:
                delivery.status = "failed"
                delivery.error_message = f"HTTP {response.status_code}"
                delivery.next_retry_at = self._calculate_next_retry(attempt)
        except httpx.TimeoutException:
            delivery.status = "failed"
            delivery.error_message = "请求超时"
            delivery.next_retry_at = self._calculate_next_retry(attempt)
        except httpx.RequestError as exc:
            delivery.status = "failed"
            delivery.error_message = f"请求错误: {type(exc).__name__}"
            delivery.next_retry_at = self._calculate_next_retry(attempt)
        except Exception as exc:
            delivery.status = "failed"
            delivery.error_message = f"未知错误: {type(exc).__name__}"
            logger.exception("Webhook 发送异常: %s", exc)
        return delivery

    async def _post_payload(self, url: str, payload: str, headers: dict[str, str]) -> Any:
        """发送 JSON。测试可注入 post，生产使用 httpx。"""
        if self._post is not None:
            return await self._post(url, payload, headers)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, content=payload, headers=headers)

    def _delay_seconds(self, attempt: int) -> float:
        """按已失败次数取等待秒数。attempt 从 1 开始。"""
        delay_index = min(max(attempt - 1, 0), len(self.retry_delays) - 1)
        return float(self.retry_delays[delay_index])

    def _calculate_next_retry(self, attempt: int) -> datetime | None:
        """计算下次重试时间。已达最大尝试次数时返回 None。"""
        if attempt >= self.max_retries:
            return None
        return datetime.now(UTC) + timedelta(seconds=self._delay_seconds(attempt))


# === 事件工厂函数 ===


def create_ingestion_completed_event(
    application_id: UUID,
    environment_id: UUID,
    run_id: UUID,
    document_id: UUID,
    version: int,
) -> WebhookEvent:
    """创建入库完成事件。"""
    return create_event(
        WebhookEventType.INGESTION_COMPLETED,
        application_id,
        environment_id,
        {
            "runId": str(run_id),
            "documentId": str(document_id),
            "version": version,
        },
    )


def create_ingestion_failed_event(
    application_id: UUID,
    environment_id: UUID,
    run_id: UUID,
    error_code: str,
    error_message: str,
) -> WebhookEvent:
    """创建入库失败事件。"""
    return create_event(
        WebhookEventType.INGESTION_FAILED,
        application_id,
        environment_id,
        {
            "runId": str(run_id),
            "errorCode": error_code,
            "errorMessage": error_message,
        },
    )


def create_profile_published_event(
    application_id: UUID,
    environment_id: UUID,
    profile_kind: str,
    profile_id: UUID,
    revision_id: UUID,
    revision: int,
) -> WebhookEvent:
    """创建策略发布事件。"""
    return create_event(
        WebhookEventType.PROFILE_PUBLISHED,
        application_id,
        environment_id,
        {
            "profileKind": profile_kind,
            "profileId": str(profile_id),
            "revisionId": str(revision_id),
            "revision": revision,
        },
    )


def create_profile_rolled_back_event(
    application_id: UUID,
    environment_id: UUID,
    profile_kind: str,
    profile_id: UUID,
    revision_id: UUID,
    revision: int,
) -> WebhookEvent:
    """创建策略回滚事件。"""
    return create_event(
        WebhookEventType.PROFILE_ROLLED_BACK,
        application_id,
        environment_id,
        {
            "profileKind": profile_kind,
            "profileId": str(profile_id),
            "revisionId": str(revision_id),
            "revision": revision,
        },
    )


def load_configured_webhooks(
    application_id: UUID,
    environment_id: UUID,
) -> list[WebhookConfig]:
    """从运行配置读取当前应用环境的 Webhook 订阅。

    WEBHOOK_CONFIGS_JSON 为空时返回空列表，调用方不发送任何请求。
    未填写 applicationId 的订阅对全部应用生效；填写后必须同时匹配环境。
    配置损坏时记录日志并返回空列表，避免影响入库和发布。
    """
    from knowledge_core.config import settings

    raw = (settings.webhook_configs_json or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("WEBHOOK_CONFIGS_JSON 不是合法 JSON，已跳过 Webhook")
        return []
    if not isinstance(parsed, list):
        logger.warning("WEBHOOK_CONFIGS_JSON 必须是数组，已跳过 Webhook")
        return []

    configs: list[WebhookConfig] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        secret = item.get("secret")
        if not isinstance(url, str) or not isinstance(secret, str) or not url or not secret:
            continue
        raw_events = item.get("events") or []
        events: list[WebhookEventType] = []
        for name in raw_events:
            try:
                events.append(WebhookEventType(str(name)))
            except ValueError:
                continue
        if not events:
            continue
        target_app = item.get("applicationId")
        target_env = item.get("environmentId")
        if target_app and str(target_app) != str(application_id):
            continue
        if target_env and str(target_env) != str(environment_id):
            continue
        raw_id = item.get("id")
        try:
            config_id = UUID(str(raw_id)) if raw_id else uuid4()
        except ValueError:
            config_id = uuid4()
        configs.append(
            WebhookConfig(
                id=config_id,
                application_id=application_id,
                environment_id=environment_id,
                url=url,
                secret=secret,
                events=events,
                enabled=bool(item.get("enabled", True)),
            )
        )
    return configs


async def dispatch_event(
    configs: list[WebhookConfig],
    event: WebhookEvent,
    sender: WebhookSender | None = None,
) -> list[WebhookDelivery]:
    """向一组订阅投递同一事件。单个订阅失败不影响其他订阅。"""
    sender = sender or WebhookSender()
    deliveries: list[WebhookDelivery] = []
    for config in configs:
        try:
            deliveries.append(await sender.deliver_with_retries(config, event))
        except Exception:
            logger.exception("Webhook 订阅 %s 投递失败", config.id)
    return deliveries


async def notify_configured_webhooks(event: WebhookEvent) -> list[WebhookDelivery]:
    """按当前配置投递事件。无订阅或异常时返回空列表，不向主流程抛错。"""
    try:
        configs = load_configured_webhooks(event.application_id, event.environment_id)
        if not configs:
            return []
        return await dispatch_event(configs, event)
    except Exception:
        logger.exception("Webhook 投递失败，已忽略以免影响主流程")
        return []


def create_evaluation_completed_event(
    application_id: UUID,
    environment_id: UUID,
    evaluation_set_id: UUID,
    run_id: UUID,
    gate_passed: bool,
    total_cases: int,
    passed_cases: int,
) -> WebhookEvent:
    """创建评测完成事件。"""
    return create_event(
        WebhookEventType.EVALUATION_COMPLETED,
        application_id,
        environment_id,
        {
            "evaluationSetId": str(evaluation_set_id),
            "runId": str(run_id),
            "gatePassed": gate_passed,
            "totalCases": total_cases,
            "passedCases": passed_cases,
        },
    )
