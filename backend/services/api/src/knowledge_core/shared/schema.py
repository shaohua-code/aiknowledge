from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiSchema(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class ApiSnakeCaseSchema(BaseModel):
    """历史响应数据的 snake_case Schema；仅用于不应被别名转换的既有字段。"""

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class ApiSuccess[SchemaData](ApiSchema):
    """控制面成功信封的 OpenAPI 约束，令前端能生成实际响应类型。"""

    success: Literal[True]
    request_id: str
    data: SchemaData
    meta: dict[str, Any] = {}
