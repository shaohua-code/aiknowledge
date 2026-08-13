from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    """运行面唯一可信身份。

    上下文只能由 API Key 解析得到，业务请求中的 applicationId 或 environmentId
    不能覆盖这里的值。
    """

    application_id: UUID
    environment_id: UUID
    application_code: str
    environment_code: str
    api_key_id: UUID
    scopes: frozenset[str]

    def require(self, *required_scopes: str) -> None:
        missing = set(required_scopes).difference(self.scopes)
        if missing:
            from knowledge_core.shared.errors import ForbiddenError

            raise ForbiddenError(details={"missingScopes": sorted(missing)})
