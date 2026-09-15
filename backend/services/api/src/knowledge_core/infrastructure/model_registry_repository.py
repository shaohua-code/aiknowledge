"""模型注册表的异步读取仓储。

该模块只读取已经由迁移建立的非密钥目录、绑定与平台默认记录。它不接触旧 JSON、
不解析环境变量，也不负责把此读取路径切换为运行时权威来源；这些职责分别由迁移和
Provider 解析器在后续切换批次承担。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge_core.infrastructure.model_catalog import ModelCatalogEntry
from knowledge_core.infrastructure.models import (
    Application,
    ApplicationEnvironment,
    ApplicationModelBindingRecord,
    ModelCatalogRecord,
    PlatformModelDefaultRecord,
)
from knowledge_core.shared.errors import ConflictError, NotFoundError


def _entry_from_record(record: ModelCatalogRecord) -> ModelCatalogEntry:
    """将数据库非密钥记录映射为既有目录值对象，避免双份字段定义漂移。"""
    return ModelCatalogEntry(
        code=record.code,
        name=record.name,
        capability=record.capability,
        provider=record.provider,
        model=record.model,
        base_url=record.base_url,
        api_key_env=record.api_key_env,
        enabled=record.enabled,
        note=record.note,
    )


class ModelRegistryRepository:
    """按环境覆盖、应用默认和平台默认顺序读取模型注册表。

    所有方法要求调用方提供现有 AsyncSession，确保解析可与请求/任务已有事务边界
    对齐，不在基础设施层私自创建跨请求连接或提交事务。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_entries(self, capability: str | None = None) -> list[ModelCatalogEntry]:
        """读取目录条目；能力筛选在数据库执行，返回顺序按可读名称和编码稳定排列。"""
        statement = select(ModelCatalogRecord).order_by(
            ModelCatalogRecord.name, ModelCatalogRecord.code
        )
        if capability is not None:
            statement = statement.where(ModelCatalogRecord.capability == capability)
        records = list(await self.session.scalars(statement))
        return [_entry_from_record(record) for record in records]

    async def get_entry(self, code: str) -> ModelCatalogEntry | None:
        """按唯一编码读取目录条目，未找到时不泄漏或猜测其他目录记录。"""
        record = await self.session.scalar(
            select(ModelCatalogRecord).where(ModelCatalogRecord.code == code)
        )
        return _entry_from_record(record) if record is not None else None

    async def create_entry(self, entry: ModelCatalogEntry) -> ModelCatalogEntry:
        """创建非密钥目录条目，并在同一事务内拒绝重复业务编码。"""
        if await self.get_entry(entry.code) is not None:
            raise ConflictError(f"模型目录中已存在编码为 {entry.code} 的模型")
        record = ModelCatalogRecord(
            code=entry.code,
            name=entry.name,
            capability=entry.capability,
            provider=entry.provider,
            model=entry.model,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            enabled=entry.enabled,
            note=entry.note,
        )
        self.session.add(record)
        await self.session.flush()
        return _entry_from_record(record)

    async def update_entry(
        self, code: str, values: dict[str, object]
    ) -> ModelCatalogEntry:
        """按部分字段更新目录，并递增 revision 让后续并发控制可感知变更。"""
        record = await self._get_entry_record(code)
        for field, value in values.items():
            if field in {
                "name",
                "capability",
                "provider",
                "model",
                "base_url",
                "api_key_env",
                "enabled",
                "note",
            }:
                setattr(record, field, value)
        record.revision += 1
        await self.session.flush()
        return _entry_from_record(record)

    async def delete_entry(self, code: str) -> None:
        """只删除未被应用或平台默认引用的目录，拒绝破坏已生效配置。"""
        record = await self._get_entry_record(code)
        application_reference = await self.session.scalar(
            select(ApplicationModelBindingRecord.id).where(
                ApplicationModelBindingRecord.catalog_entry_id == record.id
            )
        )
        platform_reference = await self.session.scalar(
            select(PlatformModelDefaultRecord.capability).where(
                PlatformModelDefaultRecord.catalog_entry_id == record.id
            )
        )
        if application_reference is not None or platform_reference is not None:
            raise ConflictError("模型目录仍被应用绑定或平台默认引用，无法删除")
        await self.session.delete(record)
        await self.session.flush()

    async def read_application_binding(self, application_id: UUID) -> dict[str, str]:
        """读取应用默认绑定；环境覆盖由运行期解析器按请求上下文单独处理。"""
        rows = list(
            await self.session.execute(
                select(ApplicationModelBindingRecord.capability, ModelCatalogRecord.code)
                .join(
                    ModelCatalogRecord,
                    ModelCatalogRecord.id == ApplicationModelBindingRecord.catalog_entry_id,
                )
                .where(
                    ApplicationModelBindingRecord.application_id == application_id,
                    ApplicationModelBindingRecord.environment_id.is_(None),
                )
            )
        )
        return {capability: code for capability, code in rows}

    async def replace_application_binding(
        self, application_id: UUID, bindings: dict[str, str | None]
    ) -> dict[str, str]:
        """按能力更新应用默认绑定；空值仅解除该能力，不影响其他能力。"""
        application = await self.session.scalar(
            select(Application.id).where(Application.id == application_id)
        )
        if application is None:
            raise NotFoundError("应用不存在")
        return await self._replace_binding(application_id, None, bindings)

    async def read_environment_binding(
        self, application_id: UUID, environment_id: UUID
    ) -> dict[str, str]:
        """读取一个已归属应用的环境覆盖，拒绝用环境 UUID 越过应用边界。"""
        await self._ensure_environment(application_id, environment_id)
        return await self._read_binding(application_id, environment_id)

    async def replace_environment_binding(
        self,
        application_id: UUID,
        environment_id: UUID,
        bindings: dict[str, str | None],
    ) -> dict[str, str]:
        """按能力更新环境覆盖；它只覆盖当前环境，不改写应用默认绑定。"""
        await self._ensure_environment(application_id, environment_id)
        return await self._replace_binding(application_id, environment_id, bindings)

    async def read_platform_defaults(self) -> dict[str, str]:
        """读取每项能力的数据库平台默认目录编码，不回退或读取旧配置文件。"""
        rows = list(
            await self.session.execute(
                select(PlatformModelDefaultRecord.capability, ModelCatalogRecord.code).join(
                    ModelCatalogRecord,
                    ModelCatalogRecord.id == PlatformModelDefaultRecord.catalog_entry_id,
                )
            )
        )
        return {capability: code for capability, code in rows}

    async def replace_platform_defaults(
        self, bindings: dict[str, str | None]
    ) -> dict[str, str]:
        """按能力更新平台默认；空值移除默认，让尚未回填能力走兼容回退。"""
        existing = {
            item.capability: item
            for item in await self.session.scalars(select(PlatformModelDefaultRecord))
        }
        for capability, code in bindings.items():
            default = existing.get(capability)
            if code is None:
                if default is not None:
                    await self.session.delete(default)
                continue
            entry = await self._get_entry_record(code)
            if entry.capability != capability:
                raise ConflictError(
                    f"模型 {code} 的能力为 {entry.capability}，不能设为 {capability} 平台默认"
                )
            if not entry.enabled:
                raise ConflictError(f"模型 {code} 已停用，无法设为平台默认")
            if default is None:
                self.session.add(
                    PlatformModelDefaultRecord(
                        capability=capability, catalog_entry_id=entry.id
                    )
                )
            else:
                default.catalog_entry_id = entry.id
                default.revision += 1
        await self.session.flush()
        return await self.read_platform_defaults()

    async def _replace_binding(
        self,
        application_id: UUID,
        environment_id: UUID | None,
        bindings: dict[str, str | None],
    ) -> dict[str, str]:
        """复用默认/环境绑定写入逻辑，保证两层约束与 revision 语义一致。"""
        existing = list(
            await self.session.scalars(
                select(ApplicationModelBindingRecord).where(
                    ApplicationModelBindingRecord.application_id == application_id,
                    (
                        ApplicationModelBindingRecord.environment_id.is_(None)
                        if environment_id is None
                        else ApplicationModelBindingRecord.environment_id == environment_id
                    ),
                )
            )
        )
        existing_by_capability = {binding.capability: binding for binding in existing}
        for capability, code in bindings.items():
            binding = existing_by_capability.get(capability)
            if code is None:
                if binding is not None:
                    await self.session.delete(binding)
                continue
            entry = await self._get_entry_record(code)
            if entry.capability != capability:
                raise ConflictError(
                    f"模型 {code} 的能力为 {entry.capability}，不能绑定到 {capability}"
                )
            if not entry.enabled:
                raise ConflictError(f"模型 {code} 已停用，无法绑定")
            if binding is None:
                self.session.add(
                    ApplicationModelBindingRecord(
                        application_id=application_id,
                        environment_id=environment_id,
                        catalog_entry_id=entry.id,
                        capability=capability,
                    )
                )
            else:
                binding.catalog_entry_id = entry.id
                binding.revision += 1
        await self.session.flush()
        return await self._read_binding(application_id, environment_id)

    async def resolve_entry(
        self,
        application_id: UUID,
        environment_id: UUID | None,
        capability: str,
    ) -> ModelCatalogEntry | None:
        """按环境覆盖 → 应用默认 → 平台默认解析一个启用条目。

        绑定存在但目录条目已删除或停用时不静默回退到更低优先级，避免管理员误以为
        已明确配置的能力仍在使用另一模型；调用方可据此报告配置冲突。
        """
        if environment_id is not None:
            environment_entry = await self._binding_entry(
                application_id, environment_id, capability
            )
            if environment_entry is not None:
                return environment_entry

        application_entry = await self._binding_entry(application_id, None, capability)
        if application_entry is not None:
            return application_entry

        record = await self.session.scalar(
            select(ModelCatalogRecord)
            .join(
                PlatformModelDefaultRecord,
                PlatformModelDefaultRecord.catalog_entry_id == ModelCatalogRecord.id,
            )
            .where(PlatformModelDefaultRecord.capability == capability)
        )
        return _entry_from_record(record) if record is not None else None

    async def _binding_entry(
        self,
        application_id: UUID,
        environment_id: UUID | None,
        capability: str,
    ) -> ModelCatalogEntry | None:
        """读取指定层级的绑定条目；空环境只匹配应用默认，不混入环境覆盖。"""
        statement = (
            select(ModelCatalogRecord)
            .join(
                ApplicationModelBindingRecord,
                ApplicationModelBindingRecord.catalog_entry_id == ModelCatalogRecord.id,
            )
            .where(
                ApplicationModelBindingRecord.application_id == application_id,
                ApplicationModelBindingRecord.capability == capability,
            )
        )
        if environment_id is None:
            statement = statement.where(ApplicationModelBindingRecord.environment_id.is_(None))
        else:
            statement = statement.where(
                ApplicationModelBindingRecord.environment_id == environment_id
            )
        record = await self.session.scalar(statement)
        return _entry_from_record(record) if record is not None else None

    async def _get_entry_record(self, code: str) -> ModelCatalogRecord:
        """取得目录 ORM 记录，统一未找到的业务错误，避免控制层重复 SQL。"""
        record = await self.session.scalar(
            select(ModelCatalogRecord).where(ModelCatalogRecord.code == code)
        )
        if record is None:
            raise NotFoundError(f"模型目录中不存在编码为 {code} 的模型")
        return record

    async def _read_binding(
        self, application_id: UUID, environment_id: UUID | None
    ) -> dict[str, str]:
        """按指定层级读取绑定，避免环境覆盖混入应用默认的控制面响应。"""
        rows = list(
            await self.session.execute(
                select(ApplicationModelBindingRecord.capability, ModelCatalogRecord.code)
                .join(
                    ModelCatalogRecord,
                    ModelCatalogRecord.id == ApplicationModelBindingRecord.catalog_entry_id,
                )
                .where(
                    ApplicationModelBindingRecord.application_id == application_id,
                    (
                        ApplicationModelBindingRecord.environment_id.is_(None)
                        if environment_id is None
                        else ApplicationModelBindingRecord.environment_id == environment_id
                    ),
                )
            )
        )
        return {capability: code for capability, code in rows}

    async def _ensure_environment(self, application_id: UUID, environment_id: UUID) -> None:
        """确认环境确属目标应用，防止跨应用路径读写同一个环境覆盖。"""
        environment = await self.session.scalar(
            select(ApplicationEnvironment.id).where(
                ApplicationEnvironment.id == environment_id,
                ApplicationEnvironment.application_id == application_id,
            )
        )
        if environment is None:
            raise NotFoundError("应用环境不存在")
