"""最小闭环示例：岗位截图 → JDCard。

准备：
  1. cp .env.example .env        填入至少一家的 API key
  2. cp config/providers.example.yaml config/providers.yaml
  3. pip install -e .

运行：
  python examples/parse_jd_example.py path/to/boss_shot1.png [shot2.png ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from coach.core.llm.router import ModelRouter
from coach.tools.jd_parser import parse_jd_screenshots


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python examples/parse_jd_example.py 截图1.png [截图2.png ...]")
        sys.exit(1)

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")

    router = ModelRouter.from_yaml(root / "config" / "providers.yaml")
    card = parse_jd_screenshots(router, sys.argv[1:])

    print(json.dumps(card.model_dump(), ensure_ascii=False, indent=2))
    if card.uncertain_fields:
        print("\n⚠️ 以下字段识别不确定，请人工确认：", card.uncertain_fields)


if __name__ == "__main__":
    main()
