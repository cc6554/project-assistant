"""The optional job-radar workflow as ordinary kernel tools.

The module contains no web server and no second agent.  It is an adapter around
the copied, deterministic legacy tools and stores its artifacts below
``session.skill_state["job-radar"]``.  The general kernel remains unaware of JD,
profile, plan, repository, and progress fields.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import PendingApproval, ToolContext, ToolDefinition, ToolOutcome, ToolRegistry
from .integrations import github_token_context
from .legacy.domain.schemas import GitHubRepo, JDCard, LearningPlan, UserSkillProfile
from . import tools as common_tools


SKILL_NAME = "job-radar"
_STATE_KEY = SKILL_NAME
_MAX_INPUT_FILES = 10
_MAX_INPUT_BYTES = 16 * 1024 * 1024
_JD_SOURCE_KINDS = {
    "text",
    "screenshot",
    "screenshots",
    "document",
    "public_source",
    "conversation",
    "agent_argument",
}
_PROFILE_SOURCE_KINDS = {
    "resume",
    "self_report",
    "chat",
    "work_log",
    "obsidian",
    "local_path",
    "document",
    "conversation",
    "agent_argument",
}

# ``selected_project`` and ``progress`` remain the small, active-project view
# used by older callers.  ``project_history`` is the durable portfolio behind
# that view.  Keep the model-facing projection compact because progress
# evidence can contain long logs and a user may practice many repositories.
_PROJECT_HISTORY_CONTEXT_LIMIT = 50
_PROJECT_CONTEXT_TEXT_LIMIT = 240
_PROJECT_STATUS_VALUES = {"not_started", "in_progress", "completed"}


def search_github_projects(
    _unused_router: Any,
    queries: list[str],
    profile: UserSkillProfile | None = None,
    *,
    workspace: str | Path | None = None,
    **options: Any,
) -> list[GitHubRepo]:
    """Run the existing deterministic GitHub collector without a model.

    The import stays lazy so loading the optional skill does not require every
    provider SDK.  ``search_projects`` always passes ``None`` and
    ``llm_rerank=False``; the first parameter exists only for the legacy
    collector's call shape and is never used here.
    """

    from .legacy.tools.github_search import search_github_projects as collect

    options["llm_rerank"] = False
    scope = github_token_context(workspace) if workspace is not None else nullcontext()
    with scope:
        return collect(None, queries, profile, **options)


def _dump(value: Any) -> Any:
    """Turn a pydantic model or nested artifact into JSON-safe ordinary data."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _state(ctx: ToolContext) -> dict[str, Any]:
    """Get the isolated skill state, initializing only its own namespace."""

    state = ctx.session.skill_state.setdefault(_STATE_KEY, {})
    # Existing sessions may have been created before an artifact was added.
    # Initialize missing keys in-place while preserving unknown extension keys.
    defaults: dict[str, Any] = {
        "jd": None,
        "jd_source": None,
        "jd_confirmation": None,
        "profile": None,
        "profile_source": None,
        "clarification": {
            "status": "completed",
            "open_questions": [],
            "answered": [],
            "updated_at": None,
        },
        "plan": None,
        "plan_source": None,
        "repos": [],
        "selected_project": None,
        "progress": [],
        "project_history": [],
        "project_selection_revision": 0,
        "history": [],
        "jd_history": [],
        "profile_history": [],
        "plan_history": [],
        "repos_history": [],
        "progress_history": [],
    }
    for key, default in defaults.items():
        if key not in state:
            state[key] = deepcopy(default)
    return state


def ensure_state(session: Any) -> dict[str, Any]:
    """Initialize this skill's isolated namespace for a newly opened session."""

    return _state(ToolContext(workspace=Path("."), session=session, router=None))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event(state: dict[str, Any], kind: str, **details: Any) -> dict[str, Any]:
    event = {"type": kind, "at": _now(), **_dump(details)}
    state.setdefault("history", []).append(event)
    return event


def _project_key(project: Any) -> str | None:
    """Return the stable identity used to join project state across refreshes.

    GitHub's ``full_name`` is preferred.  The URL and id fallbacks keep the
    state useful for old fixtures or future repository sources that do not
    provide the GitHub field.
    """

    if isinstance(project, dict):
        for field in ("full_name", "html_url", "id", "name"):
            value = project.get(field)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None
    if isinstance(project, str) and project.strip():
        return project.strip()
    if project is not None and not isinstance(project, (list, tuple, set)):
        value = str(project).strip()
        return value or None
    return None


def _project_metadata(project: Any, key: str | None = None) -> dict[str, Any] | None:
    """Make a JSON-safe repository snapshot for a history record."""

    if isinstance(project, dict):
        data = _dump(project)
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    resolved = key or _project_key(data)
    if resolved and not _project_key(data):
        data["full_name"] = resolved
    return data if data else ({"full_name": resolved} if resolved else None)


def _normalise_progress(progress: Any) -> list[dict[str, Any]]:
    """Copy progress entries and hide unapproved completion claims.

    Older sessions could contain a ``completed`` status written before the
    approval boundary was introduced.  The evidence remains intact, while the
    effective status is deliberately treated as in-progress until a real
    approval marks ``user_confirmed`` true.
    """

    if not isinstance(progress, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in progress:
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        status = str(item.get("status") or "").strip().casefold()
        if status in {"done", "complete", "completed", "finished"} and item.get("user_confirmed") is not True:
            # Keep the model's original claim available for audit/debugging
            # without exposing it as an approved completion.
            item.setdefault("reported_status", item.get("status"))
            item["status"] = "in_progress"
        result.append(item)
    return result


def _progress_status(progress: Any) -> str:
    """Derive status from evidence, with completion gated by approval."""

    entries = _normalise_progress(progress)
    if any(
        str(entry.get("status") or "").strip().casefold() in {"done", "complete", "completed", "finished"}
        and entry.get("user_confirmed") is True
        for entry in entries
    ):
        return "completed"
    return "in_progress" if entries else "not_started"


def _progress_merge(target: list[dict[str, Any]], values: Any) -> None:
    """Append unseen progress records while retaining their original order."""

    for item in _normalise_progress(values):
        if item not in target:
            target.append(item)


def _history_record(raw: Any) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    """Decode one current or legacy project-history shape."""

    if not isinstance(raw, dict):
        return None, None, [], {}
    if "project" in raw:
        project = raw.get("project")
        progress = raw.get("progress", [])
        key = raw.get("project_key") or _project_key(project)
    else:
        # A few early experiments stored repository fields at the record root.
        project = {
            name: value
            for name, value in raw.items()
            if name not in {"progress", "status", "created_at", "updated_at", "project_key", "completed_at"}
        }
        progress = raw.get("progress", [])
        key = raw.get("project_key") or _project_key(project)
    key = str(key).strip() if key is not None and str(key).strip() else None
    metadata = _project_metadata(project, key)
    meta = {
        name: deepcopy(raw.get(name))
        for name in ("created_at", "updated_at", "completed_at")
        if raw.get(name) is not None
    }
    # Preserve a legacy explicit status only as metadata.  Effective status is
    # always recomputed from approved progress below.
    if raw.get("status") in _PROJECT_STATUS_VALUES:
        meta["legacy_status"] = raw.get("status")
    return key, metadata, _normalise_progress(progress), meta


def _history_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a normalized project history view without mutating ``state``.

    This is the lazy migration path: simply opening an old session can display
    history, while the canonical list is written only when a project workflow
    mutation actually uses it.
    """

    raw_history = state.get("project_history")
    if isinstance(raw_history, dict):
        raw_items: list[Any] = []
        for key, value in raw_history.items():
            if isinstance(value, dict):
                item = deepcopy(value)
                item.setdefault("project_key", key)
                raw_items.append(item)
    elif isinstance(raw_history, list):
        raw_items = list(raw_history)
    else:
        raw_items = []

    # Repository snapshots from prior searches let migration retain useful
    # metadata even though progress_history only carried ``project`` names.
    metadata_by_key: dict[str, dict[str, Any]] = {}

    def remember_metadata(project: Any) -> None:
        key = _project_key(project)
        metadata = _project_metadata(project, key)
        if key and metadata:
            previous = metadata_by_key.get(key, {})
            merged = deepcopy(previous)
            merged.update(deepcopy(metadata))
            metadata_by_key[key] = merged

    for repo in state.get("repos", []) if isinstance(state.get("repos"), list) else []:
        remember_metadata(repo)
    for batch in state.get("repos_history", []) if isinstance(state.get("repos_history"), list) else []:
        if not isinstance(batch, dict):
            continue
        for repo in batch.get("repos", []) if isinstance(batch.get("repos"), list) else []:
            remember_metadata(repo)
    for raw in raw_items:
        key, project, _, _ = _history_record(raw)
        if key and project:
            remember_metadata(project)

    records: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}

    def ensure_record(
        project: Any,
        *,
        progress: Any = None,
        at: Any = None,
        force_progress: bool = False,
    ) -> dict[str, Any] | None:
        key = _project_key(project)
        if not key:
            return None
        metadata = _project_metadata(project, key) or {"full_name": key}
        enriched = deepcopy(metadata_by_key.get(key, {}))
        enriched.update(metadata)
        record = by_key.get(key)
        # Read-only lazy migration must be stable.  Unknown legacy timestamps
        # remain empty and are populated by the next real mutation.
        timestamp = str(at).strip() if at is not None and str(at).strip() else ""
        if record is None:
            record = {
                "project_key": key,
                "project": enriched,
                "progress": [],
                "status": "not_started",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            by_key[key] = record
            records.append(record)
        else:
            current_project = record.get("project") if isinstance(record.get("project"), dict) else {}
            merged_project = deepcopy(current_project)
            merged_project.update(enriched)
            record["project"] = merged_project
        if progress is not None:
            if force_progress:
                record["progress"] = _normalise_progress(progress)
            else:
                _progress_merge(record.setdefault("progress", []), progress)
            if timestamp:
                record["updated_at"] = timestamp
        return record

    for raw in raw_items:
        key, project, progress, metadata = _history_record(raw)
        if not key:
            continue
        record = ensure_record(project or metadata_by_key.get(key) or {"full_name": key}, at=metadata.get("created_at"))
        if record is None:
            continue
        _progress_merge(record.setdefault("progress", []), progress)
        for name in ("created_at", "updated_at", "completed_at"):
            if metadata.get(name) is not None:
                record[name] = metadata[name]

    # ``progress_history`` is the old cumulative append-only source.  Group it
    # by project before the active-project snapshot is applied.
    legacy_progress = state.get("progress_history")
    if isinstance(legacy_progress, list):
        for entry in legacy_progress:
            if not isinstance(entry, dict):
                continue
            project = entry.get("project")
            key = _project_key(project)
            if not key:
                continue
            project_snapshot = project if isinstance(project, dict) else metadata_by_key.get(key) or {"full_name": key}
            ensure_record(project_snapshot, progress=[entry], at=entry.get("at"))

    # Selection events are enough to recover a project that was selected but
    # has no progress yet.
    events = state.get("history")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict) or event.get("type") != "project_selected":
                continue
            key = event.get("project_key") or event.get("full_name")
            if key:
                ensure_record(metadata_by_key.get(str(key)) or {"full_name": str(key)}, at=event.get("at"))

    selected = state.get("selected_project")
    if selected:
        # Merge the compatibility active list with the append-only legacy
        # history.  This keeps earlier evidence when an old session had already
        # reset its active list during a project switch.
        ensure_record(selected, progress=state.get("progress", []), at=None)

    for record in records:
        progress = _normalise_progress(record.get("progress", []))
        record["progress"] = progress
        record["status"] = _progress_status(progress)
        if progress:
            timestamps = [str(item.get("at")) for item in progress if item.get("at")]
            if timestamps:
                record["updated_at"] = timestamps[-1]
            approved = [
                str(item.get("at"))
                for item in progress
                if item.get("user_confirmed") is True
                and str(item.get("status") or "").strip().casefold() in {"done", "complete", "completed", "finished"}
                and item.get("at")
            ]
            if approved:
                record["completed_at"] = approved[-1]
        record.pop("legacy_status", None)
    return records


def _ensure_project_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize the lazy history migration before a workflow mutation."""

    records = _history_records(state)
    state["project_history"] = records
    try:
        state["project_selection_revision"] = max(0, int(state.get("project_selection_revision", 0)))
    except (TypeError, ValueError):
        state["project_selection_revision"] = 0
    return records


def _sync_active_project(state: dict[str, Any], records: list[dict[str, Any]] | None = None) -> None:
    """Persist the compatibility active view into its history record."""

    selected = state.get("selected_project")
    key = _project_key(selected)
    if not key:
        return
    if records is None:
        records = _ensure_project_history(state)
    record = next((item for item in records if item.get("project_key") == key), None)
    if record is None:
        record = {
            "project_key": key,
            "project": _project_metadata(selected, key) or {"full_name": key},
            "progress": [],
            "status": "not_started",
            "created_at": _now(),
            "updated_at": _now(),
        }
        records.append(record)
    else:
        project = record.get("project") if isinstance(record.get("project"), dict) else {}
        project.update(_project_metadata(selected, key) or {})
        record["project"] = project
    # Progress is append-only evidence.  Merge the active compatibility view
    # into the record so a legacy session whose old active list was reset does
    # not erase earlier entries recovered from ``progress_history``.
    merged_progress = _normalise_progress(record.get("progress", []))
    _progress_merge(merged_progress, state.get("progress", []))
    record["progress"] = merged_progress
    record["status"] = _progress_status(record["progress"])
    now = _now()
    if not record.get("created_at"):
        record["created_at"] = now
    record["updated_at"] = now
    approved = [
        str(entry.get("at"))
        for entry in record["progress"]
        if entry.get("user_confirmed") is True
        and str(entry.get("status") or "").strip().casefold() in {"done", "complete", "completed", "finished"}
        and entry.get("at")
    ]
    if approved:
        record["completed_at"] = approved[-1]
    state["project_history"] = records


def _archive_active_project(state: dict[str, Any]) -> None:
    """Save active project evidence before clearing an invalidated view."""

    records = _ensure_project_history(state)
    _sync_active_project(state, records)


def _bump_project_selection_revision(state: dict[str, Any]) -> int:
    current = _safe_selection_revision(state)
    current = max(0, current) + 1
    state["project_selection_revision"] = current
    return current


def _safe_selection_revision(state: dict[str, Any]) -> int:
    try:
        return max(0, int(state.get("project_selection_revision", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _invalidate_downstream(state: dict[str, Any], reason: str) -> None:
    """Invalidate every artifact derived from JD/profile changes."""

    had_active = bool(state.get("selected_project"))
    _archive_active_project(state)
    state["plan"] = None
    state["plan_source"] = None
    state["repos"] = []
    state["selected_project"] = None
    state["progress"] = []
    if had_active:
        _bump_project_selection_revision(state)
    state["invalidated"] = {"reason": reason, "at": _now()}


def _result(message: str, **data: Any) -> ToolOutcome:
    payload = _dump(data)
    # JSON content gives CLI callers a useful stable representation while the
    # structured data remains available to engine/UI callers.
    return ToolOutcome(
        content=json.dumps({"message": message, **payload}, ensure_ascii=False, default=str),
        data=payload,
    )


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return current artifacts without replaying the unbounded history."""

    project_history = _history_records(state)
    return {
        "jd": deepcopy(state.get("jd")),
        "jd_source": deepcopy(state.get("jd_source")),
        "jd_confirmation": deepcopy(state.get("jd_confirmation")),
        "profile": deepcopy(state.get("profile")),
        "profile_source": deepcopy(state.get("profile_source")),
        "clarification": deepcopy(state.get("clarification")),
        "plan": deepcopy(state.get("plan")),
        "plan_source": deepcopy(state.get("plan_source")),
        "repos": deepcopy(state.get("repos", [])),
        "selected_project": deepcopy(state.get("selected_project")),
        "progress": _normalise_progress(state.get("progress", [])),
        "project_history": deepcopy(project_history),
        "project_selection_revision": _safe_selection_revision(state),
        "history_count": len(state.get("history", [])),
        "latest_event": deepcopy((state.get("history") or [])[-1]) if state.get("history") else None,
    }


def workflow_state(session: Any) -> dict[str, Any]:
    """Return the public job-radar state from a session without model calls."""

    state = session.skill_state.get(_STATE_KEY, {}) if session is not None else {}
    public = _public_state(state if isinstance(state, dict) else {})
    clarification = public.get("clarification")
    if not isinstance(clarification, dict):
        profile = public.get("profile") or {}
        questions = [
            str(item).strip()
            for item in profile.get("open_questions", [])
            if str(item).strip()
        ] if isinstance(profile, dict) else []
        public["clarification"] = {
            "status": "active" if questions else "completed",
            "open_questions": questions,
            "answered": [],
            "updated_at": None,
        }
    return public


def model_context(session: Any) -> dict[str, Any]:
    """Return the bounded current artifacts that the product model may see.

    The engine injects this snapshot into the product system message before
    every model request.  Keep the shape deliberately separate from
    :func:`workflow_state`: sidebar consumers may need event metadata, while a
    model only needs the current JD/profile/clarification/plan/project/progress
    artifacts.  Project history is reduced to bounded status/count summaries;
    the unbounded workflow event log and provider credentials never cross this
    context bridge.
    """

    public = workflow_state(session)
    history = public.get("project_history", [])
    compact_history: list[dict[str, Any]] = []
    if isinstance(history, list):
        for item in history[-_PROJECT_HISTORY_CONTEXT_LIMIT:]:
            if not isinstance(item, dict):
                continue
            project = item.get("project") if isinstance(item.get("project"), dict) else {}
            compact_project = {
                key: deepcopy(project[key])
                for key in ("full_name", "html_url", "language", "stargazers_count")
                if project.get(key) is not None
            }
            progress = item.get("progress") if isinstance(item.get("progress"), list) else []
            latest = progress[-1] if progress and isinstance(progress[-1], dict) else None
            compact: dict[str, Any] = {
                "project_key": item.get("project_key") or _project_key(project),
                "project": compact_project,
                "status": item.get("status") or _progress_status(progress),
                "progress_count": len(progress),
            }
            if latest:
                compact["latest_progress"] = {
                    "status": latest.get("status"),
                    "note": str(latest.get("note") or "")[:_PROJECT_CONTEXT_TEXT_LIMIT],
                }
            compact_history.append(compact)
    return {
        "jd": deepcopy(public.get("jd")),
        "profile": deepcopy(public.get("profile")),
        "clarification": deepcopy(public.get("clarification")),
        "plan": deepcopy(public.get("plan")),
        "project": {
            "selected": deepcopy(public.get("selected_project")),
            "candidates": deepcopy(public.get("repos", [])),
        },
        "progress": deepcopy(public.get("progress", [])),
        "project_history": compact_history,
    }


def copy_profile_state(source_session: Any, target_session: Any) -> None:
    """Copy only the current profile context into a fresh, otherwise empty session.

    A copied session must not inherit the source job, plan, candidates, progress,
    conversation, or approval state.  The caller is responsible for creating the
    target through ``AgentEngine.new_session`` before invoking this helper.
    """

    source_state = source_session.skill_state.get(_STATE_KEY, {}) if source_session is not None else {}
    if not isinstance(source_state, dict):
        source_state = {}
    target_state = target_session.skill_state.setdefault(_STATE_KEY, {})
    target_state["profile"] = deepcopy(source_state.get("profile"))
    target_state["profile_source"] = deepcopy(source_state.get("profile_source"))
    clarification = source_state.get("clarification")
    if isinstance(clarification, dict):
        target_state["clarification"] = deepcopy(clarification)
    else:
        target_state["clarification"] = {
            "status": "completed" if not (source_state.get("profile") or {}).get("open_questions") else "active",
            "open_questions": deepcopy((source_state.get("profile") or {}).get("open_questions", [])),
            "answered": [],
            "updated_at": _now(),
        }
    # Keep the new context auditable without carrying the source conversation or
    # historical job artifacts into the new session.
    target_state["profile_history"] = []
    target_state["history"] = []
    target_state["jd"] = None
    target_state["jd_source"] = None
    target_state["jd_confirmation"] = None
    target_state["plan"] = None
    target_state["plan_source"] = None
    target_state["repos"] = []
    target_state["selected_project"] = None
    target_state["progress"] = []
    target_state["project_history"] = []
    target_state["project_selection_revision"] = 0
    for key in ("jd_history", "plan_history", "repos_history", "progress_history"):
        target_state[key] = []


def _clarification_update(
    previous: Any,
    old_profile: Any,
    new_profile: dict[str, Any],
) -> dict[str, Any]:
    """Persist explicit interview status and mark questions removed as answered."""

    previous_data = previous if isinstance(previous, dict) else {}
    old_data = old_profile if isinstance(old_profile, dict) else {}
    old_questions = [str(item).strip() for item in old_data.get("open_questions", []) if str(item).strip()]
    new_questions = [str(item).strip() for item in new_profile.get("open_questions", []) if str(item).strip()]
    answered: list[str] = []
    for item in previous_data.get("answered", []) if isinstance(previous_data.get("answered"), list) else []:
        clean = str(item).strip()
        if clean and clean not in answered:
            answered.append(clean)
    for item in old_questions:
        if item not in new_questions and item not in answered:
            answered.append(item)
    return {
        "status": "active" if new_questions else "completed",
        "open_questions": new_questions,
        "answered": answered,
        "updated_at": _now(),
    }


def _require(value: Any, message: str) -> Any:
    if value is None or value == "" or value == [] or value == {}:
        raise ValueError(message)
    return value


def _first(args: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    return default


def _model(model_type: Any, value: Any) -> Any:
    if isinstance(value, model_type):
        return value
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(value)
    return model_type.parse_obj(value)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sensitive_path(path: Path) -> bool:
    """Reject obvious credential/configuration files before model upload."""

    sensitive_names = {
        ".env",
        ".env.local",
        ".env.production",
        "credentials",
        "credentials.json",
        "secret",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
        "providers.yaml",
        "providers.yml",
    }
    name = path.name.casefold()
    if name in sensitive_names:
        return True
    if path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}:
        return True
    return any(token in name for token in ("password", "passwd", "token", "api_key", "apikey"))


def _resolve_path(ctx: ToolContext, raw: str | Path) -> Path:
    """Resolve an input through the general path guard before reading it.

    ``tools.resolve_path`` belongs to the general kernel.  Keeping the call
    through the module (rather than copying its policy here) means workspace
    boundaries and sensitive-file rules remain one shared contract.
    """

    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raise ValueError("路径必须是非空字符串")
    # The explicit keyword is part of the kernel tools contract.  Do not
    # open/read the path before this call has approved it.
    resolved = common_tools.resolve_path(ctx, raw, must_exist=True)
    path = Path(resolved).resolve(strict=False)
    if _is_sensitive_path(path):
        raise PermissionError("敏感路径不能上传给模型")
    if not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    return path


def _validate_input_files(paths: list[Path], kind: str) -> None:
    if len(paths) > _MAX_INPUT_FILES:
        raise ValueError(f"一次最多处理 {_MAX_INPUT_FILES} 个{kind}")
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError(f"无法读取{kind}大小：{path.name}") from exc
        if size > _MAX_INPUT_BYTES:
            raise ValueError(f"{kind}过大（上限 {_MAX_INPUT_BYTES // (1024 * 1024)} MiB）：{path.name}")


def _paths(args: dict[str, Any], *names: str) -> list[Any]:
    value = _first(args, *names, default=[])
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [value]
    if not isinstance(value, Iterable):
        raise ValueError("路径列表格式无效")
    return list(value)


def _source_evidence(
    ctx: ToolContext,
    arguments: dict[str, Any],
    *,
    artifact: str,
    allowed_kinds: set[str],
) -> dict[str, Any]:
    """Validate provenance supplied alongside an agent-produced artifact.

    The active agent is responsible for reading screenshots and documents with
    the general read tools before submitting a structured artifact.  This
    helper records the claimed provenance and applies the shared workspace
    path guard when a caller includes source paths; it never opens a path and
    never turns an agent assertion into user confirmation.
    """

    raw = _first(arguments, "source", "provenance", "source_evidence", default=None)
    if raw is None:
        source: dict[str, Any] = {"kind": "agent_argument", "evidence": []}
    elif isinstance(raw, str):
        source = {"kind": raw.strip(), "evidence": []}
    elif isinstance(raw, dict):
        source = dict(raw)
    else:
        raise ValueError(f"{artifact} source 必须是字符串或对象")

    kind = str(source.get("kind") or source.get("type") or "").strip().casefold()
    if kind not in allowed_kinds:
        allowed = ", ".join(sorted(allowed_kinds - {"agent_argument"}))
        raise ValueError(f"{artifact} source.kind 无效；可选值：{allowed}")
    source["kind"] = kind

    evidence = source.get("evidence", source.get("references", []))
    if evidence is None:
        evidence = []
    if isinstance(evidence, str):
        evidence = [evidence] if evidence.strip() else []
    elif isinstance(evidence, (tuple, list)):
        evidence = [str(item) for item in evidence if str(item).strip()]
    else:
        raise ValueError(f"{artifact} source.evidence 必须是字符串或字符串列表")
    if source["kind"] != "agent_argument" and not evidence:
        raise ValueError(f"{artifact} 必须提供 source.evidence，说明工件来自用户材料或通用读取工具")
    source["evidence"] = evidence

    _validate_source_paths(ctx, source, artifact)
    source["user_confirmed"] = False
    source.pop("confirmed", None)
    return source


def _validate_source_paths(ctx: ToolContext, source: dict[str, Any], artifact: str) -> dict[str, Any]:
    """Validate, but never read, provenance paths from a source object."""

    raw_paths = source.get("paths", source.get("source_paths", source.get("path")))
    if raw_paths is None:
        return source
    paths = _paths({"paths": raw_paths}, "paths")
    resolved = [_resolve_path(ctx, item) for item in paths]
    _validate_input_files(resolved, f"{artifact}来源文件")
    source["paths"] = [str(path) for path in resolved]
    source.pop("source_paths", None)
    source.pop("path", None)
    return source


def _artifact(arguments: dict[str, Any], model_type: Any, *names: str, label: str) -> Any:
    """Read one structured artifact argument and validate it with its schema."""

    value = _first(arguments, *names, default=None)
    if value is None:
        raise ValueError(f"请在 {label} 中提交结构化 {label} 工件")
    if not isinstance(value, (dict, model_type)):
        raise ValueError(f"{label} 必须是对象，不能传文本让工具自行解析")
    try:
        return _model(model_type, value)
    except Exception as exc:
        raise ValueError(f"{label} 工件不符合既定 schema：{exc}") from exc


def _validate_profile_evidence(profile: UserSkillProfile) -> None:
    """Reject skills that have no source/evidence to support an assertion."""

    for skill in profile.skills:
        if not skill.name.strip():
            raise ValueError("个人档案中的技能名称不能为空")
        if not skill.sources:
            raise ValueError(f"技能 {skill.name} 必须带 sources 来源")
        if not skill.evidence.strip():
            raise ValueError(f"技能 {skill.name} 必须带 evidence 事实证据")


def job_radar_status(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    state = _state(ctx)
    return _result("job-radar 状态", state=_public_state(state))


def set_jd(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Validate and persist a JDCard submitted by the active agent.

    Parsing belongs to the active agent.  In particular, this function does
    not read screenshots, fetch a URL, or make a second model request.
    Screenshots and documents must first be supplied to the
    generic read path and represented by the submitted source evidence.
    """

    state = _state(ctx)
    card_model = _artifact(arguments, JDCard, "jd", "jd_card", "artifact", label="JD")
    source_meta = _source_evidence(
        ctx,
        arguments,
        artifact="JD",
        allowed_kinds=_JD_SOURCE_KINDS,
    )
    source_url = _first(arguments, "source_url", "url", default=source_meta.get("url"))
    if source_meta["kind"] == "public_source" and source_url is None:
        raise ValueError("public_source 必须保留 source_url")
    if source_url is not None:
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError("source_url 必须是非空字符串")
        from .legacy.tools.job_sources import platform_for

        platform = platform_for(source_url)
        if platform is None:
            raise ValueError("source_url 必须是支持招聘平台的 HTTPS 来源链接")
        source_meta.update({"url": source_url, "platform": platform})
        if source_meta["kind"] != "public_source":
            raise ValueError("提供 source_url 时 source.kind 必须是 public_source")

    card = _dump(card_model)
    state["jd"] = card
    state["jd_source"] = source_meta
    # A new or edited JD is a new user-review candidate.  Confirmation is a
    # separate UI-owned state and can never be carried in by an Agent tool call.
    state["jd_confirmation"] = None
    _invalidate_downstream(state, "JD 已更新")
    state.setdefault("jd_history", []).append(
        {"at": _now(), "jd": deepcopy(card), "source": deepcopy(source_meta)}
    )
    _event(state, "jd_updated", source=source_meta)
    return _result("JD 已保存；计划、项目和进度已失效", jd=card, state=_public_state(state))


def confirm_jd(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Record an explicit user confirmation for the current JD.

    This helper is intentionally not registered as an Agent tool.  The Web
    service invokes it only for the authenticated UI action, after the session
    lock has been acquired.  A model-provided ``confirmed`` argument therefore
    cannot satisfy this boundary.
    """

    state = _state(ctx)
    if not state.get("jd"):
        raise ValueError("请先保存 JD，再确认岗位信息")
    value = _first(arguments, "confirmed", default=True)
    if not isinstance(value, bool):
        raise ValueError("confirmed 必须是布尔值")
    if not value:
        state["jd_confirmation"] = None
        _event(state, "jd_unconfirmed", actor="user")
        return _result("JD 已取消确认", state=_public_state(state))
    confirmation = {
        "confirmed": True,
        "actor": "user",
        "at": _now(),
        # SessionStore increments this revision when the Web mutation commits.
        "revision": int(getattr(ctx.session, "revision", 0)) + 1,
    }
    state["jd_confirmation"] = confirmation
    _event(state, "jd_confirmed", actor="user")
    return _result("JD 已由用户确认", confirmation=confirmation, state=_public_state(state))


def set_profile(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Validate and persist a complete UserSkillProfile artifact.

    The profile is assembled by the active agent from conversation and the
    generic ``read_file``/``read_document`` tools.  This tool only validates
    the schema, checks optional provenance paths, and stores the result.
    """

    state = _state(ctx)
    profile_model = _artifact(
        arguments,
        UserSkillProfile,
        "profile",
        "user_skill_profile",
        "artifact",
        label="个人技能档案",
    )
    _validate_profile_evidence(profile_model)
    source_meta = _source_evidence(
        ctx,
        arguments,
        artifact="个人技能档案",
        allowed_kinds=_PROFILE_SOURCE_KINDS,
    )
    profile = profile_model
    profile_data = _dump(profile)
    old_profile = deepcopy(state.get("profile"))
    clarification = _clarification_update(
        state.get("clarification"),
        old_profile,
        profile_data,
    )
    state["profile"] = profile_data
    state["profile_source"] = source_meta
    state["clarification"] = clarification
    _invalidate_downstream(state, "个人技能档案已更新")
    history_item = {
        "at": _now(),
        "source": deepcopy(source_meta),
        "profile": deepcopy(profile_data),
        "clarification": deepcopy(clarification),
    }
    state.setdefault("profile_history", []).append(history_item)
    _event(
        state,
        "profile_updated",
        source=source_meta,
        open_questions=profile_data.get("open_questions", []),
        clarification_status=clarification["status"],
    )
    return _result(
        "个人档案已保存；计划、项目和进度已失效",
        profile=profile_data,
        clarification=clarification,
        state=_public_state(state),
    )


def build_plan(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Validate and persist a LearningPlan artifact from the active agent."""

    state = _state(ctx)
    if not state.get("jd"):
        raise ValueError("请先使用 set_jd 提交并确认 JD 工件")
    if not state.get("profile"):
        raise ValueError("请先使用 set_profile 提交个人技能档案工件")
    learning_plan = _artifact(
        arguments,
        LearningPlan,
        "plan",
        "learning_plan",
        "artifact",
        label="学习计划",
    )
    queries = [q.strip() for q in learning_plan.github_search_queries if q.strip()]
    if not queries:
        raise ValueError("学习计划必须提供至少一个 github_search_queries")
    learning_plan.github_search_queries = list(dict.fromkeys(queries))
    plan_data = _dump(learning_plan)
    plan_source = _source_evidence(
        ctx,
        arguments,
        artifact="学习计划",
        allowed_kinds={"agent_argument", "conversation"},
    )
    state["plan"] = plan_data
    state["plan_source"] = plan_source
    # A newly generated plan is a new basis for project search.  Keep every
    # practiced project's evidence in the portfolio before clearing the active
    # compatibility view.
    had_active = bool(state.get("selected_project"))
    _archive_active_project(state)
    state["repos"] = []
    state["selected_project"] = None
    state["progress"] = []
    if had_active:
        _bump_project_selection_revision(state)
    state.setdefault("plan_history", []).append(
        {"at": _now(), "plan": deepcopy(plan_data), "source": deepcopy(plan_source)}
    )
    _event(state, "plan_created", source=plan_source)
    return _result("学习计划已保存", plan=plan_data, state=_public_state(state))


def search_projects(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    state = _state(ctx)
    if not state.get("jd"):
        raise ValueError("请先使用 set_jd 提交并确认 JD 工件")
    if not state.get("profile"):
        raise ValueError("请先使用 set_profile 提交个人技能档案工件")
    if not state.get("plan"):
        raise ValueError("请先使用 build_plan 提交学习计划工件")

    learning_plan = _model(LearningPlan, state["plan"])
    profile = _model(UserSkillProfile, state["profile"])
    queries = _first(arguments, "queries", "search_queries", default=None)
    if queries is None:
        queries = list(learning_plan.github_search_queries)
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list) or not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("检索词必须是非空字符串列表")
    if not queries:
        raise ValueError("学习计划没有 GitHub 检索词，请先调整计划")

    options = {
        "min_stars": _first(arguments, "min_stars", default=50),
        "recency_days": _first(arguments, "recency_days", default=365),
        "top_n": _first(arguments, "top_n", default=10),
        "preferences": _first(arguments, "preferences", default=None),
    }
    # Repository ranking must remain deterministic.  The active agent can
    # inspect these candidates and decide what to select; this tool never
    # invokes a stage-specific model.  Rejecting an explicit true value makes
    # accidental reintroduction of the old nested reranker visible.
    if "llm_rerank" in arguments and arguments["llm_rerank"] not in (False, 0, "false", "False"):
        raise ValueError("search_projects 不支持 llm_rerank；候选排序由确定性指标完成")
    for name, lower, upper in (("min_stars", 0, 10_000_000), ("recency_days", 1, 3650), ("top_n", 1, 50)):
        value = options[name]
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f"{name} 必须是 {lower} 到 {upper} 之间的整数")
    if options["preferences"] is not None and not isinstance(options["preferences"], dict):
        raise ValueError("preferences 必须是对象")
    repos = search_github_projects(
        None,
        queries,
        profile,
        llm_rerank=False,
        workspace=ctx.workspace,
        **options,
    )
    repo_data = _dump([_model(GitHubRepo, repo) for repo in (repos or [])])
    # Candidate refreshes are safe while a project is being practiced.  Make
    # the active snapshot durable only after validation/search succeeds, then
    # replace only the candidate list.
    _archive_active_project(state)
    state["repos"] = repo_data
    state.setdefault("repos_history", []).append({"at": _now(), "queries": list(queries), "repos": deepcopy(repo_data)})
    _event(state, "repos_searched", count=len(repo_data), queries=list(queries))
    return _result("项目候选已更新", repos=repo_data, state=_public_state(state))


def select_project(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    state = _state(ctx)
    records = _ensure_project_history(state)
    repos = state.get("repos") or []
    if not repos and not records:
        raise ValueError("请先使用 search_projects 获取项目候选")
    requested_name = _first(arguments, "full_name", "name", default=None)
    requested_url = _first(arguments, "html_url", "url", default=None)
    requested_index = _first(arguments, "index", default=None)
    selected: dict[str, Any] | None = None
    if requested_index is not None:
        try:
            index = int(requested_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("项目 index 必须是整数") from exc
        # Ranks exposed to users are intentionally one-based; accepting zero
        # as well would make model-generated calls ambiguous.
        if 1 <= index <= len(repos):
            selected = repos[index - 1]
        else:
            raise ValueError("项目 index 必须是从 1 开始的候选排名")
    else:
        for repo in repos:
            if requested_name and repo.get("full_name") == requested_name:
                selected = repo
                break
            if requested_url and repo.get("html_url") == requested_url:
                selected = repo
                break
        # A previously practiced project remains selectable even when a later
        # candidate refresh no longer returns it.  This is the revisit path for
        # the cumulative portfolio and still requires an exact known identity.
        if selected is None:
            for record in records:
                project = record.get("project") if isinstance(record.get("project"), dict) else {}
                if requested_name and project.get("full_name") == requested_name:
                    selected = project
                    break
                if requested_url and project.get("html_url") == requested_url:
                    selected = project
                    break
    if selected is None:
        raise ValueError("请选择 search_projects 返回的候选项目或历史练习项目")
    previous = state.get("selected_project") or {}
    previous_key = _project_key(previous)
    selected_key = _project_key(selected)
    if previous_key != selected_key:
        # Before changing the active view, copy its latest compatibility
        # progress into the durable record.  The selected project's prior
        # progress is then restored instead of being silently discarded.
        _sync_active_project(state, records)
        _bump_project_selection_revision(state)
        record = next((item for item in records if item.get("project_key") == selected_key), None)
        state["progress"] = deepcopy(record.get("progress", [])) if record else []
    else:
        # A legacy snapshot may still point at the active project while its
        # compatibility list contains only the latest segment.  Reconcile it
        # with the migrated record so reselecting the same project also
        # restores all append-only evidence.
        record = next((item for item in records if item.get("project_key") == selected_key), None)
        if record is not None:
            restored_progress = _normalise_progress(record.get("progress", []))
            _progress_merge(restored_progress, state.get("progress", []))
            state["progress"] = restored_progress
    state["selected_project"] = deepcopy(selected)
    _sync_active_project(state, records)
    _event(
        state,
        "project_selected",
        full_name=selected.get("full_name"),
        project_key=selected_key,
        selection_revision=state.get("project_selection_revision", 0),
    )
    return _result("项目已选择", project=deepcopy(selected), state=_public_state(state))


def _explicit_confirmation(arguments: dict[str, Any]) -> bool:
    value = _first(arguments, "user_confirmed", "confirmed", "confirm_completion", default=False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "y", "确认", "已确认"}
    return False


def _explicit_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "y", "1", "完成", "已完成"}
    return False


def _progress_entry(
    state: dict[str, Any],
    *,
    note: Any,
    evidence: Any,
    status: str,
    user_confirmed: bool,
    approval_id: str | None = None,
) -> dict[str, Any]:
    selected = state.get("selected_project") or {}
    entry = {
        "at": _now(),
        "project": selected.get("full_name") or _project_key(selected),
        "note": str(note or ""),
        "evidence": _dump(evidence),
        "status": status,
        "user_confirmed": user_confirmed,
    }
    if approval_id:
        entry["approval_id"] = approval_id
    state.setdefault("progress", []).append(entry)
    state.setdefault("progress_history", []).append(deepcopy(entry))
    _sync_active_project(state)
    _event(
        state,
        "progress_recorded",
        project_key=_project_key(selected),
        status=status,
        completed=status == "completed",
    )
    return entry


def record_progress(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    state = _state(ctx)
    selected = state.get("selected_project")
    if not selected:
        raise ValueError("请先使用 select_project 选择一个项目")
    _ensure_project_history(state)
    note = _first(arguments, "note", "text", "summary", "progress", default="")
    evidence = _first(arguments, "evidence", "proof", default=None)
    status = str(_first(arguments, "status", default="in_progress")).strip().casefold()
    completed = _explicit_flag(_first(arguments, "completed", "done", default=False)) or status in {
        "done",
        "completed",
        "complete",
        "finished",
    }
    if not _has_text(note) and evidence in (None, "", [], {}):
        raise ValueError("请提供本次进度说明或 evidence")
    if completed:
        if evidence in (None, "", [], {}):
            raise ValueError("验收完成必须提供明确 evidence")
        # A model argument named ``user_confirmed`` is only an assertion and
        # is deliberately ignored as authorization.  The kernel's approval
        # decision is the user confirmation boundary.  Store the proposed
        # entry in ``prepared`` and wait for ToolDefinition.execute.
        status = "completed"
        prepared = {
            "kind": "job-radar-progress-completion",
            "project": deepcopy(selected),
            "project_key": _project_key(selected),
            "selection_revision": int(state.get("project_selection_revision", 0) or 0),
            "note": str(note or ""),
            "evidence": _dump(evidence),
        }
        return PendingApproval(
            tool_name="record_progress",
            arguments=dict(arguments),
            summary=f"确认完成项目 {selected.get('full_name', '未命名项目')} 的进度验收",
            preview=json.dumps(
                {
                    "action": "record completed progress",
                    "project": selected.get("full_name"),
                    "note": str(note or ""),
                    "evidence": _dump(evidence),
                },
                ensure_ascii=False,
                default=str,
            ),
            prepared=prepared,
        )
    else:
        status = status or "in_progress"
    entry = _progress_entry(
        state,
        note=note,
        evidence=evidence,
        status=status,
        user_confirmed=False,
    )
    return _result("进度已记录", progress=deepcopy(entry), state=_public_state(state))


def _execute_record_progress(pending: PendingApproval, ctx: ToolContext) -> ToolOutcome:
    """Commit a completion only after the kernel accepted its approval."""

    state = _state(ctx)
    prepared = pending.prepared or {}
    if prepared.get("kind") != "job-radar-progress-completion":
        raise ValueError("无效的 job-radar 进度审批数据")
    selected = state.get("selected_project") or {}
    project = prepared.get("project") or {}
    if _project_key(selected) != prepared.get("project_key") or _project_key(selected) != _project_key(project):
        raise ValueError("待审批项目已变化，拒绝写入旧的完成记录")
    expected_revision = prepared.get("selection_revision")
    try:
        current_revision = int(state.get("project_selection_revision", 0) or 0)
        expected_revision_value = int(expected_revision)
    except (TypeError, ValueError):
        raise ValueError("待审批项目缺少有效的选择版本，拒绝写入旧的完成记录") from None
    if expected_revision_value != current_revision:
        raise ValueError("待审批项目已切换，拒绝写入旧的完成记录")
    evidence = prepared.get("evidence")
    if evidence in (None, "", [], {}):
        raise ValueError("验收完成必须保留明确 evidence")
    existing = next(
        (
            item
            for item in state.get("progress", [])
            if isinstance(item, dict) and item.get("approval_id") == pending.id
        ),
        None,
    )
    if existing is not None:
        return _result("经用户审批，完成进度已记录", progress=deepcopy(existing), state=_public_state(state))
    entry = _progress_entry(
        state,
        note=prepared.get("note", ""),
        evidence=evidence,
        status="completed",
        user_confirmed=True,
        approval_id=pending.id,
    )
    return _result("经用户审批，完成进度已记录", progress=deepcopy(entry), state=_public_state(state))


# These Python aliases keep imports in older integrations readable while the
# registered tool protocol intentionally exposes only the structured names
# ``set_profile`` and ``build_plan``.
update_profile = set_profile
plan = build_plan


def _expand_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local Pydantic ``$ref`` entries for a self-contained tool spec."""

    definitions = schema.get("$defs", {})

    def expand(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in stack:
                    raise ValueError(f"循环 schema 引用：{name}")
                return expand(deepcopy(definitions[name]), (*stack, name))
            return {key: expand(item, stack) for key, item in value.items() if key != "$defs"}
        if isinstance(value, list):
            return [expand(item, stack) for item in value]
        return value

    return expand(schema)


def _model_schema(model_type: Any) -> dict[str, Any]:
    return _expand_schema_refs(model_type.model_json_schema())


def _source_schema(allowed_kinds: set[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": sorted(allowed_kinds)},
            "evidence": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                ]
            },
            "paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "path": {"type": "string", "minLength": 1},
            "source_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "url": {"type": "string", "minLength": 1},
        },
        "required": ["kind", "evidence"],
        "additionalProperties": False,
    }


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None):
    from .legacy.core.schema import ToolSpec

    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": True,
        },
    )


def register_job_radar_tools(registry: ToolRegistry) -> ToolRegistry:
    """Register all job-radar tools behind the optional skill gate."""

    definitions = [
        ToolDefinition(
            spec=_spec("job_radar_status", "查看岗位雷达当前工件、前置条件和历史。", {}),
            handler=job_radar_status,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "set_jd",
                "保存主体 Agent 已从用户材料整理出的结构化 JDCard；工具只校验、记录来源并使下游工件失效。",
                {
                    "jd": _model_schema(JDCard),
                    "jd_card": _model_schema(JDCard),
                    "source": _source_schema(_JD_SOURCE_KINDS),
                    "provenance": _source_schema(_JD_SOURCE_KINDS),
                    "source_evidence": _source_schema(_JD_SOURCE_KINDS),
                    "source_url": {"type": "string", "minLength": 1},
                    "url": {"type": "string", "minLength": 1},
                },
                required=["jd"],
            ),
            handler=set_jd,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "set_profile",
                "保存主体 Agent 根据用户经历和通用读取工具结果整理出的完整 UserSkillProfile。",
                {
                    "profile": _model_schema(UserSkillProfile),
                    "user_skill_profile": _model_schema(UserSkillProfile),
                    "source": _source_schema(_PROFILE_SOURCE_KINDS),
                    "provenance": _source_schema(_PROFILE_SOURCE_KINDS),
                    "source_evidence": _source_schema(_PROFILE_SOURCE_KINDS),
                },
                required=["profile"],
            ),
            handler=set_profile,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "build_plan",
                "保存主体 Agent 根据 JD 与个人技能档案整理出的结构化 LearningPlan；工具只校验并重置下游候选与进度。",
                {
                    "plan": _model_schema(LearningPlan),
                    "learning_plan": _model_schema(LearningPlan),
                    "source": _source_schema({"agent_argument", "conversation"}),
                    "provenance": _source_schema({"agent_argument", "conversation"}),
                    "source_evidence": _source_schema({"agent_argument", "conversation"}),
                },
                required=["plan"],
            ),
            handler=build_plan,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "search_projects",
                "按学习计划检索公开 GitHub 项目候选并保存结果。",
                {
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "top_n": {"type": "integer", "minimum": 1},
                    "min_stars": {"type": "integer", "minimum": 0},
                    "recency_days": {"type": "integer", "minimum": 1},
                    "preferences": {"type": "object"},
                },
            ),
            handler=search_projects,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "select_project",
                "从已保存的候选中选择实际要练习的项目。",
                {
                    "full_name": {"type": "string"},
                    "html_url": {"type": "string"},
                    "index": {"type": "integer", "minimum": 1},
                },
            ),
            handler=select_project,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "record_progress",
                "记录用户在选定项目上的进度；验收完成必须有 evidence 和用户明确确认。",
                {
                    "note": {"type": "string"},
                    "text": {"type": "string"},
                    "evidence": {},
                    "status": {"type": "string"},
                    "completed": {"type": "boolean"},
                    "user_confirmed": {"type": "boolean"},
                },
            ),
            handler=record_progress,
            execute=_execute_record_progress,
            skill=SKILL_NAME,
        ),
    ]
    for definition in definitions:
        registry.register(definition)
    return registry


__all__ = [
    "SKILL_NAME",
    "job_radar_status",
    "workflow_state",
    "model_context",
    "copy_profile_state",
    "ensure_state",
    "set_jd",
    "confirm_jd",
    "set_profile",
    "build_plan",
    "update_profile",
    "plan",
    "search_projects",
    "select_project",
    "record_progress",
    "register_job_radar_tools",
]
