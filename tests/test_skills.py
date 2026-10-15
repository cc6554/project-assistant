from pathlib import Path

import pytest

from agent_coach.skills import SkillCatalog


def _write_skill(root: Path, name: str, description: str = "An external skill", body: str = "Use it.") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_builtin_catalog_and_external_prompt_skill(tmp_path: Path):
    external = _write_skill(tmp_path, "code-review", body="Read files and report evidence.")
    catalog = SkillCatalog(extra_dirs=[tmp_path])

    names = {item["name"] for item in catalog.list()}
    assert names == {"job-radar", "code-review"}
    assert catalog.get("code-review").instructions == "Read files and report evidence."
    prompt = catalog.instructions(["code-review", "job-radar", "code-review"])
    assert prompt.count("## Skill: code-review") == 1
    assert "## Skill: job-radar" in prompt

    # A direct skill directory is accepted in addition to its parent directory.
    direct = SkillCatalog(extra_dirs=[external], include_builtin=False)
    assert direct.list() == [{"name": "code-review", "description": "An external skill"}]


def test_catalog_rejects_duplicate_and_invalid_metadata(tmp_path: Path):
    _write_skill(tmp_path, "job-radar", body="shadow")
    with pytest.raises(ValueError, match="Duplicate skill name"):
        SkillCatalog(extra_dirs=[tmp_path])

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\ndescription: no name\n---\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid skill metadata"):
        SkillCatalog(extra_dirs=[bad], include_builtin=False)


def test_skill_document_is_prompt_only(tmp_path: Path):
    _write_skill(tmp_path, "prompt-only", body="```python\nraise RuntimeError('must not run')\n```")
    skill = SkillCatalog(extra_dirs=[tmp_path], include_builtin=False).get("prompt-only")
    assert "RuntimeError" in skill.instructions
