#!/usr/bin/env python3
"""AI 简历评测集示例样本种子脚本。

本脚本为开发和测试环境提供 30 个示例评测样本的框架。
这些样本仅为占位数据，用于验证评测系统完整性；
正式评测需用户提供真实脱敏的业务数据替换。

示例样本覆盖以下场景（执行书要求）：
- 正常命中：知识库有直接相关证据
- 部分命中：证据存在但不完全匹配
- 无命中：需依赖模型能力或拒答
- 过期证据：证据时效性问题
- 模型失败：Provider 不可用场景
- 格式异常：结构化输出验证

使用方式：
    python scripts/seed_evaluation_samples.py --dry-run  # 预览
    python scripts/seed_evaluation_samples.py --execute  # 执行
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# 示例样本定义
# 注意：这些是占位数据，标注 [示例] 以明确区分
SAMPLE_CASES: list[dict[str, Any]] = [
    # === 正常命中场景 (10 个) ===
    {
        "case_code": "resume_match_01",
        "question": "[示例] 这份简历是否适合软件工程师岗位？请分析技术栈匹配度。",
        "inputs": {"job_title": "软件工程师", "years_required": 3},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "HYBRID"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "match_score": {"type": "number", "minimum": 0, "maximum": 100},
                "key_matches": {"type": "array", "items": {"type": "string"}},
                "gaps": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["match_score"]
        },
        "labels": ["示例数据", "正常命中", "岗位匹配"]
    },
    {
        "case_code": "resume_match_02",
        "question": "[示例] 分析候选人的项目经验与产品经理岗位的关联性。",
        "inputs": {"job_title": "产品经理"},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {},
        "labels": ["示例数据", "正常命中", "项目经验"]
    },
    {
        "case_code": "resume_match_03",
        "question": "[示例] 请提取简历中的教育背景信息。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "degree": {"type": "string"},
                "major": {"type": "string"},
                "school": {"type": "string"}
            }
        },
        "labels": ["示例数据", "正常命中", "信息提取"]
    },
    {
        "case_code": "resume_match_04",
        "question": "[示例] 候选人有哪些与数据分析相关的技能？",
        "inputs": {"skill_category": "数据分析"},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {},
        "labels": ["示例数据", "正常命中", "技能识别"]
    },
    {
        "case_code": "resume_match_05",
        "question": "[示例] 简历中是否有与团队管理相关的经历？",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "HYBRID"],
        "expected_output_schema": {},
        "labels": ["示例数据", "正常命中", "管理经验"]
    },
    {
        "case_code": "resume_match_06",
        "question": "[示例] 候选人的工作经历时间线是否完整？有无空白期？",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "has_gaps": {"type": "boolean"},
                "gap_periods": {"type": "array"}
            }
        },
        "labels": ["示例数据", "正常命中", "时间线分析"]
    },
    {
        "case_code": "resume_match_07",
        "question": "[示例] 根据简历评估候选人的沟通能力。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "HYBRID"],
        "expected_output_schema": {},
        "labels": ["示例数据", "正常命中", "软技能"]
    },
    {
        "case_code": "resume_match_08",
        "question": "[示例] 简历中的技术关键词与后端开发岗位匹配情况如何？",
        "inputs": {"job_title": "后端开发"},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "matched_keywords": {"type": "array"},
                "missing_keywords": {"type": "array"}
            }
        },
        "labels": ["示例数据", "正常命中", "关键词匹配"]
    },
    {
        "case_code": "resume_match_09",
        "question": "[示例] 候选人是否有海外工作或学习经历？",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {},
        "labels": ["示例数据", "正常命中", "国际经历"]
    },
    {
        "case_code": "resume_match_10",
        "question": "[示例] 分析简历中展示的成就和量化指标。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "achievements": {"type": "array"},
                "has_metrics": {"type": "boolean"}
            }
        },
        "labels": ["示例数据", "正常命中", "成就分析"]
    },

    # === 部分命中场景 (6 个) ===
    {
        "case_code": "partial_match_01",
        "question": "[示例] 候选人是否具备 AI/机器学习经验？",
        "inputs": {"require_ai": True},
        "expected_answer_modes": ["HYBRID", "MODEL_ONLY"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "新兴技术"]
    },
    {
        "case_code": "partial_match_02",
        "question": "[示例] 评估候选人的行业背景与金融科技岗位的契合度。",
        "inputs": {"industry": "金融科技"},
        "expected_answer_modes": ["HYBRID"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "行业匹配"]
    },
    {
        "case_code": "partial_match_03",
        "question": "[示例] 简历中是否体现了创业或创新经历？",
        "inputs": {},
        "expected_answer_modes": ["HYBRID", "MODEL_ONLY"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "创新能力"]
    },
    {
        "case_code": "partial_match_04",
        "question": "[示例] 候选人的薪资期望是否在合理范围内？",
        "inputs": {"budget_max": 50000},
        "expected_answer_modes": ["HYBRID", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "薪资评估"]
    },
    {
        "case_code": "partial_match_05",
        "question": "[示例] 简历格式和排版是否专业？",
        "inputs": {},
        "expected_answer_modes": ["HYBRID"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "格式评估"]
    },
    {
        "case_code": "partial_match_06",
        "question": "[示例] 候选人是否有远程工作经验？",
        "inputs": {"remote_required": True},
        "expected_answer_modes": ["HYBRID", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "部分命中", "工作方式"]
    },

    # === 无命中/拒答场景 (6 个) ===
    {
        "case_code": "no_match_01",
        "question": "[示例] 请预测候选人未来五年的职业发展路径。",
        "inputs": {},
        "expected_answer_modes": ["INSUFFICIENT_EVIDENCE", "MODEL_ONLY"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "预测类问题"]
    },
    {
        "case_code": "no_match_02",
        "question": "[示例] 候选人的性格特点是什么？",
        "inputs": {},
        "expected_answer_modes": ["INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "主观推断"]
    },
    {
        "case_code": "no_match_03",
        "question": "[示例] 与同岗位其他候选人相比，这位候选人排名如何？",
        "inputs": {},
        "expected_answer_modes": ["INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "横向比较"]
    },
    {
        "case_code": "no_match_04",
        "question": "[示例] 候选人离职的真正原因是什么？",
        "inputs": {},
        "expected_answer_modes": ["INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "隐私信息"]
    },
    {
        "case_code": "no_match_05",
        "question": "[示例] 请推荐适合这位候选人的其他岗位。",
        "inputs": {},
        "expected_answer_modes": ["MODEL_ONLY", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "推荐类问题"]
    },
    {
        "case_code": "no_match_06",
        "question": "[示例] 候选人是否会在入职后短期内离职？",
        "inputs": {},
        "expected_answer_modes": ["INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "无命中", "风险预测"]
    },

    # === 过期证据场景 (4 个) ===
    {
        "case_code": "stale_evidence_01",
        "question": "[示例] 候选人的技术认证是否仍然有效？",
        "inputs": {"check_expiry": True},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "HYBRID"],
        "expected_output_schema": {},
        "labels": ["示例数据", "过期证据", "认证时效"]
    },
    {
        "case_code": "stale_evidence_02",
        "question": "[示例] 简历中的联系方式是否是最新的？",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "过期证据", "联系方式"]
    },
    {
        "case_code": "stale_evidence_03",
        "question": "[示例] 候选人目前是否仍在职？",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "过期证据", "在职状态"]
    },
    {
        "case_code": "stale_evidence_04",
        "question": "[示例] 简历上的项目是否还在运营？",
        "inputs": {},
        "expected_answer_modes": ["HYBRID", "INSUFFICIENT_EVIDENCE"],
        "expected_output_schema": {},
        "labels": ["示例数据", "过期证据", "项目状态"]
    },

    # === 格式异常场景 (4 个) ===
    {
        "case_code": "format_check_01",
        "question": "[示例] 提取简历中所有技能并按熟练程度分类。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "expert": {"type": "array", "items": {"type": "string"}},
                "proficient": {"type": "array", "items": {"type": "string"}},
                "familiar": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["expert", "proficient", "familiar"]
        },
        "labels": ["示例数据", "格式校验", "结构化输出"]
    },
    {
        "case_code": "format_check_02",
        "question": "[示例] 生成候选人的结构化简历摘要。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string", "maxLength": 500},
                "top_skills": {"type": "array", "maxItems": 5}
            },
            "required": ["summary"]
        },
        "labels": ["示例数据", "格式校验", "摘要生成"]
    },
    {
        "case_code": "format_check_03",
        "question": "[示例] 输出候选人工作经历的 JSON 数组。",
        "inputs": {},
        "expected_answer_modes": ["KNOWLEDGE_GROUNDED"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "experiences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "company": {"type": "string"},
                            "role": {"type": "string"},
                            "duration": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["experiences"]
        },
        "labels": ["示例数据", "格式校验", "数组结构"]
    },
    {
        "case_code": "format_check_04",
        "question": "[示例] 按评分维度给出候选人的综合评估。",
        "inputs": {},
        "expected_answer_modes": ["HYBRID"],
        "expected_output_schema": {
            "type": "object",
            "properties": {
                "technical_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "communication_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "experience_score": {"type": "integer", "minimum": 1, "maximum": 10},
                "overall_score": {"type": "number"}
            },
            "required": ["overall_score"]
        },
        "labels": ["示例数据", "格式校验", "评分输出"]
    },
]


@dataclass
class SeedResult:
    """种子执行结果。"""
    evaluation_set_code: str
    evaluation_set_name: str
    total_cases: int
    case_codes: list[str]


def preview_samples() -> None:
    """预览示例样本数据。"""
    print("=" * 60)
    print("AI 简历评测集示例样本预览")
    print("=" * 60)
    print(f"\n评测集编码: ai_resume_examples")
    print(f"评测集名称: AI 简历评测示例集")
    print(f"样本总数: {len(SAMPLE_CASES)}")
    print("\n样本分布:")
    
    # 按标签统计
    label_counts: dict[str, int] = {}
    for case in SAMPLE_CASES:
        for label in case.get("labels", []):
            if label != "示例数据":
                label_counts[label] = label_counts.get(label, 0) + 1
    
    for label, count in sorted(label_counts.items()):
        print(f"  - {label}: {count} 个")
    
    print("\n样本列表:")
    for i, case in enumerate(SAMPLE_CASES, 1):
        labels = ", ".join(l for l in case.get("labels", []) if l != "示例数据")
        print(f"  {i:2d}. [{case['case_code']}] {labels}")
        print(f"      问题: {case['question'][:50]}...")
    
    print("\n" + "=" * 60)
    print("注意: 这些是示例数据，仅用于验证评测系统。")
    print("正式使用时需替换为真实脱敏的业务数据。")
    print("=" * 60)


def generate_seed_sql() -> str:
    """生成种子数据的 SQL 语句（用于手动执行或迁移）。"""
    lines = [
        "-- AI 简历评测集示例数据种子",
        "-- 生成时间: " + datetime.now().isoformat(),
        "-- 注意: 这些是示例数据，需要真实数据替换后才能用于正式评测",
        "",
        "-- 需要先有应用和环境，此处仅展示样本结构",
        "-- 实际插入需要替换 application_id 和 environment_id",
        "",
    ]
    
    lines.append("-- 样本数据 JSON 参考:")
    lines.append("/*")
    lines.append(json.dumps(SAMPLE_CASES, ensure_ascii=False, indent=2))
    lines.append("*/")
    
    return "\n".join(lines)


def main():
    """主入口。"""
    parser = argparse.ArgumentParser(
        description="AI 简历评测集示例样本种子脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="预览模式，不执行实际操作"
    )
    parser.add_argument(
        "--execute",
        action="store_true", 
        help="执行模式，需要数据库连接"
    )
    parser.add_argument(
        "--output-sql",
        type=str,
        help="输出 SQL 种子文件路径"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        help="输出 JSON 样本文件路径"
    )
    
    args = parser.parse_args()
    
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_CASES, f, ensure_ascii=False, indent=2)
        print(f"已导出 JSON 样本到: {args.output_json}")
        return
    
    if args.output_sql:
        sql = generate_seed_sql()
        with open(args.output_sql, "w", encoding="utf-8") as f:
            f.write(sql)
        print(f"已导出 SQL 种子到: {args.output_sql}")
        return
    
    if args.dry_run:
        preview_samples()
        return
    
    if args.execute:
        print("执行模式需要数据库连接，请使用 API 或迁移脚本导入示例数据。")
        print("或者使用 --output-json 导出后通过 API 批量导入。")
        return
    
    # 默认显示帮助
    parser.print_help()


if __name__ == "__main__":
    main()
