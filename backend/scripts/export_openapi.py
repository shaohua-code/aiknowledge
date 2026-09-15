"""跨平台导出 FastAPI 当前公开契约，供前端生成唯一 TypeScript 类型源。"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_core.main import app


def main() -> None:
    """将当前后端 OpenAPI 快照写入契约包，不修改业务数据。"""
    project_root = Path(__file__).resolve().parents[2]
    target = project_root / "frontend" / "packages" / "contracts" / "openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"exported {target}")


if __name__ == "__main__":
    main()
