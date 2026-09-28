"""命令行入口：job-radar

交互式 REPL，由智能体驱动整条工作流。会话自动保存在 data/session.json。

也提供两个实用子命令：
  job-radar check         自检已配置的 provider 连通性
  job-radar jd 图1 图2    不走智能体，直接测试截图识别
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .core.llm.router import ModelRouter
from .tools.jd_parser import parse_jd_screenshots
from .tools.session import SessionState

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "providers.yaml"
SESSION_PATH = PROJECT_ROOT / "data" / "session.json"

BANNER = """\
============================================================
 校招雷达·学习规划师（job-radar-coach）
 输入你的需求开始对话，例如：
   「我刚截了两张岗位图，路径是 ./shots/1.png ./shots/2.png」
   「这是我的简历 ./resume.pdf，帮我分析技能」
   「我直接说下我会什么：……」
   「开始访谈吧」
 命令：/status 查看进度   /save 保存会话   /quit 退出
============================================================"""


def _load_router(config: Path) -> ModelRouter:
    load_dotenv(PROJECT_ROOT / ".env")
    return ModelRouter.from_yaml(config)


def run_repl(config: Path) -> None:
    from .agent.loop import CoachAgent

    router = _load_router(config)
    state = SessionState.load(SESSION_PATH)
    agent = CoachAgent(
        router,
        state,
        on_tool=lambda desc: print(f"  ⚙️  调用工具: {desc}"),
    )

    print(BANNER)
    if state.jd:
        print(f"（已恢复会话：岗位「{state.jd.job_title}」，技能 {len(state.profile.skills) if state.profile else 0} 项）")

    while True:
        try:
            user_text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_text:
            continue
        if user_text in {"/quit", "/exit"}:
            break
        if user_text == "/save":
            state.save(SESSION_PATH)
            print(f"已保存到 {SESSION_PATH}")
            continue
        if user_text == "/status":
            print(json.dumps(json.loads(agent.registry["get_current_status"].handler({})),
                             ensure_ascii=False, indent=2))
            continue

        try:
            reply = agent.chat(user_text)
        except Exception as exc:  # noqa: BLE001 - REPL 中保留会话不崩溃
            print(f"⚠️ 出错了：{type(exc).__name__}: {exc}")
            continue
        print(f"\n教练 > {reply}")
        state.save(SESSION_PATH)

    state.save(SESSION_PATH)
    print("\n会话已保存，再见。")


def run_check(config: Path) -> None:
    # 复用 examples 里的自检逻辑
    root = PROJECT_ROOT
    load_dotenv(root / ".env")
    from .core.llm.base import ProviderError
    from .core.llm.registry import load_config
    from .core.schema import Message

    cfg = load_config(config)
    router = ModelRouter(cfg)
    for name, entry in cfg.providers.items():
        if entry.key_required and entry.api_key is None:
            print(f"[skip] {name:<16} 未配置 key")
            continue
        try:
            client = router._build_client(entry, model="connectivity-check")
            client.complete([Message(role="user", content="ok")], max_tokens=8)
            print(f"[ok]   {name:<16} 连通")
        except ProviderError as exc:
            print(f"[fail] {name:<16} {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {name:<16} {type(exc).__name__}: {exc}")


def run_jd(config: Path, images: list[str]) -> None:
    router = _load_router(config)
    card = parse_jd_screenshots(router, images)
    print(json.dumps(card.model_dump(), ensure_ascii=False, indent=2))
    if card.uncertain_fields:
        print("\n识别不确定字段（请人工确认）：", card.uncertain_fields)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="job-radar", description="校招雷达·学习规划师")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="自检 provider 连通性")

    jd_parser = sub.add_parser("jd", help="直接解析岗位截图")
    jd_parser.add_argument("images", nargs="+")

    args = parser.parse_args(argv)

    if args.command == "check":
        run_check(args.config)
    elif args.command == "jd":
        run_jd(args.config, args.images)
    else:
        run_repl(args.config)


if __name__ == "__main__":
    main()
