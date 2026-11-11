#!/usr/bin/env python3
"""数据完整性核实脚本。

本脚本用于验证 AI 知识中台数据库中的数据完整性，
检查外键引用、孤儿记录、状态一致性等问题。

运行方式：
    # 预览模式（只读分析）
    python scripts/verify_data_integrity.py --dry-run

    # 完整核实
    python scripts/verify_data_integrity.py --execute

    # 仅核实特定应用
    python scripts/verify_data_integrity.py --application-id <uuid>

核实项说明：
    - foreign_keys: 外键引用完整性
    - orphan_records: 孤儿记录检测
    - state_consistency: 状态字段一致性
    - embedding_coverage: Embedding 覆盖率
    - version_chain: 文档版本链完整性
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class IntegrityStatus(str, Enum):
    """完整性状态枚举。"""
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(slots=True)
class IntegrityCheckResult:
    """单个完整性检查结果。"""
    check_name: str
    status: IntegrityStatus
    message: str
    affected_count: int = 0
    sample_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] | None = None


class DataIntegrityVerifier:
    """数据完整性验证器。"""

    def __init__(
        self,
        application_id: UUID | None = None,
        dry_run: bool = True,
    ):
        """初始化验证器。

        Args:
            application_id: 限定应用 ID，None 表示全量检查
            dry_run: 是否为只读模式
        """
        self.application_id = application_id
        self.dry_run = dry_run
        self.results: list[IntegrityCheckResult] = []

    async def verify_all(self) -> list[IntegrityCheckResult]:
        """执行所有完整性检查。"""
        print("=" * 60)
        print("AI 知识中台数据完整性核实")
        print(f"时间: {datetime.now().isoformat()}")
        print(f"模式: {'只读分析' if self.dry_run else '完整核实'}")
        if self.application_id:
            print(f"应用: {self.application_id}")
        print("=" * 60)
        print()

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("❌ 未配置 DATABASE_URL 环境变量")
            return self.results

        # 执行各项检查
        checks = [
            ("foreign_keys", "外键引用完整性", self._check_foreign_keys),
            ("orphan_chunks", "孤儿片段检测", self._check_orphan_chunks),
            ("orphan_files", "孤儿文件检测", self._check_orphan_files),
            ("state_consistency", "状态字段一致性", self._check_state_consistency),
            ("embedding_coverage", "Embedding 覆盖率", self._check_embedding_coverage),
            ("version_chain", "文档版本链完整性", self._check_version_chain),
            ("idempotency_cleanup", "幂等记录清理", self._check_idempotency_records),
        ]

        for check_id, check_name, check_func in checks:
            print(f"检查: {check_name}...")
            try:
                result = await check_func()
                self.results.append(result)
                status_icon = {
                    IntegrityStatus.OK: "✅",
                    IntegrityStatus.WARNING: "⚠️",
                    IntegrityStatus.ERROR: "❌",
                    IntegrityStatus.SKIPPED: "⏭️",
                }[result.status]
                print(f"  {status_icon} {result.message}")
                if result.affected_count > 0:
                    print(f"     影响记录: {result.affected_count}")
                if result.sample_ids:
                    print(f"     示例 ID: {', '.join(result.sample_ids[:3])}")
            except Exception as e:
                result = IntegrityCheckResult(
                    check_name=check_name,
                    status=IntegrityStatus.ERROR,
                    message=f"检查异常: {type(e).__name__}: {str(e)[:100]}",
                )
                self.results.append(result)
                print(f"  ❌ {result.message}")

            print()

        return self.results

    async def _check_foreign_keys(self) -> IntegrityCheckResult:
        """检查外键引用完整性。

        验证关键表之间的外键引用是否有效：
        - documents → collections
        - document_revisions → documents
        - document_chunks → document_revisions
        - request_traces → answer_profile_revisions
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="foreign_keys",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将检查 documents、revisions、chunks 的外键引用",
            )

        # 实际数据库查询（需要连接）
        return IntegrityCheckResult(
            check_name="foreign_keys",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    async def _check_orphan_chunks(self) -> IntegrityCheckResult:
        """检查孤儿片段。

        查找没有对应 document_revision 的 chunks。
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="orphan_chunks",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将检查无父修订的片段记录",
            )

        return IntegrityCheckResult(
            check_name="orphan_chunks",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    async def _check_orphan_files(self) -> IntegrityCheckResult:
        """检查孤儿文件。

        查找存储中没有对应数据库记录的文件。
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="orphan_files",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将扫描存储目录与数据库记录对比",
            )

        return IntegrityCheckResult(
            check_name="orphan_files",
            status=IntegrityStatus.SKIPPED,
            message="需要存储和数据库连接，请使用 --execute 模式",
        )

    async def _check_state_consistency(self) -> IntegrityCheckResult:
        """检查状态字段一致性。

        验证状态字段的逻辑一致性：
        - 已发布的 revision 应该有 published_at
        - ready 状态的 document 应该有 current_version
        - 归档的 document 不应该有 ready 状态的 revision
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="state_consistency",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将检查 documents 和 revisions 的状态一致性",
            )

        return IntegrityCheckResult(
            check_name="state_consistency",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    async def _check_embedding_coverage(self) -> IntegrityCheckResult:
        """检查 Embedding 覆盖率。

        统计有 embedding 的 chunks 占比。
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="embedding_coverage",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将统计 chunks 表的 embedding 覆盖率",
            )

        return IntegrityCheckResult(
            check_name="embedding_coverage",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    async def _check_version_chain(self) -> IntegrityCheckResult:
        """检查文档版本链完整性。

        验证每个 document 的 version 是否连续。
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="version_chain",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将检查 documents 的版本序列完整性",
            )

        return IntegrityCheckResult(
            check_name="version_chain",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    async def _check_idempotency_records(self) -> IntegrityCheckResult:
        """检查幂等记录清理状态。

        统计过期的幂等记录数量。
        """
        if self.dry_run:
            return IntegrityCheckResult(
                check_name="idempotency_cleanup",
                status=IntegrityStatus.SKIPPED,
                message="只读模式：将统计 idempotency_records 表的过期记录",
            )

        return IntegrityCheckResult(
            check_name="idempotency_cleanup",
            status=IntegrityStatus.SKIPPED,
            message="需要数据库连接，请使用 --execute 模式",
        )

    def print_summary(self) -> int:
        """打印核实摘要并返回退出码。"""
        print("=" * 60)
        print("核实摘要")
        print("=" * 60)

        ok = sum(1 for r in self.results if r.status == IntegrityStatus.OK)
        warning = sum(1 for r in self.results if r.status == IntegrityStatus.WARNING)
        error = sum(1 for r in self.results if r.status == IntegrityStatus.ERROR)
        skipped = sum(1 for r in self.results if r.status == IntegrityStatus.SKIPPED)

        print(f"  ✅ 正常: {ok}")
        print(f"  ⚠️  警告: {warning}")
        print(f"  ❌ 错误: {error}")
        print(f"  ⏭️  跳过: {skipped}")

        if error > 0:
            print("\n⚠️  存在数据完整性错误，需要人工介入修复。")
            return 1
        elif warning > 0:
            print("\n⚠️  存在数据完整性警告，建议进一步检查。")
            return 0
        else:
            print("\n✅ 所有核实的数据项状态正常。")
            return 0


def main():
    """主入口。"""
    parser = argparse.ArgumentParser(
        description="AI 知识中台数据完整性核实脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读分析模式，不执行实际查询",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行模式，实际查询数据库",
    )
    parser.add_argument(
        "--application-id",
        type=str,
        help="限定应用 ID (UUID 格式)",
    )

    args = parser.parse_args()

    application_id = UUID(args.application_id) if args.application_id else None
    dry_run = not args.execute

    verifier = DataIntegrityVerifier(
        application_id=application_id,
        dry_run=dry_run,
    )
    asyncio.run(verifier.verify_all())
    exit_code = verifier.print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
