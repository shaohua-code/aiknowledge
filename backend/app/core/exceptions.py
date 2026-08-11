"""统一异常体系：基类 KnowledgeHubError 及其业务子类。

对应 SubTask 2.4 与 SubTask 6.3：所有业务异常都派生自 KnowledgeHubError，
通过 FastAPI 异常处理器统一转换为 JSON 响应。每个子类携带：
- code: 机器可读错误码（大写下划线）
- message: 默认人类可读消息
- http_status: HTTP 状态码
- retryable: 客户端是否可重试
- details: 额外上下文（字段错误、缺失资源 ID 等）
"""
from __future__ import annotations

from typing import Any


class KnowledgeHubError(Exception):
    """智能知识中台异常基类。

    所有业务异常都派生自此类，统一被异常处理器捕获并转换为 JSON 响应。

    Attributes:
        code: 机器可读错误码，如 VALIDATION_ERROR
        message: 默认人类可读消息
        http_status: HTTP 状态码
        retryable: 客户端是否可重试
        details: 额外上下文信息
    """

    code: str = "INTERNAL_ERROR"
    message: str = "服务器内部错误"
    http_status: int = 500
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化异常。

        Args:
            message: 自定义消息，未提供则使用类默认值
            code: 自定义错误码
            http_status: 自定义 HTTP 状态码
            retryable: 是否可重试
            details: 额外上下文
        """
        self.message = message if message is not None else self.message
        self.code = code if code is not None else self.code
        self.http_status = http_status if http_status is not None else self.http_status
        self.retryable = retryable if retryable is not None else self.retryable
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(KnowledgeHubError):
    """请求参数校验失败。

    触发场景：请求体字段缺失、类型错误、枚举值非法、长度超限等。
    """

    code = "VALIDATION_ERROR"
    message = "请求参数校验失败"
    http_status = 422


class InvalidApiKeyError(KnowledgeHubError):
    """API Key 无效或缺失。

    触发场景：未携带 Authorization 头、Bearer Token 格式错误、
    API Key 不存在或已停用、哈希校验失败。
    """

    code = "INVALID_API_KEY"
    message = "API Key 无效或未授权"
    http_status = 401


class ScopeNotAllowedError(KnowledgeHubError):
    """API Key 不具备所需 Scope。

    触发场景：不具备 knowledge:write 的 Key 上传文档、
    不具备 research:run 的 Key 调用研究接口。
    """

    code = "SCOPE_NOT_ALLOWED"
    message = "当前 API Key 不具备所需权限"
    http_status = 403


class ProjectDisabledError(KnowledgeHubError):
    """项目已停用，拒绝所有业务请求。

    触发场景：项目状态为 DISABLED 时，除项目管理接口外的所有业务接口被拒绝。
    """

    code = "PROJECT_DISABLED"
    message = "项目已停用，暂不可用"
    http_status = 403


class ProjectCodeMismatchError(KnowledgeHubError):
    """请求头 X-Project-Code 与 API Key 所属项目不一致。

    触发场景：客户端携带的 X-Project-Code 与服务端从 API Key 解析得到的项目代码不匹配，
    用于防止跨项目误用。
    """

    code = "PROJECT_CODE_MISMATCH"
    message = "项目代码与 API Key 不一致"
    http_status = 403


class ProjectScopeMismatchError(KnowledgeHubError):
    """请求资源不属于当前 API Key 所属项目。

    触发场景：通过路径参数访问其他项目的资源，
    如 AI 基金 Key 访问 AI 简历的 knowledge-base。
    """

    code = "PROJECT_SCOPE_MISMATCH"
    message = "资源不属于当前项目"
    http_status = 403


class KnowledgeBaseNotFoundError(KnowledgeHubError):
    """知识库不存在或不可见。

    触发场景：knowledge_base code 在当前项目下不存在，
    为避免泄露跨项目资源是否存在，统一返回 404。
    """

    code = "KNOWLEDGE_BASE_NOT_FOUND"
    message = "知识库不存在"
    http_status = 404


class TaskNotFoundError(KnowledgeHubError):
    """研究任务或后台任务不存在。

    触发场景：通过 jobId/documentId/scheduleRunId 查询的任务在当前项目下不存在。
    """

    code = "TASK_NOT_FOUND"
    message = "任务不存在"
    http_status = 404


class CrawlSourceNotFoundError(KnowledgeHubError):
    """采集源不存在。

    触发场景：通过 crawlSourceId 访问的采集源在当前项目下不存在。
    """

    code = "CRAWL_SOURCE_NOT_FOUND"
    message = "采集源不存在"
    http_status = 404


class ScheduleRunConflictError(KnowledgeHubError):
    """调度运行冲突。

    触发场景：同一 schedule_id + planned_run_at 已存在运行记录，
    多实例同时领取同一到期任务时由幂等键触发。
    """

    code = "SCHEDULE_RUN_CONFLICT"
    message = "调度运行已存在，禁止重复触发"
    http_status = 409


class IdempotencyConflictError(KnowledgeHubError):
    """幂等键冲突：相同 Key 不同内容。

    触发场景：客户端复用 Idempotency-Key，但请求体内容与首次不同，
    系统拒绝创建新任务以避免重复。
    """

    code = "IDEMPOTENCY_CONFLICT"
    message = "幂等键已存在但请求内容不一致"
    http_status = 409


class OutputTypeNotAllowedError(KnowledgeHubError):
    """研究输出类型不被当前项目允许。

    触发场景：项目未启用某 outputType（如 JSON 结构化输出），
    客户端却请求该类型。
    """

    code = "OUTPUT_TYPE_NOT_ALLOWED"
    message = "输出类型不被允许"
    http_status = 403


class ToolNotAllowedError(KnowledgeHubError):
    """工具未在当前项目白名单中。

    触发场景：AI 简历 Key 调用 fund_market 基金工具，
    该工具未在 AI 简历项目的 project_tools 白名单中。
    """

    code = "TOOL_NOT_ALLOWED"
    message = "工具未在当前项目白名单中"
    http_status = 403


class InsufficientEvidenceError(KnowledgeHubError):
    """证据不足，无法生成研究结论。

    触发场景：内部检索、联网搜索、业务工具均未返回有效证据，
    或去重后证据数低于阈值，无法支撑一次模型生成。
    """

    code = "INSUFFICIENT_EVIDENCE"
    message = "证据不足，无法生成结论"
    http_status = 422


class RateLimitedError(KnowledgeHubError):
    """项目级限流触发。

    触发场景：当前 API Key 每分钟调用量超过 RATE_LIMIT_PER_MINUTE，
    /retrieval/search 与 /research/run 分别计数。
    """

    code = "RATE_LIMITED"
    message = "请求过于频繁，请稍后重试"
    http_status = 429
    retryable = True


class ExternalSourceFailedError(KnowledgeHubError):
    """外部数据源失败（非超时）。

    触发场景：联网搜索返回 5xx、业务工具返回错误、
    Embedding 服务异常但未超时。研究链路中触发降级而非中断。
    """

    code = "EXTERNAL_SOURCE_FAILED"
    message = "外部数据源调用失败"
    http_status = 502
    retryable = True


class ExternalSourceTimeoutError(KnowledgeHubError):
    """外部数据源超时。

    触发场景：联网搜索超过 WEB_SEARCH_TIMEOUT_SECONDS（5s）、
    业务工具超过 TOOL_TIMEOUT_SECONDS（4s）。
    研究链路中触发降级，返回 degraded=true。
    """

    code = "EXTERNAL_SOURCE_TIMEOUT"
    message = "外部数据源调用超时"
    http_status = 504
    retryable = True


class ModelTimeoutError(KnowledgeHubError):
    """大模型调用超时。

    触发场景：研究链路中单次模型生成超过配置超时，
    返回已整理证据与失败状态，degraded=true。
    """

    code = "MODEL_TIMEOUT"
    message = "模型调用超时"
    http_status = 504
    retryable = True


class CrawlUrlNotAllowedError(KnowledgeHubError):
    """采集 URL 不被允许（SSRF 防护）。

    触发场景：URL 指向 localhost、内网地址、云元数据服务，
    或不在采集源 allowedDomains 白名单中。
    """

    code = "CRAWL_URL_NOT_ALLOWED"
    message = "采集 URL 不被允许"
    http_status = 403


class CrawlRuleInvalidError(KnowledgeHubError):
    """采集规则配置无效。

    触发场景：提取规则语法错误、CSS Selector 无效、
    limits 配置相互冲突（如 maxDepth 为负）。
    """

    code = "CRAWL_RULE_INVALID"
    message = "采集规则配置无效"
    http_status = 422


class InternalError(KnowledgeHubError):
    """未预期的内部错误。

    触发场景：未捕获异常、数据库连接断开、未知系统错误。
    生产环境响应体不泄露堆栈，仅返回通用错误码。
    """

    code = "INTERNAL_ERROR"
    message = "服务器内部错误"
    http_status = 500
    retryable = True
