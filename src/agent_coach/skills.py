"""Prompt based, discoverable skills for the general agent.

Skills are deliberately data only.  A skill is a Markdown document with a
small YAML front matter block and an instruction body; loading one never
imports or executes code from the document.  Domain tools (for example the
optional job-radar tools) are registered separately and are gated by the
kernel through :class:`~agent_coach.contracts.ToolDefinition.skill`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_FRONT_MATTER_START = "---"


class _StrictLoader(yaml.SafeLoader):
    """YAML loader that treats duplicate metadata keys as an error."""


def _construct_mapping(loader: _StrictLoader, node: yaml.Node, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing skill metadata",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True, slots=True)
class Skill:
    """One prompt skill loaded from a ``SKILL.md`` file."""

    name: str
    description: str
    instructions: str


def _invalid(path: Path, reason: str) -> ValueError:
    return ValueError(f"Invalid skill metadata in {path}: {reason}")


def _parse_skill(path: Path) -> Skill:
    """Parse and validate one skill document.

    The parser is intentionally strict about the small public metadata
    contract.  This keeps a malformed plug-in from silently replacing another
    skill or from becoming an accidental, unnamed system prompt.
    """

    if not path.is_file() or path.name.upper() != "SKILL.MD":
        raise _invalid(path, "expected a SKILL.md file")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _invalid(path, f"cannot read file ({exc})") from exc

    # A front matter block must begin at the beginning of the Markdown file.
    # Accept a UTF-8 BOM because editors on Windows sometimes leave one.
    text = raw.removeprefix("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_START:
        raise _invalid(path, "YAML front matter must start with ---")

    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            end = index
            break
    if end is None:
        raise _invalid(path, "YAML front matter is not closed")

    front = "\n".join(lines[1:end])
    try:
        metadata = yaml.load(front, Loader=_StrictLoader) if front.strip() else None
    except yaml.YAMLError as exc:
        raise _invalid(path, f"invalid YAML ({exc})") from exc
    if not isinstance(metadata, dict):
        raise _invalid(path, "front matter must be a mapping")

    # ``name`` and ``description`` are the required public fields.  Additional
    # metadata is retained by neither the runtime nor the prompt, but is
    # harmless and lets external skill authors keep their own version/author
    # annotations without making the catalog less portable.
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise _invalid(path, "name must be a non-empty string")
    if not _NAME_RE.fullmatch(name.strip()):
        raise _invalid(path, "name must contain lowercase letters, digits, '.', '_' or '-'")
    if not isinstance(description, str) or not description.strip():
        raise _invalid(path, "description must be a non-empty string")

    instructions = "\n".join(lines[end + 1 :]).strip()
    return Skill(name=name.strip(), description=description.strip(), instructions=instructions)


def _skill_files(directory: Path) -> list[Path]:
    """Return skill documents represented by an extra directory.

    Both forms are useful for callers embedding the agent:

    * ``extras/<skill-name>/SKILL.md`` (a directory of plug-ins), and
    * ``extras/<skill-name>`` where that directory itself contains
      ``SKILL.md`` (a single plug-in directory).

    A direct ``SKILL.md`` path is accepted as a convenience as well.  We only
    inspect one directory level; arbitrary recursive scanning could
    accidentally load documentation or vendored examples as system prompts.
    """

    directory = Path(directory).expanduser()
    if directory.is_file():
        if directory.name.upper() == "SKILL.MD":
            return [directory]
        raise ValueError(f"Skill path must be a directory or SKILL.md: {directory}")
    if not directory.exists():
        raise ValueError(f"Skill directory does not exist: {directory}")
    if not directory.is_dir():
        raise ValueError(f"Skill path is not a directory: {directory}")

    own = directory / "SKILL.md"
    if own.is_file():
        return [own]
    # A directory of children is sorted for reproducible duplicate detection
    # and catalog order.  Ignore ordinary files at the parent level.
    return sorted(
        (
            child / "SKILL.md"
            for child in directory.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        ),
        key=lambda p: p.as_posix().casefold(),
    )


class SkillCatalog:
    """Discover built-in and external prompt skills.

    ``extra_dirs`` can point to a directory containing ``<name>/SKILL.md``, a
    single skill directory, or the Markdown file itself.  Duplicate names are
    rejected, including a plug-in shadowing a built-in skill.  Callers can use
    ``include_builtin=False`` for an isolated plug-in catalog.
    """

    def __init__(
        self,
        extra_dirs: list[Path] | None = None,
        include_builtin: bool = True,
    ) -> None:
        self.extra_dirs = [Path(p) for p in (extra_dirs or [])]
        self.include_builtin = bool(include_builtin)
        self._skills: dict[str, Skill] = {}
        self._load()

    def _add(self, skill: Skill, path: Path) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Duplicate skill name: {skill.name} ({path})")
        self._skills[skill.name] = skill

    def _load(self) -> None:
        if self.include_builtin:
            builtin_dir = Path(__file__).resolve().parent / "skills"
            if builtin_dir.exists():
                for path in _skill_files(builtin_dir):
                    self._add(_parse_skill(path), path)
        for directory in self.extra_dirs:
            # Extra locations are optional configuration.  A first-run
            # workspace often has not created its plug-in directory yet; an
            # absent optional directory therefore contributes no skills.
            if not directory.expanduser().exists():
                continue
            for path in _skill_files(directory):
                self._add(_parse_skill(path), path)

    def list(self) -> list[dict[str, str]]:
        """Return stable, metadata-only summaries for UI/CLI discovery."""

        return [
            {"name": skill.name, "description": skill.description}
            for skill in sorted(self._skills.values(), key=lambda item: item.name)
        ]

    def get(self, name: str) -> Skill:
        """Return a skill by exact name, raising ``KeyError`` when unknown."""

        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def instructions(self, active: list[str]) -> str:
        """Combine instructions for active skills in caller-provided order."""

        chunks: list[str] = []
        seen: set[str] = set()
        for name in active:
            if name in seen:
                continue
            seen.add(name)
            skill = self.get(name)
            if not skill.instructions:
                continue
            chunks.append(f"## Skill: {skill.name}\n{skill.instructions}")
        return "\n\n".join(chunks)


__all__ = ["Skill", "SkillCatalog"]
