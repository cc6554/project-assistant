import pytest

from agent_coach.legacy.tools.jd_parser import parse_jd_text
from agent_coach.legacy.tools.profile_builder import build_profile_from_text


def test_full_jd_requirement_after_600_characters_reaches_model():
    captured = []

    class Router:
        def complete_json(self, task, messages, schema, **kwargs):
            captured.extend(messages)
            return {"job_title": "Backend engineer"}

    parse_jd_text(Router(), ["岗位介绍" * 200 + "TAIL_REQUIRED_SKILL"])
    assert "TAIL_REQUIRED_SKILL" in captured[-1].content


def test_oversized_jd_is_explicitly_rejected_before_model_call():
    with pytest.raises(ValueError, match="64000"):
        parse_jd_text(None, ["x" * 64001])


def test_large_profile_is_not_silently_truncated():
    with pytest.raises(ValueError, match="12000"):
        build_profile_from_text(None, "x" * 12001, source="self_report")
