"""Webhook 事件通知测试。

验证事件创建、签名生成、签名验证和发送功能。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from knowledge_core.config import settings
from knowledge_core.infrastructure.webhooks import (
    WebhookConfig,
    WebhookEventType,
    WebhookSender,
    create_event,
    create_ingestion_completed_event,
    create_ingestion_failed_event,
    create_profile_published_event,
    load_configured_webhooks,
    notify_configured_webhooks,
    sign_payload,
    verify_signature,
)


class TestWebhookEventCreation:
    """Webhook 事件创建测试。"""

    def test_create_event_has_required_fields(self) -> None:
        """事件应包含所有必需字段。"""
        app_id = uuid4()
        env_id = uuid4()

        event = create_event(
            WebhookEventType.INGESTION_COMPLETED,
            app_id,
            env_id,
            {"runId": "run_123"},
        )

        assert event.id.startswith("evt_")
        assert event.type == WebhookEventType.INGESTION_COMPLETED
        assert event.application_id == app_id
        assert event.environment_id == env_id
        assert event.data["runId"] == "run_123"
        assert event.created_at is not None

    def test_event_to_dict_format(self) -> None:
        """事件字典应使用 camelCase 键名。"""
        event = create_event(
            WebhookEventType.PROFILE_PUBLISHED,
            uuid4(),
            uuid4(),
            {"profileId": "prof_123"},
        )

        data = event.to_dict()

        assert "applicationId" in data
        assert "environmentId" in data
        assert "createdAt" in data
        assert data["type"] == "profile.published"

    def test_event_to_json(self) -> None:
        """事件应能正确序列化为 JSON。"""
        event = create_event(
            WebhookEventType.EVALUATION_COMPLETED,
            uuid4(),
            uuid4(),
            {"gatePassed": True},
        )

        json_str = event.to_json()

        assert '"type": "evaluation.completed"' in json_str
        assert '"gatePassed": true' in json_str


class TestWebhookEventFactories:
    """Webhook 事件工厂函数测试。"""

    def test_ingestion_completed_event(self) -> None:
        """入库完成事件应包含正确数据。"""
        event = create_ingestion_completed_event(
            application_id=uuid4(),
            environment_id=uuid4(),
            run_id=uuid4(),
            document_id=uuid4(),
            version=1,
        )

        assert event.type == WebhookEventType.INGESTION_COMPLETED
        assert "runId" in event.data
        assert "documentId" in event.data
        assert event.data["version"] == 1

    def test_ingestion_failed_event(self) -> None:
        """入库失败事件应包含错误信息。"""
        event = create_ingestion_failed_event(
            application_id=uuid4(),
            environment_id=uuid4(),
            run_id=uuid4(),
            error_code="PARSE_ERROR",
            error_message="文档格式错误",
        )

        assert event.type == WebhookEventType.INGESTION_FAILED
        assert event.data["errorCode"] == "PARSE_ERROR"
        assert event.data["errorMessage"] == "文档格式错误"

    def test_profile_published_event(self) -> None:
        """策略发布事件应包含修订信息。"""
        event = create_profile_published_event(
            application_id=uuid4(),
            environment_id=uuid4(),
            profile_kind="answer",
            profile_id=uuid4(),
            revision_id=uuid4(),
            revision=3,
        )

        assert event.type == WebhookEventType.PROFILE_PUBLISHED
        assert event.data["profileKind"] == "answer"
        assert event.data["revision"] == 3


class TestWebhookSignature:
    """Webhook 签名测试。"""

    def test_sign_payload_format(self) -> None:
        """签名应以 sha256= 前缀开头。"""
        payload = '{"test": "data"}'
        secret = "test_secret"
        timestamp = 1731283200

        signature = sign_payload(payload, secret, timestamp)

        assert signature.startswith("sha256=")
        assert len(signature) == 7 + 64  # "sha256=" + 64 位十六进制

    def test_sign_payload_deterministic(self) -> None:
        """相同输入应产生相同签名。"""
        payload = '{"test": "data"}'
        secret = "test_secret"
        timestamp = 1731283200

        sig1 = sign_payload(payload, secret, timestamp)
        sig2 = sign_payload(payload, secret, timestamp)

        assert sig1 == sig2

    def test_sign_payload_different_for_different_secrets(self) -> None:
        """不同密钥应产生不同签名。"""
        payload = '{"test": "data"}'
        timestamp = 1731283200

        sig1 = sign_payload(payload, "secret1", timestamp)
        sig2 = sign_payload(payload, "secret2", timestamp)

        assert sig1 != sig2

    def test_verify_signature_valid(self) -> None:
        """有效签名应验证通过。"""
        payload = '{"test": "data"}'
        secret = "test_secret"
        timestamp = int(datetime.now(UTC).timestamp())

        signature = sign_payload(payload, secret, timestamp)

        assert verify_signature(payload, signature, str(timestamp), secret)

    def test_verify_signature_invalid(self) -> None:
        """篡改的签名应验证失败。"""
        payload = '{"test": "data"}'
        secret = "test_secret"
        timestamp = int(datetime.now(UTC).timestamp())

        signature = "sha256=invalid_signature"

        assert not verify_signature(payload, signature, str(timestamp), secret)

    def test_verify_signature_expired(self) -> None:
        """过期的时间戳应验证失败。"""
        payload = '{"test": "data"}'
        secret = "test_secret"
        # 10 分钟前的时间戳
        timestamp = int(datetime.now(UTC).timestamp()) - 600

        signature = sign_payload(payload, secret, timestamp)

        # 默认容差 5 分钟
        assert not verify_signature(payload, signature, str(timestamp), secret)

    def test_verify_signature_invalid_timestamp(self) -> None:
        """无效的时间戳格式应验证失败。"""
        payload = '{"test": "data"}'
        secret = "test_secret"

        assert not verify_signature(payload, "sha256=xxx", "not_a_number", secret)


class TestWebhookConfig:
    """Webhook 配置测试。"""

    def test_is_subscribed(self) -> None:
        """应正确判断是否订阅了事件。"""
        config = WebhookConfig(
            id=uuid4(),
            application_id=uuid4(),
            environment_id=uuid4(),
            url="https://example.com/webhook",
            secret="test_secret",
            events=[
                WebhookEventType.INGESTION_COMPLETED,
                WebhookEventType.PROFILE_PUBLISHED,
            ],
        )

        assert config.is_subscribed(WebhookEventType.INGESTION_COMPLETED)
        assert config.is_subscribed(WebhookEventType.PROFILE_PUBLISHED)
        assert not config.is_subscribed(WebhookEventType.INGESTION_FAILED)


class TestWebhookSender:
    """Webhook 发送器测试。"""

    @pytest.mark.asyncio
    async def test_skip_disabled_webhook(self) -> None:
        """禁用的 Webhook 应跳过发送。"""
        sender = WebhookSender()
        config = WebhookConfig(
            id=uuid4(),
            application_id=uuid4(),
            environment_id=uuid4(),
            url="https://example.com/webhook",
            secret="test_secret",
            events=[WebhookEventType.INGESTION_COMPLETED],
            enabled=False,
        )
        event = create_ingestion_completed_event(uuid4(), uuid4(), uuid4(), uuid4(), 1)

        delivery = await sender.send(config, event)

        assert delivery.status == "skipped"
        assert "已禁用" in delivery.error_message

    @pytest.mark.asyncio
    async def test_skip_unsubscribed_event(self) -> None:
        """未订阅的事件应跳过发送。"""
        sender = WebhookSender()
        config = WebhookConfig(
            id=uuid4(),
            application_id=uuid4(),
            environment_id=uuid4(),
            url="https://example.com/webhook",
            secret="test_secret",
            events=[WebhookEventType.PROFILE_PUBLISHED],  # 只订阅发布事件
            enabled=True,
        )
        event = create_ingestion_completed_event(uuid4(), uuid4(), uuid4(), uuid4(), 1)

        delivery = await sender.send(config, event)

        assert delivery.status == "skipped"
        assert "未订阅" in delivery.error_message

    @pytest.mark.asyncio
    async def test_retries_until_success(self) -> None:
        """短间隔内应重试，并在成功后停止。"""
        calls = {"count": 0}

        async def post(url: str, payload: str, headers: dict[str, str]):
            """前两次返回 500，第三次返回 200。"""
            _ = (url, payload, headers)
            calls["count"] += 1
            status = 500 if calls["count"] < 3 else 200
            return type("Response", (), {"status_code": status})()

        async def no_sleep(_: float) -> None:
            return None

        sender = WebhookSender(
            max_retries=3,
            retry_delays=[0, 0, 0],
            post=post,
            sleep=no_sleep,
        )
        config = WebhookConfig(
            id=uuid4(),
            application_id=uuid4(),
            environment_id=uuid4(),
            url="https://example.com/webhook",
            secret="test_secret",
            events=[WebhookEventType.INGESTION_COMPLETED],
        )
        event = create_ingestion_completed_event(uuid4(), uuid4(), uuid4(), uuid4(), 1)

        delivery = await sender.deliver_with_retries(config, event)

        assert delivery.status == "succeeded"
        assert calls["count"] == 3

    @pytest.mark.asyncio
    async def test_long_backoff_is_not_slept_inline(self) -> None:
        """超过主流程上限的重试间隔只记录时间，不在当前调用里等待。"""
        slept: list[float] = []

        async def post(url: str, payload: str, headers: dict[str, str]):
            _ = (url, payload, headers)
            return type("Response", (), {"status_code": 500})()

        async def record_sleep(delay: float) -> None:
            slept.append(delay)

        sender = WebhookSender(
            max_retries=3,
            retry_delays=[60, 300],
            immediate_retry_ceiling_seconds=2,
            post=post,
            sleep=record_sleep,
        )
        config = WebhookConfig(
            id=uuid4(),
            application_id=uuid4(),
            environment_id=uuid4(),
            url="https://example.com/webhook",
            secret="test_secret",
            events=[WebhookEventType.PROFILE_PUBLISHED],
        )
        event = create_profile_published_event(uuid4(), uuid4(), "retrieval", uuid4(), uuid4(), 2)

        delivery = await sender.deliver_with_retries(config, event)

        assert delivery.status == "failed"
        assert delivery.next_retry_at is not None
        assert slept == []


class TestWebhookConfigLoading:
    """运行配置中的订阅解析。"""

    def test_filters_by_application(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只返回匹配当前应用环境的订阅。"""
        application_id = uuid4()
        environment_id = uuid4()
        payload = json.dumps(
            [
                {
                    "url": "https://example.com/hook",
                    "secret": "s",
                    "events": ["ingestion.completed"],
                    "applicationId": str(application_id),
                    "environmentId": str(environment_id),
                }
            ]
        )
        monkeypatch.setattr(settings, "webhook_configs_json", payload)

        matched = load_configured_webhooks(application_id, environment_id)
        missed = load_configured_webhooks(uuid4(), environment_id)

        assert len(matched) == 1
        assert matched[0].events == [WebhookEventType.INGESTION_COMPLETED]
        assert missed == []

    @pytest.mark.asyncio
    async def test_notify_without_config_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """没有订阅时不发请求。"""
        monkeypatch.setattr(settings, "webhook_configs_json", "")
        event = create_ingestion_completed_event(uuid4(), uuid4(), uuid4(), uuid4(), 1)

        deliveries = await notify_configured_webhooks(event)

        assert deliveries == []
