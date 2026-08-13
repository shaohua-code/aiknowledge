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
from knowledge_core.shared.schema import ApiSchema

router = APIRouter(
    prefix="/control/v1/platform/model-configuration",
    tags=["平台模型配置"],
    dependencies=[Depends(require_admin)],
)


class ModelConfigurationUpdate(ApiSchema):
    chat_provider: Literal["disabled", "openai", "openai_compatible"]
    chat_model: str = Field(max_length=160)
    chat_base_url: HttpUrl
    chat_api_key: str | None = Field(default=None, max_length=500)
    embedding_provider: Literal["local_hash", "openai", "openai_compatible"]
    embedding_model: str = Field(max_length=160)
    embedding_base_url: HttpUrl
    embedding_api_key: str | None = Field(default=None, max_length=500)
    web_search_provider: Literal["disabled", "serper"]
    web_search_base_url: HttpUrl
    web_search_api_key: str | None = Field(default=None, max_length=500)


@router.get("")
async def read_model_configuration() -> dict:
    return success(model_configuration_view(get_model_configuration()))


@router.put("")
async def update_model_configuration(payload: ModelConfigurationUpdate) -> dict:
    values = payload.model_dump(mode="json")
    configuration = save_model_configuration(values)
    return success(model_configuration_view(configuration))


@router.post("/test")
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
