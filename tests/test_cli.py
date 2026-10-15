from __future__ import annotations

from pathlib import Path

from agent_coach.cli import build_parser, main


def test_parser_supports_global_options_before_and_after_run():
    first = build_parser().parse_args(["--workspace", "x", "--demo", "run", "hello"])
    second = build_parser().parse_args(["run", "hello", "--workspace", "x", "--demo"])
    assert first.command == second.command == "run"
    assert first.workspace == second.workspace == "x"
    assert first.demo and second.demo


def test_init_and_doctor_do_not_print_or_require_secrets(tmp_path, capsys):
    config = tmp_path / "config" / "providers.yaml"
    assert main(["init", "--workspace", str(tmp_path), "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert config.exists()
    assert "api_key" not in output.lower()
    assert main(["doctor", "--config", str(config)]) == 0
    doctor_output = capsys.readouterr().out
    assert "sk-" not in doctor_output


def test_sessions_command_is_available_without_provider_config(tmp_path, capsys):
    assert main(["sessions", "--workspace", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


def test_demo_run_shows_preview_and_pending_exit_code(tmp_path, capsys):
    code = main(["run", "--workspace", str(tmp_path), "--demo", "--no-skills", "show demo"])
    output = capsys.readouterr().out
    assert code == 3
    assert "离线演示模型" in output
    assert "审批预览" in output
    assert "会话" in output
