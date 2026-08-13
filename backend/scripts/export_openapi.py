from __future__ import annotations

import json
from pathlib import Path

from knowledge_core.main import app


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    target = project_root / "frontend" / "packages" / "contracts" / "openapi.json"
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"exported {target}")


if __name__ == "__main__":
    main()
