"""连通性自检：逐个调用 providers.yaml 里已配置 key 的厂商，报告谁可用。

运行：python examples/check_providers.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from coach.core.llm.base import ProviderError
from coach.core.llm.router import ModelRouter
from coach.core.llm.registry import load_config
from coach.core.schema import Message


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")

    cfg = load_config(root / "config" / "providers.yaml")
    router = ModelRouter(cfg)

    for name, entry in cfg.providers.items():
        if entry.key_required and entry.api_key is None:
            print(f"⏭️  {name:<16} 未配置 key，跳过")
            continue
        try:
            client = router._build_client(entry, model="connectivity-check")
            client.complete(
                [Message(role="user", content="只回复两个字：正常")],
                max_tokens=16,
            )
            print(f"✅ {name:<16} 连通（{entry.api_mode}）")
        except ProviderError as exc:
            # 模型名不存在等 4xx 也说明网络/鉴权层面的信息，原样展示
            print(f"❌ {name:<16} {exc}")
        except Exception as exc:  # noqa: BLE001 - 自检脚本需要展示全部失败原因
            print(f"❌ {name:<16} {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
