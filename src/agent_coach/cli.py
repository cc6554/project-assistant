"""Command-line interface for the persistent job-coaching Agent."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Sequence

from .bootstrap import build_engine, doctor_config, init_config
from .engine import AgentEngine, ApprovalMismatch, EngineError, RunResult
from .store import SessionNotFound, SessionStore, StoreError, validate_session_id


HELP_TEXT = """可用命令：
  /help                 显示帮助
  /status               查看当前会话状态
  /plan                 查看当前计划状态
  /new                  创建并切换到新会话
  /resume ID            切换到已有会话
  /continue             恢复当前会话中未完成的工具队列
  /approve ID           执行精确匹配的待审批操作
  /reject ID            拒绝精确匹配的待审批操作
  /resolve ID [备注]    人工确认不确定结果（绝不重放工具）
  /quit                 退出（当前状态会保留）
输入普通文本即可继续同一条求职辅导对话；单次 run 遇待审批会以退出码 3 返回。
截图或图片可通过 run 的 --attachment PATH 附加到同一轮 user 消息。
"""


def _runtime_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument("--workspace", default=default, help="workspace 目录（默认当前目录）")
    parser.add_argument("--config", default=default, help="显式 provider 配置路径")
    parser.add_argument("--session", default=default, help="要继续的 session id")
    parser.add_argument("--demo", action="store_true", default=argparse.SUPPRESS if suppress_defaults else False,
                        help="使用离线脚本模型")
    parser.add_argument("--no-skills", action="store_true", default=argparse.SUPPRESS if suppress_defaults else False,
                        help="开发诊断：禁用求职工作流与领域工具")
    parser.add_argument(
        "--attachment", "--image", dest="attachment", action="append", default=default,
        help="附加 workspace 内图片到同一轮求职对话（可重复）",
    )
    parser.add_argument("--max-steps", type=int, default=default, help="每次模型循环最多步数")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-coach", description="校招雷达 · 持久化求职辅导 CLI")
    _runtime_options(parser)
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="生成 provider 配置模板")
    _runtime_options(init_parser, suppress_defaults=True)
    init_parser.add_argument("--force", action="store_true", help="覆盖目标配置")

    doctor_parser = sub.add_parser("doctor", help="检查配置结构（不联网）")
    _runtime_options(doctor_parser, suppress_defaults=True)

    skills_parser = sub.add_parser("skills", help="列出 skills")
    _runtime_options(skills_parser, suppress_defaults=True)

    sessions_parser = sub.add_parser("sessions", help="列出持久化会话")
    _runtime_options(sessions_parser, suppress_defaults=True)

    run_parser = sub.add_parser("run", help="执行一次请求")
    _runtime_options(run_parser, suppress_defaults=True)
    run_parser.add_argument("prompt", nargs="+", help="用户请求")
    return parser


def _workspace(args: argparse.Namespace) -> Path:
    return Path(args.workspace or Path.cwd()).expanduser().resolve(strict=False)


def _config(args: argparse.Namespace) -> Path | None:
    value = getattr(args, "config", None)
    return Path(value).expanduser().resolve(strict=False) if value else None


def _event_printer(event: dict[str, Any]) -> None:
    kind = event.get("type", "event")
    message = str(event.get("message", ""))
    print(f"[{kind}] {message}")


def _build(args: argparse.Namespace, *, on_event: bool = False) -> AgentEngine:
    return build_engine(
        _workspace(args),
        config_path=_config(args),
        demo=bool(args.demo),
        no_skills=bool(args.no_skills),
        max_steps=max(1, int(args.max_steps or 8)),
        on_event=_event_printer if on_event else None,
    )


def _ensure_session(engine: AgentEngine, requested: str | None) -> str:
    if requested:
        validate_session_id(requested)
        engine.get_session(requested)
        return requested
    return engine.new_session().id


def _print_result(result: RunResult) -> None:
    if result.assistant_text:
        print(result.assistant_text)
    if result.status == "pending_approval" and result.pending is not None:
        print(f"等待审批：{result.pending.id}；{result.pending.summary}")
        if result.pending.preview:
            print(f"审批预览：{result.pending.preview}")
    elif result.status in {"paused", "uncertain", "error", "cancelled", "resolved"} and result.message:
        print(result.message)


def _command_init(args: argparse.Namespace) -> int:
    destination = _config(args) or (_workspace(args) / "config" / "providers.yaml")
    try:
        path = init_config(destination, force=bool(args.force))
    except FileExistsError:
        print(f"配置已存在：{destination}（如需覆盖请使用 --force）")
        return 2
    except OSError as exc:
        print(f"无法生成配置模板：{type(exc).__name__}")
        return 2
    print(f"已生成无密钥配置模板：{path}")
    return 0


def _command_doctor(args: argparse.Namespace) -> int:
    result = doctor_config(_config(args))
    print(result["message"])
    for provider in result.get("providers", []):
        key_state = "已声明环境变量" if provider.get("api_key_configured") else "未配置 key（可用于本地端点）"
        print(f"provider {provider['name']}: {provider['api_mode']}，{key_state}")
    if result.get("tasks"):
        print("tasks：" + ", ".join(result["tasks"]))
    for error in result.get("errors", []):
        print("问题：" + str(error))
    return 0 if result.get("ok") else 2


def _command_sessions(args: argparse.Namespace) -> int:
    store = SessionStore(_workspace(args))
    for session in store.list():
        pending = f" pending={session.pending.id}" if session.pending else ""
        print(f"{session.id} rev={session.revision} messages={len(session.messages)}{pending}")
    return 0


def _command_skills(args: argparse.Namespace) -> int:
    try:
        engine = _build(args)
    except Exception as exc:
        print(f"无法加载 skills：{type(exc).__name__}")
        return 2
    rows = engine.available_skills()
    if not rows:
        print("没有可用 skills（可能已使用 --no-skills）")
        return 0
    for row in rows:
        name = row.get("name", "")
        description = row.get("description", "")
        print(f"{name}: {str(description).replace(chr(10), ' ')[:240]}")
    return 0


def _command_run(args: argparse.Namespace) -> int:
    if args.demo:
        print("离线演示模型：脚本驱动，不联网；写入操作仍需审批。")
    try:
        engine = _build(args, on_event=True)
        result = engine.run(
            " ".join(args.prompt),
            session_id=args.session,
            attachments=getattr(args, "attachment", None),
        )
    except (SessionNotFound, StoreError, ValueError) as exc:
        print(f"无法执行：{str(exc)}")
        return 2
    except Exception as exc:
        print(f"无法启动：{type(exc).__name__}")
        return 2
    print(f"会话：{result.session.id}")
    _print_result(result)
    return result.exit_code


def _repl(engine: AgentEngine, session_id: str) -> int:
    print("校招雷达 已启动。输入 /help 查看命令；/quit 退出。")
    while True:
        try:
            raw = input(f"[{session_id[:8]}] > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        text = raw.strip()
        if not text:
            continue
        if text.startswith("/"):
            try:
                parts = shlex.split(text)
            except ValueError as exc:
                print(f"命令格式错误：{exc}")
                continue
            command = parts[0].lower()
            if command == "/quit":
                return 0
            if command == "/help":
                print(HELP_TEXT)
                continue
            try:
                if command == "/skills":
                    rows = engine.available_skills()
                    print("\n".join(f"{row.get('name', '')}: {row.get('description', '')}" for row in rows) or "没有可用 skills")
                elif command == "/skill" and len(parts) == 2:
                    engine.activate_skill(session_id, parts[1])
                    print(f"已挂载 skill：{parts[1]}")
                elif command == "/unskill" and len(parts) == 2:
                    engine.deactivate_skill(session_id, parts[1])
                    print(f"已停用 skill：{parts[1]}")
                elif command == "/status":
                    print(json.dumps(engine.status(session_id), ensure_ascii=False, indent=2))
                elif command == "/plan":
                    print(json.dumps(engine.get_session(session_id).plan, ensure_ascii=False, indent=2))
                elif command == "/new":
                    session_id = engine.new_session().id
                    print(f"已切换到新会话：{session_id}")
                elif command == "/resume" and len(parts) == 2:
                    session_id = _ensure_session(engine, parts[1])
                    print(f"已切换会话：{session_id}")
                elif command == "/continue" and len(parts) == 1:
                    result = engine.continue_session(session_id)
                    _print_result(result)
                elif command in {"/approve", "/reject"} and len(parts) == 2:
                    result = engine.approve(session_id, parts[1]) if command == "/approve" else engine.reject(session_id, parts[1])
                    _print_result(result)
                elif command == "/resolve" and len(parts) >= 2:
                    result = engine.resolve_uncertain(session_id, parts[1], " ".join(parts[2:]))
                    _print_result(result)
                else:
                    print("命令格式错误，输入 /help 查看用法")
            except ApprovalMismatch as exc:
                print(f"审批失败：{exc}")
            except (EngineError, StoreError, ValueError) as exc:
                print(f"命令失败：{exc}")
            continue

        try:
            result = engine.run(text, session_id=session_id)
            _print_result(result)
        except Exception as exc:
            print(f"执行失败：{type(exc).__name__}：{str(exc)[:240]}")


def _command_repl(args: argparse.Namespace) -> int:
    if args.demo:
        print("离线演示模型：脚本驱动，不联网；写入操作仍需审批。")
    try:
        engine = _build(args, on_event=True)
        session_id = _ensure_session(engine, args.session)
    except Exception as exc:
        print(f"无法启动：{type(exc).__name__}：{str(exc)[:240]}")
        return 2
    return _repl(engine, session_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "init":
        return _command_init(args)
    if args.command == "doctor":
        return _command_doctor(args)
    if args.command == "skills":
        return _command_skills(args)
    if args.command == "sessions":
        return _command_sessions(args)
    if args.command == "run":
        return _command_run(args)
    return _command_repl(args)


__all__ = ["HELP_TEXT", "build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover - exercised by direct module use
    raise SystemExit(main())
