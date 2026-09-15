from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, HttpUrl

from knowledge_core.control.auth import require_admin
from knowledge_core.infrastructure.model_configuration import (
    get_model_configuration,
    model_configuration_view,
    save_model_configuration,
)
from knowledge_core.infrastructure.providers import get_chat_provider
from knowledge_core.shared.response import success
from knowledge_core.shared.schema import ApiSchema, ApiSnakeCaseSchema, ApiSuccess

router = APIRouter(
    prefix="/control/v1/platform/model-configuration",
    tags=["平台模型配置"],
    dependencies=[Depends(require_admin)],
)


class ModelConfigurationUpdate(ApiSchema):
    """管理员可写的平台模型设置。

    凭证字段只接收部署环境变量名，避免浏览器、审计日志和共享 JSON 接触实际 API Key。
    """

    chat_provider: Literal["disabled", "openai", "openai_compatible"]
    chat_model: str = Field(max_length=160)
    chat_base_url: HttpUrl
    chat_api_key_env: str = Field(min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$")
    embedding_provider: Literal["local_hash", "openai", "openai_compatible"]
    embedding_model: str = Field(max_length=160)
    embedding_base_url: HttpUrl
    embedding_api_key_env: str = Field(min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$")
    web_search_provider: Literal["disabled", "serper"]
    web_search_base_url: HttpUrl
    web_search_api_key_env: str = Field(min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$")


class ModelConfigurationResponse(ApiSnakeCaseSchema):
    """平台模型配置公开视图；密钥仅以是否已配置的布尔值呈现。"""

    chat_provider: Literal["disabled", "openai", "openai_compatible"]
    chat_model: str
    chat_base_url: str
    chat_api_key_env: str
    chat_api_key_configured: bool
    embedding_provider: Literal["local_hash", "openai", "openai_compatible"]
    embedding_model: str
    embedding_base_url: str
    embedding_api_key_env: str
    embedding_api_key_configured: bool
    web_search_provider: Literal["disabled", "serper"]
    web_search_base_url: str
    web_search_api_key_env: str
    web_search_api_key_configured: bool
    legacy_inline_credentials_detected: bool


class ChatModelTestResponse(ApiSchema):
    """平台 Chat 连通性探针的安全摘要。"""

    connected: bool
    input_tokens: int
    output_tokens: int


@router.get(
    "", response_model=ApiSuccess[ModelConfigurationResponse]
)
async def read_model_configuration() -> dict:
    return success(model_configuration_view(get_model_configuration()))


@router.put(
    "", response_model=ApiSuccess[ModelConfigurationResponse]
)
async def update_model_configuration(payload: ModelConfigurationUpdate) -> dict:
    values = payload.model_dump(mode="json")
    configuration = save_model_configuration(values)
    return success(model_configuration_view(configuration))


@router.post(
    "/test", response_model=ApiSuccess[ChatModelTestResponse]
)
async def test_chat_model() -> dict:
    result = await get_chat_provider().generate(
        [
            {"role": "system", "content": "只返回 JSON。"},
            {"role": "user", "content": '{"status":"ok"}'},
        ]
    )
    return success(
        {
            "connected": True,
            "inputTokens": result.input_tokens,
            "outputTokens": result.output_tokens,
        }
    )
