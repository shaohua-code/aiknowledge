"""请求与响应 Pydantic 模型定义。

对应 SubTask 7：项目、API Key、知识库管理接口所需的请求体与响应体模型集中定义，
便于在 FastAPI 端点中复用并自动生成 OpenAPI 文档。

设计要点
--------
1. 所有字段使用 ``Field(..., description="...")`` 中文描述，便于 OpenAPI 文档展示。
2. 响应模型中敏感字段（如 API Key 明文）显式标注 ``Optional`` 与注释，
   避免误用与泄露。
3. 请求模型中 ``status`` 字段使用 ``Literal`` 限定枚举值，杜绝非法状态进入系统。
4. 创建类模型不包含 ``id`` / ``createdAt`` 等服务端生成字段，
   响应类模型才包含这些字段。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 项目管理相关模型
# ============================================================================

# 项目状态枚举：active=启用 / disabled=停用
ProjectStatus = Literal["active", "disabled"]


class ProjectCreateRequest(BaseModel):
    """创建项目请求体。

    用于 ``POST /api/v1/projects``，仅管理密钥可调用。
    ``code`` 创建后不可改，且全局唯一（CIText 大小写不敏感）。

    Attributes:
        code: 项目编码，3-32 字符，仅小写字母+数字+连字符；创建后不可改。
        name: 项目显示名，1-100 字符。
        description: 项目描述，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "code": "ai-fund",
            "name": "AI 基金",
            "description": "基金智能研究与决策",
        }
    })

    # 项目编码：格式校验由端点单独实现（正则），这里仅做长度校验
    code: str = Field(
        ...,
        min_length=3,
        max_length=32,
        description="项目编码（3-32 字符，小写字母+数字+连字符，创建后不可改）",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="项目显示名",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="项目描述，可空",
    )


class ProjectUpdateRequest(BaseModel):
    """编辑项目请求体。

    用于 ``PATCH /api/v1/projects/{projectId}``，仅允许修改 name/description/status，
    不允许修改 ``code``（code 创建后不可改）。

    Attributes:
        name: 项目显示名，可空。
        description: 项目描述，可空。
        status: 项目状态，可空，``active`` 或 ``disabled``。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "AI 基金（已上线）",
            "description": "基金智能研究与决策平台",
            "status": "active",
        }
    })

    # 全部可选：PATCH 语义，仅更新传入字段
    name: str | None = Field(default=None, min_length=1, max_length=100, description="项目显示名")
    description: str | None = Field(
        default=None, max_length=2000, description="项目描述"
    )
    status: ProjectStatus | None = Field(default=None, description="项目状态：active / disabled")


class ProjectResponse(BaseModel):
    """项目响应体。

    用于项目相关接口的响应数据，包含服务端生成的 ``id`` 与 ``createdAt``。

    Attributes:
        id: 项目主键（UUID）。
        code: 项目编码（创建后不可改）。
        name: 项目显示名。
        description: 项目描述。
        status: 项目状态。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="项目 ID（UUID）")
    code: str = Field(..., description="项目编码")
    name: str = Field(..., description="项目显示名")
    description: str | None = Field(default=None, description="项目描述")
    status: str = Field(..., description="项目状态：active / disabled")
    createdAt: datetime = Field(..., description="创建时间")


# ============================================================================
# API Key 管理相关模型
# ============================================================================

# API Key 环境枚举：dev=开发 / staging=预发 / production=生产 / collector=采集器
ApiKeyEnvironment = Literal["dev", "staging", "production", "collector"]
# API Key 状态枚举：active=启用 / revoked=已吊销
ApiKeyStatus = Literal["active", "revoked"]


class ApiKeyCreateRequest(BaseModel):
    """创建 API Key 请求体。

    用于 ``POST /api/v1/projects/{projectId}/api-keys``。
    明文 Key 由服务端生成，仅此一次返回给客户端，后续无法再次获取。

    Attributes:
        name: Key 显示名，便于后台识别。
        environment: 环境，``dev`` / ``staging`` / ``production`` / ``collector``。
        scopes: 权限范围数组，如 ``["retrieval:read", "research:run"]``。
        expiresAt: 过期时间（ISO8601），可空表示永不过期。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "生产环境调用方",
            "environment": "production",
            "scopes": ["retrieval:read", "research:run"],
            "expiresAt": "2027-01-01T00:00:00Z",
        }
    })

    name: str = Field(..., min_length=1, max_length=120, description="Key 显示名")
    environment: ApiKeyEnvironment = Field(..., description="环境：dev / staging / production / collector")
    scopes: list[str] = Field(
        default_factory=list,
        description="权限范围数组，如 ['retrieval:read', 'research:run']",
    )
    expiresAt: datetime | None = Field(
        default=None,
        description="过期时间（ISO8601），空表示永不过期",
    )


class ApiKeyResponse(BaseModel):
    """API Key 响应体。

    创建与轮换接口返回时携带 ``plaintextKey``（仅展示一次）；
    列表查询接口不返回 ``plaintextKey``（始终为 None）。

    Attributes:
        id: API Key 主键。
        name: Key 显示名。
        environment: 环境。
        scopes: 权限范围数组。
        keyPrefix: Key 前缀（前 12 位），用于后台识别。
        plaintextKey: 明文 Key，仅在创建/轮换时返回一次，其余场景为 None。
        lastUsedAt: 最近使用时间，可空。
        expiresAt: 过期时间，可空表示永不过期。
        status: 状态：active / revoked。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="API Key ID")
    name: str = Field(..., description="Key 显示名")
    environment: str = Field(..., description="环境")
    scopes: list[str] = Field(default_factory=list, description="权限范围数组")
    keyPrefix: str = Field(..., description="Key 前缀（前 12 位），用于后台识别")
    # 明文 Key：仅在创建/轮换时返回一次，列表查询时为 None
    # 客户端必须立即保存，关闭页面后无法再次获取
    plaintextKey: str | None = Field(
        default=None,
        description="明文 Key（仅展示一次，列表查询不返回）",
    )
    lastUsedAt: datetime | None = Field(default=None, description="最近使用时间")
    expiresAt: datetime | None = Field(default=None, description="过期时间，空表示永不过期")
    status: str = Field(..., description="状态：active / revoked")
    createdAt: datetime = Field(..., description="创建时间")


# ============================================================================
# 知识库管理相关模型
# ============================================================================

# 知识库状态枚举：active=启用 / disabled=停用
KnowledgeBaseStatus = Literal["active", "disabled"]


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求体。

    用于 ``POST /api/v1/knowledge-bases``。
    ``code`` 项目内唯一（CIText 大小写不敏感），不同项目可有相同 code。
    ``embeddingDimension`` 不传则使用项目设置的默认值。

    Attributes:
        code: 知识库编码，项目内唯一。
        name: 知识库名称。
        description: 知识库描述，可空。
        embeddingModel: Embedding 模型名称，创建后不可改。
        embeddingDimension: 向量维度，创建后不可改；不传则用项目设置默认值。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "code": "fund-research",
            "name": "基金研究报告",
            "description": "基金公司发布的研报与市场分析",
            "embeddingModel": "text-embedding-3-small",
            "embeddingDimension": 1536,
        }
    })

    code: str = Field(
        ...,
        min_length=3,
        max_length=32,
        description="知识库编码（项目内唯一，小写字母+数字+连字符）",
    )
    name: str = Field(..., min_length=1, max_length=120, description="知识库名称")
    description: str | None = Field(default=None, max_length=2000, description="知识库描述")
    embeddingModel: str | None = Field(
        default=None, max_length=120, description="Embedding 模型名称，创建后不可改"
    )
    embeddingDimension: int | None = Field(
        default=None,
        ge=1,
        le=8192,
        description="向量维度，创建后不可改；不传则用项目设置默认值",
    )


class KnowledgeBaseUpdateRequest(BaseModel):
    """编辑知识库请求体。

    用于 ``PATCH /api/v1/knowledge-bases/{code}``。
    不允许修改 ``code`` 与 ``embeddingDimension``（维度变更需重建知识库）。

    Attributes:
        name: 知识库名称，可空。
        description: 知识库描述，可空。
        status: 知识库状态，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "基金研究报告（更新）",
            "description": "更新后的描述",
            "status": "active",
        }
    })

    name: str | None = Field(default=None, min_length=1, max_length=120, description="知识库名称")
    description: str | None = Field(default=None, max_length=2000, description="知识库描述")
    status: KnowledgeBaseStatus | None = Field(
        default=None, description="知识库状态：active / disabled"
    )


class KnowledgeBaseResponse(BaseModel):
    """知识库响应体。

    用于知识库相关接口的响应数据。
    ``documentCount`` 字段由 Repository 统计填充，便于客户端展示文档数。

    Attributes:
        id: 知识库 ID。
        code: 知识库编码。
        name: 知识库名称。
        description: 知识库描述。
        embeddingModel: Embedding 模型名称。
        embeddingDimension: 向量维度。
        status: 知识库状态。
        documentCount: 文档数量，仅在列表查询中填充，详情查询可为 None。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="知识库 ID")
    code: str = Field(..., description="知识库编码")
    name: str = Field(..., description="知识库名称")
    description: str | None = Field(default=None, description="知识库描述")
    embeddingModel: str | None = Field(default=None, description="Embedding 模型名称")
    embeddingDimension: int | None = Field(default=None, description="向量维度")
    status: str = Field(..., description="知识库状态：active / disabled")
    # 文档数：列表查询时由 Repository 统计填充，详情查询时为 None
    documentCount: int | None = Field(default=None, description="文档数量")
    createdAt: datetime = Field(..., description="创建时间")


# ============================================================================
# 文档导入相关模型（SubTask 8）
# ============================================================================

# 文档写入类型枚举：TEXT=手动录入文本 / URL=URL 抓取
DocumentCreateType = Literal["TEXT", "URL"]

# 文档处理状态枚举（对外大写形式，对应内部小写处理状态）
# 内部状态 pending / parsing / chunking / embedding / ready / failed
# 对外状态 PENDING / PARSING / CHUNKING / EMBEDDING / READY / FAILED
DocumentProcessingStatus = Literal[
    "PENDING",
    "PARSING",
    "CHUNKING",
    "EMBEDDING",
    "READY",
    "FAILED",
]


class DocumentCreateRequest(BaseModel):
    """文档创建请求体（文本/URL 写入接口，SubTask 8.2）。

    用于 ``POST /api/v1/knowledge-bases/{code}/documents``。
    文件上传接口（8.1）使用 multipart/form-data，不使用此模型。

    Attributes:
        type: 写入类型，``TEXT`` 或 ``URL``。
        title: 文档标题。
        content: 正文内容，``type=TEXT`` 时必填。
        url: 抓取 URL，``type=URL`` 时必填。
        externalId: 业务项目稳定资源 ID，可空，用于外部系统幂等。
        tags: 标签列表，可空。
        metadata: 元数据，可空，存储页数/作者等扩展信息。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "type": "TEXT",
            "title": "基金投资策略",
            "content": "本文档介绍基金投资策略...",
            "tags": ["基金", "投资"],
            "metadata": {"author": "研究员A"},
        }
    })

    type: DocumentCreateType = Field(
        ...,
        description="写入类型：TEXT=手动录入文本 / URL=URL 抓取",
    )
    title: str = Field(..., min_length=1, max_length=500, description="文档标题")
    content: str | None = Field(
        default=None,
        max_length=2_000_000,
        description="正文内容，type=TEXT 时必填",
    )
    url: str | None = Field(
        default=None,
        max_length=2000,
        description="抓取 URL，type=URL 时必填",
    )
    externalId: str | None = Field(
        default=None,
        max_length=120,
        description="业务项目稳定资源 ID，可空，用于外部系统幂等",
    )
    tags: list[str] | None = Field(
        default=None,
        description="标签列表，可空",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="元数据，可空，存储页数/作者等扩展信息",
    )


class DocumentImportResponse(BaseModel):
    """文档导入响应体（文件上传与文本/URL 写入接口共用，SubTask 8.1/8.2）。

    返回新建文档 ID、入库任务 ID 与初始状态，客户端可通过
    ``GET /api/v1/documents/{documentId}`` 轮询处理进度。

    Attributes:
        documentId: 文档主键（UUID）。
        ingestionTaskId: 入库任务 ID，用于关联 IngestionJob。
        status: 初始处理状态，固定为 ``PENDING``。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "documentId": "d8f3b2a1-1234-5678-9abc-def012345678",
            "ingestionTaskId": "e9f4c3b2-1234-5678-9abc-def012345678",
            "status": "PENDING",
        }
    })

    documentId: str = Field(..., description="文档 ID（UUID）")
    ingestionTaskId: str = Field(..., description="入库任务 ID")
    status: str = Field(..., description="处理状态：PENDING")


class DocumentResponse(BaseModel):
    """文档详情响应体（SubTask 8.3）。

    用于 ``GET /api/v1/documents/{documentId}``，返回文档当前状态、
    所属知识库 code、分块数等关键信息。

    Attributes:
        documentId: 文档主键。
        title: 文档标题。
        sourceType: 来源类型，file / url / manual / crawler。
        processingStatus: 处理状态（大写对外形式），PENDING / PARSING / ... / FAILED。
        ingestionTaskId: 入库任务 ID。
        knowledgeBaseCode: 所属知识库 code，便于客户端关联。
        chunkCount: 分块数，处理完成后反映向量化分块总数。
        enabled: 是否参与检索。
        createdAt: 创建时间。
        updatedAt: 最近更新时间。
    """

    model_config = ConfigDict(from_attributes=True)

    documentId: str = Field(..., description="文档 ID")
    title: str = Field(..., description="文档标题")
    sourceType: str = Field(..., description="来源类型：file / url / manual / crawler")
    processingStatus: str = Field(
        ..., description="处理状态：PENDING / PARSING / CHUNKING / EMBEDDING / READY / FAILED"
    )
    ingestionTaskId: str | None = Field(
        default=None, description="入库任务 ID，未关联时为 None"
    )
    knowledgeBaseCode: str = Field(..., description="所属知识库 code")
    chunkCount: int = Field(default=0, description="分块数")
    enabled: bool = Field(default=True, description="是否参与检索")
    createdAt: datetime = Field(..., description="创建时间")
    updatedAt: datetime = Field(..., description="最近更新时间")


# ============================================================================
# 检索相关模型（Task 10）
# ============================================================================
class RetrievalSearchRequest(BaseModel):
    """纯检索请求体（Task 10.4，``POST /api/v1/retrieval/search``）。

    用于触发一次混合检索（全文 + 向量 + RRF 合并），仅返回知识片段，
    不调用聊天模型，便于业务方做自定义拼接与重排。

    Attributes:
        query: 查询文本，将同时用于全文检索与生成 Embedding 向量。
        knowledgeBaseIds: 参与检索的知识库 ID 列表（必须属于当前项目）。
            空列表时返回空结果。
        topK: 返回结果数，默认 5，范围 [1, 20]。候选池固定 30，
            ``topK`` 仅作用于 RRF 合并后的截断。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "基金投资策略",
            "knowledgeBaseIds": ["d8f3b2a1-1234-5678-9abc-def012345678"],
            "topK": 5,
        }
    })

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="查询文本，将同时用于全文检索与生成 Embedding 向量",
    )
    knowledgeBaseIds: list[str] = Field(
        ...,
        min_length=1,
        description="知识库 ID 列表（必须属于当前项目），空列表返回空结果",
    )
    topK: int = Field(
        default=5,
        ge=1,
        le=20,
        description="返回结果数，默认 5，范围 [1, 20]",
    )


# ============================================================================
# 提示词版本管理相关模型（Task 14）
# ============================================================================
class PromptCreateRequest(BaseModel):
    """创建提示词版本请求体。

    用于 ``POST /api/v1/prompts``。版本号由服务端自动递增（当前项目最大版本 + 1），
    客户端无法指定。``activateImmediately=true`` 时创建后立即激活该版本，
    替换原 active 版本。

    为什么版本号由服务端生成？
        版本号是项目内递增的整数，需保证唯一性。若由客户端指定，
        并发创建时易产生冲突或跳号，且客户端无法感知当前最大版本号。
        服务端在事务内查询 ``MAX(version)`` 并 +1，保证单调递增。

    Attributes:
        systemPrompt: 系统提示词，定义大模型角色与行为约束，必填。
        evidenceRules: 证据使用规则，约束大模型如何引用与裁剪证据，必填。
        outputSchema: 输出 JSON Schema，约束大模型返回结构（结论/建议/不确定性等），必填。
        prohibitions: 禁止事项，约束大模型不可输出的内容（如投资建议、绝对结论），必填。
        riskTemplate: 风险提示模板，附加到回答末尾，必填。
        activateImmediately: 是否创建后立即激活，默认 False。
            True 时调用 ``set_active`` 在事务内切换 active 版本。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "systemPrompt": "你是一位严谨的基金研究员...",
            "evidenceRules": "仅引用证据中的事实，不得虚构数据...",
            "outputSchema": {
                "type": "object",
                "properties": {
                    "conclusions": {"type": "array"},
                    "suggestedActions": {"type": "array"},
                    "confidence": {"type": "number"},
                },
                "required": ["conclusions"],
            },
            "prohibitions": "禁止承诺收益、禁止提供具体投资建议...",
            "riskTemplate": "以上内容仅供参考，不构成投资建议。",
            "activateImmediately": True,
        }
    })

    systemPrompt: str = Field(
        ...,
        min_length=1,
        description="系统提示词，定义大模型角色与行为约束",
    )
    evidenceRules: str = Field(
        ...,
        min_length=1,
        description="证据使用规则，约束大模型如何引用与裁剪证据",
    )
    outputSchema: dict[str, Any] = Field(
        ...,
        description="输出 JSON Schema，约束大模型返回结构（结论/建议/不确定性等）",
    )
    prohibitions: str = Field(
        ...,
        min_length=1,
        description="禁止事项，约束大模型不可输出的内容",
    )
    riskTemplate: str = Field(
        ...,
        min_length=1,
        description="风险提示模板，附加到回答末尾",
    )
    activateImmediately: bool = Field(
        default=False,
        description="是否创建后立即激活该版本，默认 False",
    )


class PromptUpdateRequest(BaseModel):
    """编辑提示词版本请求体。

    用于 ``PATCH /api/v1/prompts/{versionId}``。

    为什么 active 版本不允许直接编辑？
        active 版本可能正在被研究任务使用，直接修改会导致：
        1. 历史任务的复现性被破坏（同样问题再次运行得到不同结果）
        2. 正在进行的任务读到部分更新的字段，状态不一致
        因此 active 版本应通过"创建新版本 → 激活新版本"的方式迭代，
        保留旧版本用于历史任务的版本号追溯。本接口仅允许编辑非 active 版本，
        便于用户在版本"草稿态"时修正内容。

    Attributes:
        systemPrompt: 系统提示词，可空。
        evidenceRules: 证据使用规则，可空。
        outputSchema: 输出 JSON Schema，可空。
        prohibitions: 禁止事项，可空。
        riskTemplate: 风险提示模板，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "systemPrompt": "你是一位严谨的基金研究员（v2 修订）...",
            "prohibitions": "禁止承诺收益、禁止提供具体投资建议、禁止预测涨跌...",
        }
    })

    # 全部可选：PATCH 语义，仅更新传入字段
    systemPrompt: str | None = Field(default=None, min_length=1, description="系统提示词")
    evidenceRules: str | None = Field(default=None, min_length=1, description="证据使用规则")
    outputSchema: dict[str, Any] | None = Field(default=None, description="输出 JSON Schema")
    prohibitions: str | None = Field(default=None, min_length=1, description="禁止事项")
    riskTemplate: str | None = Field(default=None, min_length=1, description="风险提示模板")


class PromptResponse(BaseModel):
    """提示词版本响应体。

    用于提示词相关接口的响应数据，包含完整字段与服务端生成的 ``id`` / ``createdAt``。

    Attributes:
        id: 提示词版本 ID（UUID）。
        version: 版本号，项目内递增。
        isActive: 是否当前启用版本。每项目仅一个为 true。
        systemPrompt: 系统提示词。
        evidenceRules: 证据使用规则。
        outputSchema: 输出 JSON Schema。
        prohibitions: 禁止事项。
        riskTemplate: 风险提示模板。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="提示词版本 ID（UUID）")
    version: int = Field(..., description="版本号，项目内递增")
    isActive: bool = Field(..., description="是否当前启用版本")
    systemPrompt: str = Field(..., description="系统提示词")
    evidenceRules: str = Field(..., description="证据使用规则")
    outputSchema: dict[str, Any] = Field(..., description="输出 JSON Schema")
    prohibitions: str = Field(..., description="禁止事项")
    riskTemplate: str = Field(..., description="风险提示模板")
    createdAt: datetime = Field(..., description="创建时间")


class PromptListItemResponse(BaseModel):
    """提示词版本列表项响应体（精简版）。

    用于 ``GET /api/v1/prompts`` 列表接口，``systemPrompt`` 截断为 50 字，
    便于客户端在列表中快速预览，详情需调用 ``GET /api/v1/prompts/{versionId}``。

    Attributes:
        id: 提示词版本 ID。
        version: 版本号。
        isActive: 是否当前启用版本。
        systemPrompt: 系统提示词摘要（截断 50 字）。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="提示词版本 ID")
    version: int = Field(..., description="版本号")
    isActive: bool = Field(..., description="是否当前启用版本")
    systemPrompt: str = Field(..., description="系统提示词摘要（截断 50 字）")
    createdAt: datetime = Field(..., description="创建时间")


# ============================================================================
# 短链路研究相关模型（Task 15）
# ============================================================================
# 研究输出类型枚举：narrative=叙述性段落 / json=严格 JSON / bullet_points=要点列表
ResearchOutputType = Literal["narrative", "json", "bullet_points"]

# 研究策略枚举：决定启用哪些证据源
# - knowledge_only: 仅内部检索
# - knowledge_web: 内部检索 + 联网搜索
# - knowledge_tools: 内部检索 + 工具调用
# - full: 三路全开（默认）
ResearchStrategy = Literal[
    "knowledge_only", "knowledge_web", "knowledge_tools", "full"
]


class ResearchRunRequest(BaseModel):
    """短链路研究请求体（Task 15.4，``POST /api/v1/research/run``）。

    触发一次完整研究链路：并行取证（内部检索 + 联网搜索 + 工具调用）
    + 一次模型生成。整体硬超时 15 秒（``settings.research_hard_timeout_seconds``），
    超时返回降级结果（已整理证据 + degraded=true）。

    设计要点
    --------
    1. ``strategy`` 决定启用哪些证据源：
       - ``knowledge_only``：仅内部检索，适用于纯知识库问答（延迟最低）
       - ``knowledge_web``：内部 + 联网，补充时效性信息
       - ``knowledge_tools``：内部 + 工具，获取实时结构化数据
       - ``full``：三路全开，覆盖最全面（延迟最高）
    2. ``toolCodes`` 最多取前 3 个（``MAX_TOOLS_PER_RUN``），
       避免单次研究 token 与延迟爆炸。
    3. ``knowledgeBaseIds`` 必须属于当前项目，由 HybridSearcher 在 SQL 层
       通过 ``project_id`` 前置过滤保证跨项目隔离。

    Attributes:
        question: 用户问题原文，将作为内部检索与联网搜索的查询文本。
        outputType: 期望输出类型，``narrative`` / ``json`` / ``bullet_points``。
            影响提示词中的输出格式提示。
        strategy: 研究策略，决定启用哪些证据源。默认 ``full``。
        knowledgeBaseIds: 参与检索的知识库 ID 列表（必须属于当前项目）。
            空列表时内部检索跳过，仅依赖联网与工具证据。
        toolCodes: 请求调用的工具 code 列表，最多取前 3 个。
            超过 3 个的部分被截断（``MAX_TOOLS_PER_RUN``）。
        toolInputs: 工具入参字典，key 为 tool_code，value 为入参 dict。
            未提供入参的工具以空 dict 调用。
        context: 输入上下文（如会话历史、用户画像），可空。
            会拼接到 user_message 中供模型参考。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "question": "2024 年基金市场表现如何？",
            "outputType": "narrative",
            "strategy": "full",
            "knowledgeBaseIds": ["d8f3b2a1-1234-5678-9abc-def012345678"],
            "toolCodes": ["fund_market"],
            "toolInputs": {"fund_market": {"fund_code": "000001"}},
            "context": {"session_id": "sess_xxx"},
        }
    })

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="用户问题原文，将作为内部检索与联网搜索的查询文本",
    )
    outputType: ResearchOutputType = Field(
        default="narrative",
        description="期望输出类型：narrative / json / bullet_points",
    )
    strategy: ResearchStrategy = Field(
        default="full",
        description="研究策略：knowledge_only / knowledge_web / knowledge_tools / full",
    )
    knowledgeBaseIds: list[str] = Field(
        default_factory=list,
        description="参与检索的知识库 ID 列表（必须属于当前项目），空列表时跳过内部检索",
    )
    toolCodes: list[str] = Field(
        default_factory=list,
        description="请求调用的工具 code 列表，最多取前 3 个",
    )
    toolInputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="工具入参字典，key 为 tool_code，value 为入参 dict",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="输入上下文（如会话历史、用户画像），可空",
    )


# ============================================================================
# 异步研究任务与反馈相关模型（Task 16）
# ============================================================================
# 异步任务对外状态枚举（大写形式，区别于内部小写状态）
# 内部状态：pending / running / success / partial_success / failed / timeout
# 对外状态：PENDING / RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / TIMEOUT
ResearchJobStatus = Literal[
    "PENDING",
    "RUNNING",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "TIMEOUT",
]

# 反馈评分枚举：helpful=有用 / partially_helpful=部分有用 / not_helpful=无用
FeedbackRating = Literal["helpful", "partially_helpful", "not_helpful"]


class ResearchJobResponse(BaseModel):
    """异步研究任务提交响应体（Task 16.2，``POST /api/v1/research/jobs``）。

    提交异步研究任务后返回 jobId 与轮询地址，客户端通过 ``statusUrl``
    轮询任务状态。

    为什么异步任务需要返回 statusUrl？
        异步任务的最终结果在 Worker 执行完成后才生成，客户端无法在一次请求中
        拿到完整结果。返回 ``statusUrl`` 让客户端有明确的轮询入口，
        避免自行拼接 URL（防止路径变更导致的客户端改造）。

    Attributes:
        jobId: 异步研究任务 ID（即 ResearchTask.id），用于查询任务状态。
        status: 初始状态，固定为 ``PENDING``（任务已入队，等待 Worker 领取）。
        statusUrl: 任务状态查询地址，客户端轮询此 URL 获取最终结果。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "jobId": "a1b2c3d4-1234-5678-9abc-def012345678",
            "status": "PENDING",
            "statusUrl": "/api/v1/research/jobs/a1b2c3d4-1234-5678-9abc-def012345678",
        }
    })

    jobId: str = Field(..., description="异步研究任务 ID（即 ResearchTask.id）")
    status: str = Field(..., description="初始状态，固定为 PENDING")
    statusUrl: str = Field(..., description="任务状态查询地址")


class ResearchJobStatusResponse(BaseModel):
    """异步研究任务状态查询响应体（Task 16.2，``GET /api/v1/research/jobs/{jobId}``）。

    返回任务当前状态、关键时间戳与最终结果。``result`` 仅在
    ``status=SUCCESS`` 或 ``status=PARTIAL_SUCCESS`` 时填充，
    其余状态为 None。

    状态机流转说明
    --------------
    1. PENDING：任务已入队，等待 Worker 领取（``started_at`` 为空）
    2. RUNNING：Worker 已领取并开始执行（``started_at`` 已填充）
    3. SUCCESS：研究成功完成，``result`` 含完整结论（``completed_at`` 已填充）
    4. PARTIAL_SUCCESS：研究降级完成（如联网搜索超时），``result`` 含部分结论
    5. FAILED：研究失败（如证据不足、模型异常），``errorCode`` 含错误码
    6. TIMEOUT：任务整体超时（Celery soft_time_limit 触发）

    为什么 PARTIAL_SUCCESS 也要返回 result？
        降级场景下证据已收集但模型生成可能失败，客户端仍可基于证据做决策，
        因此需返回 result（含 evidence 字段）让客户端能利用已收集的证据。

    Attributes:
        jobId: 任务 ID。
        status: 任务状态（大写形式）。
        question: 用户问题原文，便于客户端在轮询列表中识别任务。
        startedAt: 任务开始时间（ISO8601），PENDING 状态为 None。
        completedAt: 任务完成时间（ISO8601），未完成时为 None。
        totalDurationMs: 任务总耗时（毫秒），未完成时为 None。
        degraded: 是否降级。True 表示取证或生成环节出现部分失败。
        degradedReasons: 降级原因列表，如 ``["web_search_timeout"]``。
        errorCode: 失败错误码（大写下划线），成功时为 None。
        result: 完整研究结果，仅成功/部分成功时填充。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "jobId": "a1b2c3d4-1234-5678-9abc-def012345678",
            "status": "SUCCESS",
            "question": "2024 年基金市场表现如何？",
            "startedAt": "2026-07-30T10:00:00+00:00",
            "completedAt": "2026-07-30T10:00:08+00:00",
            "totalDurationMs": 7800,
            "degraded": False,
            "degradedReasons": [],
            "errorCode": None,
            "result": None,
        }
    })

    jobId: str = Field(..., description="任务 ID")
    status: str = Field(..., description="任务状态：PENDING / RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / TIMEOUT")
    question: str = Field(..., description="用户问题原文")
    startedAt: datetime | None = Field(default=None, description="任务开始时间（ISO8601），PENDING 状态为 None")
    completedAt: datetime | None = Field(default=None, description="任务完成时间（ISO8601），未完成时为 None")
    totalDurationMs: int | None = Field(default=None, description="任务总耗时（毫秒），未完成时为 None")
    degraded: bool = Field(default=False, description="是否降级")
    degradedReasons: list[str] = Field(default_factory=list, description="降级原因列表")
    errorCode: str | None = Field(default=None, description="失败错误码，成功时为 None")
    result: dict[str, Any] | None = Field(default=None, description="完整研究结果，仅成功/部分成功时填充")


class ResearchJobListItem(BaseModel):
    """异步研究任务列表项（Task 16.2，``GET /api/v1/research/jobs``）。

    列表项不含完整 result，仅含状态摘要，详情需调用
    ``GET /api/v1/research/jobs/{jobId}``。

    Attributes:
        jobId: 任务 ID。
        status: 任务状态（大写形式）。
        question: 用户问题原文（用于列表识别）。
        degraded: 是否降级。
        startedAt: 任务开始时间，可空。
        completedAt: 任务完成时间，可空。
        totalDurationMs: 总耗时（毫秒），可空。
        createdAt: 任务创建时间（入队时间）。
    """

    model_config = ConfigDict(from_attributes=True)

    jobId: str = Field(..., description="任务 ID")
    status: str = Field(..., description="任务状态（大写形式）")
    question: str = Field(..., description="用户问题原文")
    degraded: bool = Field(default=False, description="是否降级")
    startedAt: datetime | None = Field(default=None, description="任务开始时间")
    completedAt: datetime | None = Field(default=None, description="任务完成时间")
    totalDurationMs: int | None = Field(default=None, description="总耗时（毫秒）")
    createdAt: datetime = Field(..., description="任务创建时间")


class FeedbackRequest(BaseModel):
    """反馈提交请求体（Task 16.3，``POST /api/v1/research/{requestId}/feedback``）。

    用于客户端对研究结论的满意度评价。反馈数据将用于：
    1. **系统优化**：识别质量差的研究结论，反向优化提示词与证据评分策略
    2. **效果评估**：结合 ``businessResultId`` 关联业务侧落地结果，
       评估研究结论对业务决策的实际贡献
    3. **降级监控**：低分反馈集中于某降级原因时，定位系统瓶颈

    为什么反馈需要 accepted 字段？
        ``rating=helpful`` 仅表示用户对内容满意，``accepted`` 表示用户实际
        采取了行动（如基于研究结论执行了买入操作）。两者维度不同：
        满意不一定采纳（如结论合理但用户选择观望），采纳一定满意。

    Attributes:
        rating: 评分，``helpful`` / ``partially_helpful`` / ``not_helpful``。
        accepted: 是否采纳（用户是否基于此结果行动）。
        comment: 评论文本，可空。用于补充具体反馈意见。
        businessResultId: 业务结果 ID，可空。用于关联业务侧落地结果，
            便于效果评估（如基金买入订单 ID）。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "rating": "helpful",
            "accepted": True,
            "comment": "结论清晰，证据充分",
            "businessResultId": "order_20260730_001",
        }
    })

    rating: FeedbackRating = Field(
        ...,
        description="评分：helpful=有用 / partially_helpful=部分有用 / not_helpful=无用",
    )
    accepted: bool = Field(..., description="是否采纳（用户是否基于此结果行动）")
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="评论文本，可空",
    )
    businessResultId: str | None = Field(
        default=None,
        max_length=120,
        description="业务结果 ID，可空，用于关联业务侧落地结果",
    )


class FeedbackResponse(BaseModel):
    """反馈提交响应体（Task 16.3）。

    返回反馈 ID 与关键字段，便于客户端确认提交结果。
    同一 ``requestId`` 多次提交反馈会更新原记录（upsert 语义），
    始终返回相同的 ``feedbackId``。

    Attributes:
        feedbackId: 反馈记录 ID。
        requestId: 关联的研究任务对外请求 ID。
        rating: 评分。
        accepted: 是否采纳。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "feedbackId": "f1e2d3c4-1234-5678-9abc-def012345678",
            "requestId": "req_1785412111000",
            "rating": "helpful",
            "accepted": True,
        }
    })

    feedbackId: str = Field(..., description="反馈记录 ID")
    requestId: str = Field(..., description="关联的研究任务对外请求 ID")
    rating: str = Field(..., description="评分：helpful / partially_helpful / not_helpful")
    accepted: bool = Field(..., description="是否采纳")


# ============================================================================
# 定时任务调度相关模型（Task 18）
# ============================================================================
# 任务类型枚举（对外大写形式，对应数据库小写形式）
# - CRAWL_SOURCE: 触发采集源爬取（Task 19 实现）
# - TOOL_SYNC: 工具数据同步（占位 TODO）
# - RESEARCH_RUN: 触发研究任务（复用 ResearchService）
# - REINDEX_KNOWLEDGE: 重建知识库向量索引（占位 TODO）
# - EXPIRE_KNOWLEDGE: 过期知识清理（占位 TODO）
ScheduleTaskType = Literal[
    "CRAWL_SOURCE",
    "TOOL_SYNC",
    "RESEARCH_RUN",
    "REINDEX_KNOWLEDGE",
    "EXPIRE_KNOWLEDGE",
]

# 并发策略枚举
# - skip: 若上次执行仍在运行，跳过本次触发（避免堆积）
# - queue: 允许排队执行（适用于幂等且可并行的任务）
ScheduleConcurrencyPolicy = Literal["skip", "queue"]

# 调度运行状态枚举（对外大写形式，对应数据库小写形式）
# 内部状态：pending / running / success / failed / timeout
# 对外状态：PENDING / RUNNING / SUCCESS / FAILED / TIMEOUT
ScheduleRunStatus = Literal[
    "PENDING",
    "RUNNING",
    "SUCCESS",
    "FAILED",
    "TIMEOUT",
]


class ScheduleCreateRequest(BaseModel):
    """创建定时任务请求体（Task 18.1，``POST /api/v1/schedules``）。

    用于创建一个定时任务，由 Celery Beat 每分钟扫描 ``next_run_at`` 到期的任务
    并投递执行。``next_run_at`` 由服务端根据 cron + timezone 计算，
    客户端无法指定。

    设计要点
    --------
    1. ``cronExpression`` 是用户本地时间语义（如 ``0 9 * * 1-5`` 表示工作日 9 点），
       服务端按 ``timezone`` 转换为 UTC 后存储 ``next_run_at``。
    2. ``config`` 是任务配置（JSONB），不同 taskType 携带不同字段：
       - CRAWL_SOURCE: ``{"crawlSourceId": "..."}`
       - TOOL_SYNC: ``{"toolCode": "fund_market"}`
       - RESEARCH_RUN: ``{"question": "...", "strategy": "full", ...}`
       - REINDEX_KNOWLEDGE: ``{"knowledgeBaseId": "..."}`
       - EXPIRE_KNOWLEDGE: ``{"knowledgeBaseId": "...", "retentionDays": 30}`
    3. ``concurrencyPolicy`` 控制并发策略：skip=跳过 / queue=排队。
    4. ``timeoutSeconds`` 单次执行超时，默认 300s。
    5. ``maxRetries`` 失败重试次数，默认 2。

    Attributes:
        name: 任务名称，便于后台识别。
        taskType: 任务类型，``CRAWL_SOURCE`` / ``TOOL_SYNC`` / ``RESEARCH_RUN`` /
            ``REINDEX_KNOWLEDGE`` / ``EXPIRE_KNOWLEDGE``。
        cronExpression: cron 表达式（5 段：分 时 日 月 周），本地时间语义。
        timezone: 时区，默认 ``Asia/Shanghai``。IANA 时区名称。
        config: 任务配置（JSONB），不同 taskType 携带不同字段。
        concurrencyPolicy: 并发策略，默认 ``skip``。
        timeoutSeconds: 单次执行超时秒数，默认 300。
        maxRetries: 失败重试次数，默认 2。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "每日基金市场研究",
            "taskType": "RESEARCH_RUN",
            "cronExpression": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "config": {
                "question": "今日基金市场表现如何？",
                "strategy": "full",
                "knowledgeBaseIds": [],
                "toolCodes": ["fund_market"],
            },
            "concurrencyPolicy": "skip",
            "timeoutSeconds": 300,
            "maxRetries": 2,
        }
    })

    name: str = Field(..., min_length=1, max_length=120, description="任务名称")
    taskType: ScheduleTaskType = Field(
        ...,
        description="任务类型：CRAWL_SOURCE / TOOL_SYNC / RESEARCH_RUN / REINDEX_KNOWLEDGE / EXPIRE_KNOWLEDGE",
    )
    cronExpression: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="cron 表达式（5 段：分 时 日 月 周），本地时间语义",
    )
    timezone: str = Field(
        default="Asia/Shanghai",
        max_length=64,
        description="时区（IANA 名称），默认 Asia/Shanghai",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="任务配置（JSONB），不同 taskType 携带不同字段",
    )
    concurrencyPolicy: ScheduleConcurrencyPolicy = Field(
        default="skip",
        description="并发策略：skip=跳过 / queue=排队，默认 skip",
    )
    timeoutSeconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        description="单次执行超时秒数，默认 300",
    )
    maxRetries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="失败重试次数，默认 2",
    )


class ScheduleUpdateRequest(BaseModel):
    """编辑定时任务请求体（Task 18.1，``PATCH /api/v1/schedules/{scheduleId}``）。

    仅允许修改 name / cronExpression / timezone / config / concurrencyPolicy /
    timeoutSeconds / maxRetries。``taskType`` 创建后不可改（不同类型 config 结构
    不同，混改会导致执行器解析失败）。``enabled`` 通过 pause/resume 接口控制，
    不在此处修改。

    改 ``cronExpression`` 或 ``timezone`` 时，服务端会重算 ``next_run_at``，
    保证下次触发点与新表达式一致。

    Attributes:
        name: 任务名称，可空。
        cronExpression: cron 表达式，可空。修改后重算 next_run_at。
        timezone: 时区，可空。修改后重算 next_run_at。
        config: 任务配置，可空。
        concurrencyPolicy: 并发策略，可空。
        timeoutSeconds: 单次执行超时秒数，可空。
        maxRetries: 失败重试次数，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "每日基金市场研究（修订）",
            "cronExpression": "30 9 * * 1-5",
            "timezone": "Asia/Shanghai",
        }
    })

    # 全部可选：PATCH 语义，仅更新传入字段
    name: str | None = Field(default=None, min_length=1, max_length=120, description="任务名称")
    cronExpression: str | None = Field(
        default=None, min_length=1, max_length=64, description="cron 表达式"
    )
    timezone: str | None = Field(default=None, max_length=64, description="时区（IANA 名称）")
    config: dict[str, Any] | None = Field(default=None, description="任务配置（JSONB）")
    concurrencyPolicy: ScheduleConcurrencyPolicy | None = Field(
        default=None, description="并发策略：skip / queue"
    )
    timeoutSeconds: int | None = Field(
        default=None, ge=1, le=3600, description="单次执行超时秒数"
    )
    maxRetries: int | None = Field(default=None, ge=0, le=10, description="失败重试次数")


class ScheduleResponse(BaseModel):
    """定时任务响应体（Task 18.1）。

    用于调度相关接口的响应数据，包含完整字段与服务端生成的 ``id`` / ``createdAt`` /
    ``nextRunAt`` / ``lastRunAt``。

    字段命名采用 camelCase（与前端约定一致），状态字段为对外大写形式。

    Attributes:
        id: 定时任务 ID（UUID）。
        name: 任务名称。
        taskType: 任务类型（大写形式）。
        cronExpression: cron 表达式。
        timezone: 时区。
        config: 任务配置（JSONB）。
        concurrencyPolicy: 并发策略。
        timeoutSeconds: 单次执行超时秒数。
        maxRetries: 失败重试次数。
        enabled: 是否启用。
        nextRunAt: 下次执行时间（UTC ISO8601），暂停或无法计算时为 None。
        lastRunAt: 上次执行时间（UTC ISO8601），从未执行时为 None。
        createdAt: 创建时间。
        updatedAt: 最近更新时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="定时任务 ID（UUID）")
    name: str = Field(..., description="任务名称")
    taskType: str = Field(..., description="任务类型（大写形式）")
    cronExpression: str = Field(..., description="cron 表达式")
    timezone: str = Field(..., description="时区（IANA 名称）")
    config: dict[str, Any] | None = Field(default=None, description="任务配置（JSONB）")
    concurrencyPolicy: str = Field(..., description="并发策略：skip / queue")
    timeoutSeconds: int = Field(..., description="单次执行超时秒数")
    maxRetries: int = Field(..., description="失败重试次数")
    enabled: bool = Field(..., description="是否启用")
    nextRunAt: datetime | None = Field(default=None, description="下次执行时间（UTC ISO8601）")
    lastRunAt: datetime | None = Field(default=None, description="上次执行时间（UTC ISO8601）")
    createdAt: datetime = Field(..., description="创建时间")
    updatedAt: datetime = Field(..., description="最近更新时间")


class ScheduleRunResponse(BaseModel):
    """定时任务运行记录响应体（Task 18.1）。

    用于 ``GET /api/v1/schedules/{scheduleId}/runs`` 与
    ``GET /api/v1/schedule-runs/{runId}`` 接口的响应数据。

    状态字段为对外大写形式（PENDING / RUNNING / SUCCESS / FAILED / TIMEOUT），
    与数据库小写形式对应。

    Attributes:
        id: 运行记录 ID（UUID）。
        scheduleId: 关联定时任务 ID。
        plannedAt: 计划执行时间（UTC ISO8601），由 Beat 根据 cron 推算。
        startedAt: 实际开始时间（UTC ISO8601），PENDING 时为 None。
        completedAt: 实际完成时间（UTC ISO8601），未完成时为 None。
        status: 运行状态（大写形式）。
        attempt: 重试次数，从 0 开始递增。
        queueJobId: Celery 任务 ID，用于追踪队列。
        resultSummary: 运行结果摘要（JSONB）。
        errorCode: 失败错误码（大写下划线），成功时为 None。
        errorMessage: 失败错误信息，成功时为 None。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="运行记录 ID（UUID）")
    scheduleId: str = Field(..., description="关联定时任务 ID")
    plannedAt: datetime = Field(..., description="计划执行时间（UTC ISO8601）")
    startedAt: datetime | None = Field(default=None, description="实际开始时间（UTC ISO8601）")
    completedAt: datetime | None = Field(default=None, description="实际完成时间（UTC ISO8601）")
    status: str = Field(..., description="运行状态（大写）：PENDING / RUNNING / SUCCESS / FAILED / TIMEOUT")
    attempt: int = Field(..., description="重试次数，从 0 开始递增")
    queueJobId: str | None = Field(default=None, description="Celery 任务 ID")
    resultSummary: dict[str, Any] | None = Field(default=None, description="运行结果摘要（JSONB）")
    errorCode: str | None = Field(default=None, description="失败错误码（大写下划线），成功时为 None")
    errorMessage: str | None = Field(default=None, description="失败错误信息，成功时为 None")
    createdAt: datetime = Field(..., description="创建时间")


# ============================================================================
# 网页采集相关模型（Task 19）
# ============================================================================
# 采集源类型枚举（对外大写形式，对应数据库小写形式）
# - SINGLE_PAGE：单页采集，直接抓取 start_urls（通常 1 个 URL）
# - URL_LIST：URL 列表，逐个抓取 start_urls 中的每个 URL
# - RSS：RSS 订阅源，解析 RSS feed 提取条目 URL
# - SITEMAP：站点地图，解析 sitemap.xml 提取 URL 列表
# - LIST_PAGE：列表页，抓取列表页后按 extract_rules 提取详情页 URL
CrawlSourceType = Literal[
    "SINGLE_PAGE",
    "URL_LIST",
    "RSS",
    "SITEMAP",
    "LIST_PAGE",
]

# 入库策略枚举
# - REVIEW_REQUIRED：需人工审核后入库（默认），采集结果先入待审核资料池
# - AUTO_IMPORT：自动入库，采集结果直接创建 Document + IngestionJob
# - EVIDENCE_ONLY：仅作证据，不入知识库，仅入待审核资料池短期保存
CrawlImportPolicy = Literal[
    "REVIEW_REQUIRED",
    "AUTO_IMPORT",
    "EVIDENCE_ONLY",
]

# 采集源状态枚举（对外大写形式，对应数据库小写形式）
# - ACTIVE：启用
# - PAUSED：暂停（仅 paused 状态可删除）
# - DISABLED：停用
CrawlSourceStatus = Literal["ACTIVE", "PAUSED", "DISABLED"]

# 采集运行状态枚举（对外大写形式，对应数据库小写形式）
# - PENDING：待执行
# - RUNNING：执行中
# - SUCCESS：成功
# - FAILED：失败
CrawlRunStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"]

# 采集页面状态枚举（对外大写形式，对应数据库小写形式）
# - DISCOVERED：已发现（待抓取）
# - FETCHED：已抓取
# - IMPORTED：已入库
# - REVIEW：待审核
# - FAILED：失败
# - SOURCE_UNAVAILABLE：源不可用
CrawlPageStatus = Literal[
    "DISCOVERED",
    "FETCHED",
    "IMPORTED",
    "REVIEW",
    "FAILED",
    "SOURCE_UNAVAILABLE",
]

# 网络资料审核状态枚举（对外大写形式，对应数据库小写形式）
# - PENDING：待审核
# - ADOPTED：已采用（已入库知识库）
# - REJECTED：已拒绝
# - EXPIRED：已过期（仅作证据的资料短期保存后过期）
WebMaterialStatus = Literal["PENDING", "ADOPTED", "REJECTED", "EXPIRED"]


class CrawlSourceCreateRequest(BaseModel):
    """创建采集源请求体（Task 19，``POST /api/v1/crawl-sources``）。

    用于创建一个网络采集源，定义采集类型、起始 URL、域名限制、入库策略等。
    ``code`` 在项目内唯一（复合唯一约束 ``uq_crawl_sources_project_code``），
    创建后不可改。

    设计要点
    --------
    1. ``startUrls`` 必须非空（至少 1 个 URL），由端点校验格式合法性。
    2. ``allowedDomains`` 为空表示不限制域名（仅做 SSRF 防护）；
       非空时仅允许列表内域名被采集（白名单模式）。
    3. ``importPolicy`` 默认 ``REVIEW_REQUIRED``（需审核），
       避免不可信内容直接入库污染知识库。
    4. ``limits`` 控制采集规模与频率，避免拖垮目标站点与 Worker。

    Attributes:
        code: 采集源编码，项目内唯一，1-64 字符。
        name: 采集源名称，1-120 字符。
        type: 采集类型，SINGLE_PAGE / URL_LIST / RSS / SITEMAP / LIST_PAGE。
        startUrls: 起始 URL 列表，至少 1 个。
        allowedDomains: 允许采集域名白名单，可空（不限制）。
        blockedPaths: 屏蔽路径列表，可空。
        destinationKnowledgeBaseId: 入库目标知识库 ID，可空（evidence_only 时为空）。
        extractRules: 正文提取规则（JSONB），如 CSS 选择器。
        importPolicy: 入库策略，默认 REVIEW_REQUIRED。
        limits: 采集限制（JSONB），如 maxPagesPerRun / requestIntervalMs。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "code": "fund-news",
            "name": "基金新闻采集",
            "type": "LIST_PAGE",
            "startUrls": ["https://example.com/news/list"],
            "allowedDomains": ["example.com"],
            "blockedPaths": ["/admin", "/private"],
            "destinationKnowledgeBaseId": "kb-uuid-xxx",
            "extractRules": {"detailSelector": "a.news-title", "contentSelector": "div.article"},
            "importPolicy": "REVIEW_REQUIRED",
            "limits": {"maxPagesPerRun": 100, "requestIntervalMs": 1000, "maxDepth": 1, "concurrencyPerDomain": 1},
        }
    })

    code: str = Field(..., min_length=1, max_length=64, description="采集源编码（项目内唯一）")
    name: str = Field(..., min_length=1, max_length=120, description="采集源名称")
    type: CrawlSourceType = Field(..., description="采集类型：SINGLE_PAGE / URL_LIST / RSS / SITEMAP / LIST_PAGE")
    startUrls: list[str] = Field(..., min_length=1, description="起始 URL 列表，至少 1 个")
    allowedDomains: list[str] | None = Field(default=None, description="允许采集域名白名单，可空")
    blockedPaths: list[str] | None = Field(default=None, description="屏蔽路径列表，可空")
    destinationKnowledgeBaseId: str | None = Field(
        default=None, description="入库目标知识库 ID，可空（evidence_only 时为空）"
    )
    extractRules: dict[str, Any] | None = Field(default=None, description="正文提取规则（JSONB）")
    importPolicy: CrawlImportPolicy = Field(
        default="REVIEW_REQUIRED",
        description="入库策略：REVIEW_REQUIRED / AUTO_IMPORT / EVIDENCE_ONLY，默认 REVIEW_REQUIRED",
    )
    limits: dict[str, Any] | None = Field(
        default=None,
        description="采集限制（JSONB）：maxPagesPerRun / maxDepth / requestIntervalMs / concurrencyPerDomain",
    )


class CrawlSourceUpdateRequest(BaseModel):
    """编辑采集源请求体（Task 19，``PATCH /api/v1/crawl-sources/{sourceId}``）。

    仅允许修改 name / startUrls / allowedDomains / blockedPaths /
    destinationKnowledgeBaseId / extractRules / importPolicy / limits。
    ``code`` / ``type`` 创建后不可改（type 决定采集流程分发逻辑）。
    ``status`` 通过 pause/resume 接口控制，不在此处修改。

    Attributes:
        name: 采集源名称，可空。
        startUrls: 起始 URL 列表，可空。
        allowedDomains: 允许采集域名白名单，可空。
        blockedPaths: 屏蔽路径列表，可空。
        destinationKnowledgeBaseId: 入库目标知识库 ID，可空。
        extractRules: 正文提取规则，可空。
        importPolicy: 入库策略，可空。
        limits: 采集限制，可空。
    """

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name": "基金新闻采集（修订）",
            "limits": {"maxPagesPerRun": 200},
        }
    })

    # 全部可选：PATCH 语义，仅更新传入字段
    name: str | None = Field(default=None, min_length=1, max_length=120, description="采集源名称")
    startUrls: list[str] | None = Field(default=None, description="起始 URL 列表")
    allowedDomains: list[str] | None = Field(default=None, description="允许采集域名白名单")
    blockedPaths: list[str] | None = Field(default=None, description="屏蔽路径列表")
    destinationKnowledgeBaseId: str | None = Field(default=None, description="入库目标知识库 ID")
    extractRules: dict[str, Any] | None = Field(default=None, description="正文提取规则（JSONB）")
    importPolicy: CrawlImportPolicy | None = Field(default=None, description="入库策略")
    limits: dict[str, Any] | None = Field(default=None, description="采集限制（JSONB）")


class CrawlSourceResponse(BaseModel):
    """采集源响应体（Task 19）。

    字段命名采用 camelCase（与前端约定一致），状态字段为对外大写形式。
    ``type`` / ``importPolicy`` / ``status`` 均为大写形式，对应数据库小写存储。

    Attributes:
        id: 采集源 ID（UUID）。
        code: 采集源编码（项目内唯一）。
        name: 采集源名称。
        type: 采集类型（大写）。
        startUrls: 起始 URL 列表。
        allowedDomains: 允许采集域名白名单。
        blockedPaths: 屏蔽路径列表。
        destinationKnowledgeBaseId: 入库目标知识库 ID。
        extractRules: 正文提取规则（JSONB）。
        importPolicy: 入库策略（大写）。
        limits: 采集限制（JSONB）。
        status: 状态（大写）：ACTIVE / PAUSED / DISABLED。
        createdAt: 创建时间。
        updatedAt: 最近更新时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="采集源 ID（UUID）")
    code: str = Field(..., description="采集源编码（项目内唯一）")
    name: str = Field(..., description="采集源名称")
    type: str = Field(..., description="采集类型（大写）")
    startUrls: list[str] | None = Field(default=None, description="起始 URL 列表")
    allowedDomains: list[str] | None = Field(default=None, description="允许采集域名白名单")
    blockedPaths: list[str] | None = Field(default=None, description="屏蔽路径列表")
    destinationKnowledgeBaseId: str | None = Field(default=None, description="入库目标知识库 ID")
    extractRules: dict[str, Any] | None = Field(default=None, description="正文提取规则（JSONB）")
    importPolicy: str = Field(..., description="入库策略（大写）")
    limits: dict[str, Any] | None = Field(default=None, description="采集限制（JSONB）")
    status: str = Field(..., description="状态（大写）：ACTIVE / PAUSED / DISABLED")
    createdAt: datetime = Field(..., description="创建时间")
    updatedAt: datetime = Field(..., description="最近更新时间")


class CrawlRunResponse(BaseModel):
    """采集运行记录响应体（Task 19）。

    用于 ``GET /api/v1/crawl-sources/{sourceId}/runs`` 与
    ``GET /api/v1/crawl-runs/{runId}`` 接口的响应数据。

    状态字段为对外大写形式（PENDING / RUNNING / SUCCESS / FAILED），
    与数据库小写形式对应。

    Attributes:
        id: 运行记录 ID（UUID）。
        crawlSourceId: 关联采集源 ID。
        status: 运行状态（大写）。
        discoveredCount: 发现页面数。
        successCount: 成功抓取数。
        duplicateCount: 重复页面数（URL 去重）。
        failedCount: 失败页面数。
        importedCount: 入库数。
        startedAt: 运行开始时间（UTC ISO8601）。
        completedAt: 运行完成时间（UTC ISO8601）。
        errorCode: 失败错误码，可空。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="运行记录 ID（UUID）")
    crawlSourceId: str = Field(..., description="关联采集源 ID")
    status: str = Field(..., description="运行状态（大写）：PENDING / RUNNING / SUCCESS / FAILED")
    discoveredCount: int = Field(..., description="发现页面数")
    successCount: int = Field(..., description="成功抓取数")
    duplicateCount: int = Field(..., description="重复页面数（URL 去重）")
    failedCount: int = Field(..., description="失败页面数")
    importedCount: int = Field(..., description="入库数")
    startedAt: datetime | None = Field(default=None, description="运行开始时间（UTC ISO8601）")
    completedAt: datetime | None = Field(default=None, description="运行完成时间（UTC ISO8601）")
    errorCode: str | None = Field(default=None, description="失败错误码，可空")
    createdAt: datetime = Field(..., description="创建时间")


class CrawlPageResponse(BaseModel):
    """采集页面响应体（Task 19）。

    用于 ``GET /api/v1/crawl-runs/{runId}/pages`` 接口的响应数据。
    ``canonicalUrlHash`` 是去重键，``contentHash`` 是正文哈希（用于增量更新检测）。

    Attributes:
        id: 页面记录 ID（UUID）。
        crawlSourceId: 关联采集源 ID。
        crawlRunId: 关联运行记录 ID。
        url: 原始 URL。
        canonicalUrl: 规范化 URL。
        canonicalUrlHash: 规范化 URL 哈希（去重键）。
        title: 页面标题，可空。
        contentHash: 正文哈希，可空。
        publishedAt: 页面发布时间，可空。
        fetchedAt: 抓取时间，可空。
        httpStatus: HTTP 状态码，可空。
        status: 页面状态（大写）：DISCOVERED / FETCHED / IMPORTED / REVIEW / FAILED / SOURCE_UNAVAILABLE。
        documentId: 入库后关联的文档 ID，可空。
        errorCode: 失败错误码，可空。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="页面记录 ID（UUID）")
    crawlSourceId: str = Field(..., description="关联采集源 ID")
    crawlRunId: str = Field(..., description="关联运行记录 ID")
    url: str = Field(..., description="原始 URL")
    canonicalUrl: str = Field(..., description="规范化 URL")
    canonicalUrlHash: str = Field(..., description="规范化 URL 哈希（去重键）")
    title: str | None = Field(default=None, description="页面标题")
    contentHash: str | None = Field(default=None, description="正文哈希（SHA-256）")
    publishedAt: datetime | None = Field(default=None, description="页面发布时间")
    fetchedAt: datetime | None = Field(default=None, description="抓取时间")
    httpStatus: int | None = Field(default=None, description="HTTP 状态码")
    status: str = Field(
        ..., description="页面状态（大写）：DISCOVERED / FETCHED / IMPORTED / REVIEW / FAILED / SOURCE_UNAVAILABLE"
    )
    documentId: str | None = Field(default=None, description="入库后关联的文档 ID")
    errorCode: str | None = Field(default=None, description="失败错误码")
    createdAt: datetime = Field(..., description="创建时间")


class WebMaterialResponse(BaseModel):
    """网络待审核资料响应体（Task 19）。

    用于 ``GET /api/v1/web-materials`` 接口的响应数据。
    ``review_required`` 策略下采集结果先入此表，人工审核通过后才入库到知识库。

    Attributes:
        id: 资料 ID（UUID）。
        crawlSourceId: 关联采集源 ID，可空。
        crawlPageId: 关联采集页面 ID，可空。
        title: 资料标题。
        content: 资料正文。
        sourceUrl: 来源 URL。
        status: 审核状态（大写）：PENDING / ADOPTED / REJECTED / EXPIRED。
        knowledgeBaseId: 采用后入库的目标知识库 ID，可空。
        reviewedAt: 审核时间，可空。
        createdAt: 创建时间。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="资料 ID（UUID）")
    crawlSourceId: str | None = Field(default=None, description="关联采集源 ID")
    crawlPageId: str | None = Field(default=None, description="关联采集页面 ID")
    title: str = Field(..., description="资料标题")
    content: str = Field(..., description="资料正文")
    sourceUrl: str = Field(..., description="来源 URL")
    status: str = Field(..., description="审核状态（大写）：PENDING / ADOPTED / REJECTED / EXPIRED")
    knowledgeBaseId: str | None = Field(default=None, description="采用后入库的目标知识库 ID")
    reviewedAt: datetime | None = Field(default=None, description="审核时间")
    createdAt: datetime = Field(..., description="创建时间")

