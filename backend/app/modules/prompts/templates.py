"""提示词默认模板：按项目类型区分的内置提示词模板。

对应 SubTask 14.2：项目首次访问时若无任何提示词版本，使用本模块的模板
创建默认版本并激活，确保每个项目开箱即用。

设计理念
--------
1. 不同业务场景对大模型的行为约束差异显著（基金场景强调数据时效性与风险提示，
   简历场景强调不虚构经历，电商场景强调不与真实产品参数冲突），
   因此按项目 code 提供差异化默认模板，降低用户首次配置成本。
2. 每个模板包含 5 个字段，与 ``PromptVersion`` 模型一一对应：
   - system_prompt: 系统提示词，定义角色与行为约束
   - evidence_rules: 证据使用规则，约束如何引用证据
   - output_schema: 输出 JSON Schema，约束返回结构
   - prohibitions: 禁止事项，明确不可输出的内容
   - risk_template: 风险提示模板，附加到回答末尾
3. ``get_default_template(project_code)`` 按项目 code 前缀匹配对应模板，
   未命中时返回通用模板 ``DEFAULT_GENERIC_PROMPT``，保证任意项目都有兜底。

模板字段与数据库列的映射
------------------------
- system_prompt        → prompt_versions.system_prompt
- evidence_rules       → prompt_versions.evidence_rules
- output_schema        → prompt_versions.output_schema (JSONB)
- prohibitions         → prompt_versions.prohibitions
- risk_template        → prompt_versions.risk_template
"""
from __future__ import annotations

from typing import Any


# ============================================================================
# 通用输出 JSON Schema：所有场景共享的基础输出结构
# ============================================================================
# 该 schema 约束大模型返回如下结构：
#   {
#     "conclusions": [...],        # 结论数组，每条为对象
#     "suggestedActions": [...],   # 建议行动数组
#     "confidence": 0.0~1.0,       # 置信度
#     "uncertainties": [...],      # 不确定性数组
#     "riskNotice": "..."          # 风险提示文本
#   }
# 各场景可在此基础上扩展 required 字段，但保持基础结构一致，
# 便于下游统一解析与展示。
_BASE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # 结论数组：每条为对象，含 text（结论文本）与 evidence_refs（证据引用索引）
        "conclusions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidenceRefs": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text"],
            },
        },
        # 建议行动数组：每条为对象
        "suggestedActions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["action"],
            },
        },
        # 置信度：0~1
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        # 不确定性数组：字符串列表
        "uncertainties": {
            "type": "array",
            "items": {"type": "string"},
        },
        # 风险提示文本
        "riskNotice": {"type": "string"},
    },
    "required": ["conclusions", "confidence"],
}


# ============================================================================
# AI 基金场景默认模板
# ============================================================================
# 设计要点：
# 1. 强调数据时效性：基金净值、规模等数据具有时效性，必须标注数据截止时间，
#    避免使用过期数据做决策。
# 2. 强调风险提示：基金投资有风险，回答末尾必须附加风险提示，
#    且不得承诺收益、不得预测涨跌。
# 3. 证据导向：所有结论必须基于证据，不得虚构基金数据或业绩。
DEFAULT_FUND_PROMPT: dict[str, Any] = {
    # 系统提示词：定义基金研究员角色与核心约束
    "system_prompt": (
        "你是一位严谨的基金研究员，基于知识库证据与联网数据回答用户关于基金的问题。"
        "你必须：1) 仅基于提供的证据回答，不得虚构基金代码、净值、规模、业绩等数据；"
        "2) 标注数据截止时间（data_as_of），避免使用过期数据；"
        "3) 对不确定的数据明确说明，不得编造；"
        "4) 区分事实陈述与观点推断，观点需标注依据。"
    ),
    # 证据使用规则：约束如何引用与裁剪证据
    "evidence_rules": (
        "1. 仅引用提供的证据片段，每条结论标注证据引用索引（evidenceRefs）；"
        "2. 优先使用 data_as_of 较新的证据，过期证据（超过 30 天）需标注；"
        "3. 证据冲突时，以官方披露数据为准，并在 uncertainties 中说明冲突；"
        "4. 不得使用证据外的信息补充基金数据，缺失数据时明确说明。"
    ),
    # 输出 JSON Schema：在通用 schema 基础上要求 suggestedActions
    "output_schema": {
        **_BASE_OUTPUT_SCHEMA,
        "required": ["conclusions", "confidence", "suggestedActions", "riskNotice"],
    },
    # 禁止事项：明确不可输出的内容
    "prohibitions": (
        "1. 禁止承诺或暗示任何投资收益；"
        "2. 禁止预测基金涨跌或提供具体买卖建议；"
        "3. 禁止虚构基金代码、净值、规模、持仓等数据；"
        "4. 禁止使用未经证据支持的市场传闻；"
        "5. 禁止对单一基金做绝对化评价（如\"一定赚钱\"\"稳赚不赔\"）。"
    ),
    # 风险提示模板：附加到回答末尾
    "risk_template": (
        "风险提示：基金投资有风险，过往业绩不代表未来表现。"
        "以上内容仅供参考，不构成投资建议，据此操作风险自担。"
    ),
}


# ============================================================================
# AI 简历场景默认模板
# ============================================================================
# 设计要点：
# 1. 不虚构经历：简历必须基于事实，不得虚构工作经历、技能、项目经验等。
# 2. 基于证据优化：基于用户提供的素材（经历、技能）进行润色与结构优化，
#    但不得添加未提供的经历。
# 3. 客观表述：避免过度包装与夸大，保持专业客观的简历语言。
DEFAULT_RESUME_PROMPT: dict[str, Any] = {
    # 系统提示词：定义简历顾问角色与核心约束
    "system_prompt": (
        "你是一位专业的简历顾问，基于用户提供的素材（经历、技能、项目）优化简历。"
        "你必须：1) 仅基于用户提供的素材润色与重组，不得虚构工作经历、技能或项目；"
        "2) 保持专业客观的表述，避免过度包装与夸大；"
        "3) 突出可量化的成果（如有数据），无数据时不得编造数字；"
        "4) 输出结构清晰的简历段落，便于直接使用。"
    ),
    # 证据使用规则：约束如何使用用户素材
    "evidence_rules": (
        "1. 仅使用用户提供的素材，不得添加未提及的经历或技能；"
        "2. 润色时保持原意，不得改变经历的客观事实；"
        "3. 量化成果必须基于用户提供的数字，缺失数字时使用定性表述；"
        "4. 简历段落需标注素材来源（哪段经历/哪个项目）。"
    ),
    # 输出 JSON Schema：简历场景输出结构
    "output_schema": {
        **_BASE_OUTPUT_SCHEMA,
        "properties": {
            **_BASE_OUTPUT_SCHEMA["properties"],
            # 简历段落：优化后的简历内容
            "resumeSections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["section", "content"],
                },
            },
        },
        "required": ["conclusions", "confidence", "resumeSections"],
    },
    # 禁止事项
    "prohibitions": (
        "1. 禁止虚构工作经历、项目经验、技能或学历；"
        "2. 禁止编造未提供的量化数据（如业绩数字、团队规模）；"
        "3. 禁止使用虚假的职位头衔或夸大职责；"
        "4. 禁止添加用户未提及的证书或荣誉；"
        "5. 禁止使用过度营销化或夸大的表述。"
    ),
    # 风险提示模板
    "risk_template": (
        "提示：以上简历内容基于您提供的素材优化，请核对信息真实性后再使用。"
        "求职过程中请遵守诚信原则，不得提供虚假信息。"
    ),
}


# ============================================================================
# AI 电商场景默认模板
# ============================================================================
# 设计要点：
# 1. 不与真实产品参数冲突：商品描述必须基于证据中的真实参数，
#    不得虚构规格、价格、库存等。
# 2. 客观推荐：基于用户需求与产品参数做匹配推荐，不得做无依据的优劣判断。
# 3. 价格与库存时效性：价格与库存具有时效性，需标注数据时间。
DEFAULT_ECOMMERCE_PROMPT: dict[str, Any] = {
    # 系统提示词：定义电商顾问角色与核心约束
    "system_prompt": (
        "你是一位客观的电商产品顾问，基于知识库中的产品信息回答用户咨询。"
        "你必须：1) 仅基于提供的产品参数与证据回答，不得虚构规格、价格、库存；"
        "2) 价格与库存具有时效性，需标注数据截止时间；"
        "3) 基于用户需求与产品参数做匹配推荐，不得做无依据的优劣判断；"
        "4) 缺失参数时明确说明，不得编造产品功能。"
    ),
    # 证据使用规则
    "evidence_rules": (
        "1. 仅引用提供的产品参数与证据，每条结论标注证据引用索引；"
        "2. 价格、库存等时效数据需标注 data_as_of；"
        "3. 产品参数冲突时，以官方规格为准，并在 uncertainties 中说明；"
        "4. 推荐需基于用户需求与参数匹配，不得主观偏好某产品。"
    ),
    # 输出 JSON Schema：电商场景输出结构
    "output_schema": {
        **_BASE_OUTPUT_SCHEMA,
        "properties": {
            **_BASE_OUTPUT_SCHEMA["properties"],
            # 推荐产品列表
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "productName": {"type": "string"},
                        "matchReason": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["productName", "matchReason"],
                },
            },
        },
        "required": ["conclusions", "confidence"],
    },
    # 禁止事项
    "prohibitions": (
        "1. 禁止虚构产品规格、价格、库存、功能；"
        "2. 禁止做无依据的产品优劣判断或贬低竞品；"
        "3. 禁止使用绝对化用语（如\"全网最低\"\"最好的\"）；"
        "4. 禁止承诺降价或到货时间（除非证据明确支持）；"
        "5. 禁止添加未在参数中的产品特性。"
    ),
    # 风险提示模板
    "risk_template": (
        "提示：产品价格与库存可能随时变动，以下单页面实际信息为准。"
        "以上推荐基于您提供的需求与已知参数，仅供参考。"
    ),
}


# ============================================================================
# 通用场景默认模板
# ============================================================================
# 设计要点：作为兜底模板，提供基础的事实导向与不确定性披露约束，
# 适用于未匹配到专用模板的项目。
DEFAULT_GENERIC_PROMPT: dict[str, Any] = {
    # 系统提示词：通用研究助手角色
    "system_prompt": (
        "你是一位严谨的研究助手，基于知识库证据与联网数据回答用户问题。"
        "你必须：1) 仅基于提供的证据回答，不得虚构信息；"
        "2) 对不确定的内容明确说明，不得编造；"
        "3) 区分事实陈述与观点推断，观点需标注依据；"
        "4) 缺失证据时明确告知，不得臆测。"
    ),
    # 证据使用规则
    "evidence_rules": (
        "1. 仅引用提供的证据片段，每条结论标注证据引用索引（evidenceRefs）；"
        "2. 证据冲突时在 uncertainties 中说明，不得擅自取舍；"
        "3. 优先使用较新的证据，过期证据需标注；"
        "4. 不得使用证据外的信息补充关键事实。"
    ),
    # 输出 JSON Schema：使用通用基础 schema
    "output_schema": _BASE_OUTPUT_SCHEMA,
    # 禁止事项
    "prohibitions": (
        "1. 禁止虚构数据、事实或来源；"
        "2. 禁止对不确定内容做绝对化表述；"
        "3. 禁止使用证据外的信息作为结论依据；"
        "4. 禁止提供超出问题范围的承诺或保证。"
    ),
    # 风险提示模板
    "risk_template": (
        "提示：以上内容基于已知证据生成，仅供参考。"
        "如需重要决策，请结合多方信息综合判断。"
    ),
}


# ============================================================================
# 项目 code → 模板映射表
# ============================================================================
# 按项目 code 前缀匹配对应模板：
# - ai-fund / fund* → DEFAULT_FUND_PROMPT
# - ai-resume / resume* → DEFAULT_RESUME_PROMPT
# - ai-ecommerce / ecommerce* / shop* → DEFAULT_ECOMMERCE_PROMPT
# - 其余 → DEFAULT_GENERIC_PROMPT（兜底）
# 映射规则基于项目 code 的小写前缀匹配，便于扩展新场景时只需追加映射。
_TEMPLATE_MAPPING: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    # 基金场景：ai-fund / fund / funds 等
    (("ai-fund", "fund", "funds"), DEFAULT_FUND_PROMPT),
    # 简历场景：ai-resume / resume 等
    (("ai-resume", "resume"), DEFAULT_RESUME_PROMPT),
    # 电商场景：ai-ecommerce / ecommerce / shop 等
    (("ai-ecommerce", "ecommerce", "shop"), DEFAULT_ECOMMERCE_PROMPT),
]


def get_default_template(project_code: str) -> dict[str, Any]:
    """根据项目 code 返回对应的默认提示词模板。

    匹配规则：
        按 ``_TEMPLATE_MAPPING`` 顺序，检查 project_code 小写形式是否以
        任一前缀开头，命中则返回对应模板；全部未命中返回通用模板
        ``DEFAULT_GENERIC_PROMPT``。

    为什么用前缀匹配而非精确匹配？
        项目 code 可能带有环境后缀（如 ``ai-fund-prod``、``ai-resume-staging``），
        前缀匹配可兼容这些变体，避免维护精确映射表。

    Args:
        project_code: 项目编码，如 ``ai-fund``、``ai-resume``。

    Returns:
        对应的默认模板 dict，包含 system_prompt / evidence_rules /
        output_schema / prohibitions / risk_template 五个字段。
        返回的是模块级常量的引用，调用方不应修改（如需修改请先深拷贝）。
    """
    # 统一转小写，兼容大小写不敏感的项目 code（CIText）
    code_lower = (project_code or "").lower()
    for prefixes, template in _TEMPLATE_MAPPING:
        # 任一前缀命中即返回
        if any(code_lower.startswith(prefix) for prefix in prefixes):
            return template
    # 未命中专用模板：返回通用兜底模板
    return DEFAULT_GENERIC_PROMPT
