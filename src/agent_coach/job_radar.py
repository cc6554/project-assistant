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
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit, urlunsplit

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
_PROJECT_SOURCE_KINDS = {
    "user_url",
    "url",
    "upload",
    "local_import",
    "local_path",
    "local",
    "imported_material",
    "conversation",
    "paper_implementation",
    "github",
}
_PAPER_QUERY_TERMS = {
    "paper",
    "implementation",
    "reproduction",
    "reproduce",
    "arxiv",
    "conference",
}

# ``selected_project`` and ``progress`` remain the small, active-project view
# used by older callers.  ``project_history`` is the durable portfolio behind
# that view.  Keep the model-facing projection compact because progress
# evidence can contain long logs and a user may practice many repositories.
_PROJECT_HISTORY_CONTEXT_LIMIT = 50
_PROJECT_CONTEXT_TEXT_LIMIT = 240
_PROJECT_STATUS_VALUES = {"not_started", "in_progress", "completed"}
_PROJECT_FILTER_MAX_TERMS = 50
_PROJECT_FILTER_TERM_MAX_BYTES = 256
_PROJECT_FILTER_OTHER_MAX_BYTES = 4096
_LOCAL_SOURCE_MAX_ENTRIES = 512


def _default_project_filter() -> dict[str, Any]:
    """Return a fresh default so sessions never share mutable filter state."""

    return {
        "min_stars": 50,
        "include": [],
        "exclude": [],
        "other": "",
        "paper_projects": True,
        "rerank": True,
    }


def _normalise_filter_terms(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"project_filter.{field} 必须是字符串列表")
    if len(value) > _PROJECT_FILTER_MAX_TERMS:
        raise ValueError(f"project_filter.{field} 最多允许 {_PROJECT_FILTER_MAX_TERMS} 项")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"project_filter.{field} 必须只包含字符串")
        term = item.strip()
        if not term:
            continue
        if len(term.encode("utf-8")) > _PROJECT_FILTER_TERM_MAX_BYTES:
            raise ValueError(f"project_filter.{field} 单项过长")
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(term)
    return result


def normalize_project_filter(value: Any) -> dict[str, Any]:
    """Validate and normalize the durable project search preferences.

    The returned shape is intentionally stable across UI, tool, and model
    context boundaries.  ``None``/missing values use product defaults so an
    older session can be upgraded without a model call.
    """

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("project_filter 必须是对象")
    allowed = {"min_stars", "include", "exclude", "other", "paper_projects", "rerank"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"project_filter 包含未知字段：{', '.join(str(item) for item in unknown)}")

    min_stars = value.get("min_stars", 50)
    if min_stars in (None, ""):
        min_stars = 50
    if isinstance(min_stars, bool) or not isinstance(min_stars, int) or not 0 <= min_stars <= 10_000_000:
        raise ValueError("project_filter.min_stars 必须是 0 到 10000000 之间的整数")

    other = value.get("other", "")
    if other is None:
        other = ""
    if not isinstance(other, str):
        raise ValueError("project_filter.other 必须是字符串")
    other = other.strip()
    if len(other.encode("utf-8")) > _PROJECT_FILTER_OTHER_MAX_BYTES:
        raise ValueError("project_filter.other 过长")

    paper_projects = value.get("paper_projects", True)
    rerank = value.get("rerank", True)
    if paper_projects is None:
        paper_projects = True
    if rerank is None:
        rerank = True
    if not isinstance(paper_projects, bool) or not isinstance(rerank, bool):
        raise ValueError("project_filter.paper_projects 和 rerank 必须是布尔值")

    return {
        "min_stars": min_stars,
        "include": _normalise_filter_terms(value.get("include"), "include"),
        "exclude": _normalise_filter_terms(value.get("exclude"), "exclude"),
        "other": other,
        "paper_projects": paper_projects,
        "rerank": rerank,
    }


def _paper_queries(queries: Iterable[str]) -> list[str]:
    """Build bounded paper/reproduction discovery queries.

    These queries are only a recall aid for GitHub Search.  The resulting
    ``paper_candidate`` flag is deliberately unverified; the main agent must
    inspect the README and cited paper source before presenting venue or
    citation information.
    """

    result: list[str] = []
    seen: set[str] = set()
    for raw in queries:
        query = str(raw).strip()
        if not query:
            continue
        words = set(query.casefold().replace("_", " ").replace("-", " ").split())
        if words & _PAPER_QUERY_TERMS:
            variant = query
        else:
            variant = f"{query} paper implementation"
        # GitHub's default repository search emphasizes names, descriptions,
        # and topics.  Paper/code associations are commonly documented in a
        # README, so generated paper queries must opt into README search unless
        # the caller already supplied an explicit ``in:`` scope.
        if not any(token.casefold().startswith("in:") for token in variant.split()):
            variant = f"{variant} in:readme"
        if variant.casefold() not in seen:
            result.append(variant)
            seen.add(variant.casefold())
    return result


def _safe_project_url(value: Any) -> str:
    """Validate and canonicalize a user-supplied public project URL."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("项目 URL 必须是非空字符串")
    raw = value.strip()
    if any(ord(char) < 0x20 for char in raw):
        raise ValueError("项目 URL 包含非法控制字符")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("项目 URL 格式无效") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ValueError("项目 URL 必须是无凭据的 HTTPS 公共链接")
    query = parse_qs(parsed.query, keep_blank_values=True)
    credential_keys = {"token", "access_token", "api_key", "apikey", "key", "auth", "authorization"}
    normalized_query_keys = {
        str(key).strip().casefold().replace("-", "_")
        for key in query
    }
    if normalized_query_keys.intersection(credential_keys):
        raise ValueError("项目 URL 查询参数不能携带凭据")
    hostname = parsed.hostname.casefold()
    # ``urlunsplit`` cannot safely reconstruct an IPv6 literal from the
    # hostname alone (the required brackets would be lost).  The current
    # project provenance contract does not need literal IPv6 URLs, so reject
    # them explicitly instead of persisting a malformed canonical URL.
    if ":" in hostname:
        raise ValueError("项目 URL 暂不支持 IPv6 字面量")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    # Keep ordinary non-credential query parameters because they may identify
    # a public revision, while normalizing host and trailing slash.
    return urlunsplit(("https", hostname, path, parsed.query, ""))


def _candidate_key_material(project: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Return identity fields for a user project, excluding display labels."""

    url = project.get("html_url") or project.get("url") or source.get("url")
    paths = source.get("paths", []) if isinstance(source.get("paths"), list) else []
    identity: dict[str, Any] = {
        # A canonical public URL is the identity even when the user later
        # reclassifies the same URL as a paper implementation.  Local and
        # conversation-only records retain their source kind as part of the
        # fallback identity.
        "kind": None
        if url
        else str(source.get("kind") or project.get("source_kind") or "user_url"),
        "url": url,
        "paths": sorted(str(item) for item in paths if str(item).strip()),
        # A canonical URL is the complete identity.  Keep repository/title
        # fallbacks only for source records without a URL, so a later rename
        # of the same URL does not create a duplicate candidate.
        "repository": (project.get("repository") or project.get("full_name")) if not url and not paths else None,
    }
    # Conversation-only projects still need a reproducible identity.  Include
    # evidence/description only when no URL or path exists, so a display title
    # by itself can never collide with another local project.
    if not identity["url"] and not identity["paths"]:
        identity["evidence"] = source.get("evidence", [])
        identity["description"] = project.get("description", "")
    return identity


def _stable_user_project_key(project: dict[str, Any], source: dict[str, Any]) -> str:
    material = _candidate_key_material(project, source)
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "user:" + hashlib.sha256(encoded).hexdigest()[:32]


def _merge_candidate(target: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    key = _project_key(candidate)
    if not key:
        return
    for index, current in enumerate(target):
        if _project_key(current) == key:
            merged = deepcopy(current)
            merged.update(deepcopy(candidate))
            target[index] = merged
            return
    target.append(deepcopy(candidate))


def search_github_projects(
    _unused_router: Any,
    queries: list[str],
    profile: UserSkillProfile | None = None,
    *,
    workspace: str | Path | None = None,
    **options: Any,
) -> list[GitHubRepo]:
    """Run the deterministic GitHub collector without a model.

    The import stays lazy so loading the optional skill does not require every
    provider SDK.  ``search_projects`` always passes ``None`` and
    ``llm_rerank=False``; optional ``paper_queries`` are a separate discovery
    channel whose matches remain explicitly unverified until the main agent
    reads their source material.
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
        "project_filter": _default_project_filter(),
        "repos": [],
        # User-supplied projects are durable candidates.  ``repos`` remains
        # the compatibility view consumed by existing UI callers, while this
        # separate list prevents a search refresh from erasing a local/URL
        # reproduction project.
        "user_projects": [],
        "user_projects_history": [],
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
    try:
        state["project_filter"] = normalize_project_filter(state.get("project_filter"))
    except ValueError:
        # A malformed legacy extension must not prevent the session from
        # opening.  Replace it with the documented defaults and let the user
        # explicitly save a corrected filter through ``set_project_filter``.
        state["project_filter"] = _default_project_filter()
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
        for field in ("project_key", "identity_key", "full_name", "html_url", "id", "name"):
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
    for project in state.get("user_projects", []) if isinstance(state.get("user_projects"), list) else []:
        remember_metadata(project)
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
        "project_filter": deepcopy(state.get("project_filter", _default_project_filter())),
        "repos": deepcopy(state.get("repos", [])),
        "user_projects": deepcopy(state.get("user_projects", [])),
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
                for key in (
                    "project_key",
                    "full_name",
                    "name",
                    "html_url",
                    "language",
                    "stargazers_count",
                    "source_kind",
                    "source_verified",
                    "source_paths",
                    "source_evidence",
                    "code_verified",
                    "code_paths",
                    "code_evidence",
                    "paper_candidate",
                    "paper_verified",
                    "paper_title",
                    "paper_url",
                    "venue",
                    "citation_count",
                    "paper_evidence",
                )
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
        "project_filter": deepcopy(public.get("project_filter", _default_project_filter())),
        "project": {
            "selected": deepcopy(public.get("selected_project")),
            "candidates": deepcopy(public.get("repos", [])),
            "user_projects": deepcopy(public.get("user_projects", [])),
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
    target_state["project_filter"] = _default_project_filter()
    target_state["repos"] = []
    target_state["user_projects"] = []
    target_state["user_projects_history"] = []
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

    evidence = source.get(
        "evidence",
        source.get("references", source.get("source_evidence", source.get("code_evidence", []))),
    )
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


def set_project_filter(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Persist user-confirmed project search constraints for this session.

    The active agent may use this tool after discussing refinements with the
    user.  Search calls inherit the saved constraints, so a later model call
    cannot silently relax the user's minimum stars or exclusions.
    """

    if not isinstance(arguments, dict):
        raise ValueError("project_filter 参数必须是对象")
    raw = _first(arguments, "project_filter", "filter", default=None)
    if raw is None and not ("project_filter" in arguments or "filter" in arguments):
        raw = arguments
    elif raw is None:
        raw = {}
    normalized = normalize_project_filter(raw)
    state = _state(ctx)
    state["project_filter"] = normalized
    _event(state, "project_filter_updated", project_filter=normalized)
    return _result(
        "项目筛选偏好已保存",
        project_filter=deepcopy(normalized),
        state=_public_state(state),
    )


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

    project_filter = normalize_project_filter(state.get("project_filter"))
    state["project_filter"] = project_filter
    requested_include_paper = _first(arguments, "include_paper_implementations", default=None)
    if requested_include_paper is not None and not isinstance(requested_include_paper, bool):
        raise ValueError("include_paper_implementations 必须是布尔值")
    # A model may make a narrower request, but it cannot relax a saved user
    # preference.  In particular, paper discovery remains disabled when the
    # user turned it off in the filter.
    include_paper = project_filter["paper_projects"] and (
        requested_include_paper if requested_include_paper is not None else True
    )
    paper_queries = _paper_queries(queries) if include_paper else []

    requested_min_stars = _first(arguments, "min_stars", default=None)
    if requested_min_stars is None:
        min_stars = project_filter["min_stars"]
    else:
        if (
            isinstance(requested_min_stars, bool)
            or not isinstance(requested_min_stars, int)
            or not 0 <= requested_min_stars <= 10_000_000
        ):
            raise ValueError("min_stars 必须是 0 到 10000000 之间的整数")
        min_stars = max(project_filter["min_stars"], requested_min_stars)

    raw_preferences = _first(arguments, "preferences", default=None)
    if raw_preferences is not None and not isinstance(raw_preferences, dict):
        raise ValueError("preferences 必须是对象")
    explicit_include = _normalise_filter_terms(
        raw_preferences.get("include") if isinstance(raw_preferences, dict) else None,
        "include",
    )
    explicit_exclude = _normalise_filter_terms(
        raw_preferences.get("exclude") if isinstance(raw_preferences, dict) else None,
        "exclude",
    )

    def _merge_terms(saved: list[str], explicit: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for term in [*saved, *explicit]:
            folded = term.casefold()
            if folded not in seen:
                seen.add(folded)
                result.append(term)
        return result

    preferences = {
        "include": _merge_terms(project_filter["include"], explicit_include),
        "exclude": _merge_terms(project_filter["exclude"], explicit_exclude),
    }
    options = {
        "min_stars": min_stars,
        "recency_days": _first(arguments, "recency_days", default=365),
        "top_n": _first(arguments, "top_n", default=10),
        "preferences": preferences,
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
    repos = search_github_projects(
        None,
        queries,
        profile,
        llm_rerank=False,
        workspace=ctx.workspace,
        paper_queries=paper_queries,
        paper_recency_days=max(options["recency_days"], 3650),
        **options,
    )
    normalized_repos = [_model(GitHubRepo, repo) for repo in (repos or [])]

    def _verified_code(repo: GitHubRepo) -> bool:
        # Both names are accepted at the boundary for compatibility, but an
        # explicit false on either field is never overridden by the other.
        if repo.code_verified is False or repo.source_verified is False:
            return False
        if repo.code_verified is not True and repo.source_verified is not True:
            return False
        paths = repo.code_paths or repo.source_paths
        evidence = repo.code_evidence or repo.source_evidence
        return bool(paths) and bool(evidence)

    unverified_count = sum(1 for repo in normalized_repos if not _verified_code(repo))
    # Keep the recommendation boundary strict even when a caller replaces the
    # network collector with a fixture or when a stale session contains a
    # candidate written before source provenance existed.
    normalized_repos = [repo for repo in normalized_repos if _verified_code(repo)]
    repo_data = _dump(normalized_repos)
    # Keep user-provided projects in the compatibility candidate projection so
    # existing UI consumers can select them by index, while ``user_projects``
    # remains the durable source of truth across refreshes.
    combined: list[dict[str, Any]] = list(repo_data)
    rejected_user_count = 0
    for candidate in state.get("user_projects", []) if isinstance(state.get("user_projects"), list) else []:
        if not _project_has_verified_source(candidate):
            rejected_user_count += 1
            continue
        if not any(_project_key(item) == _project_key(candidate) for item in combined):
            combined.append(deepcopy(candidate))
    # Candidate refreshes are safe while a project is being practiced.  Make
    # the active snapshot durable only after validation/search succeeds, then
    # replace only the candidate list.
    _archive_active_project(state)
    state["repos"] = combined
    state.setdefault("repos_history", []).append(
        {
            "at": _now(),
            "queries": list(queries),
            "paper_queries": list(paper_queries),
            "project_filter": deepcopy(project_filter),
            "repos": deepcopy(repo_data),
            "user_projects": deepcopy(state.get("user_projects", [])),
        }
    )
    _event(
        state,
        "repos_searched",
        count=len(repo_data),
        queries=list(queries),
        paper_queries=list(paper_queries),
        project_filter=deepcopy(project_filter),
        user_project_count=len(state.get("user_projects", [])),
    )
    verification = {
        "verified_count": len(repo_data),
        "rejected_unverified_count": unverified_count,
        "rejected_user_project_count": rejected_user_count,
        "message": (
            "没有通过源码核验的项目；README、论文、数据或无法读取源码的仓库已排除，请提供可读取的 GitHub 仓库或本地实现源码"
            if not repo_data
            else "候选仅保留已核验源码的仓库；README、论文、数据或无法读取源码的仓库已排除"
            if unverified_count or rejected_user_count
            else "候选均已提供源码路径和公开核验依据"
        ),
    }
    return _result(
        "项目候选已更新",
        repos=combined,
        paper_queries=paper_queries,
        source_verification=verification,
        state=_public_state(state),
    )


def _project_title(payload: dict[str, Any]) -> str:
    value = payload.get("name") or payload.get("title") or payload.get("full_name")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("项目必须提供 name 或 title")
    title = value.strip()
    if len(title) > 512:
        raise ValueError("项目名称过长")
    return title


def _reject_local_symlink_paths(ctx: ToolContext, paths: Iterable[Any]) -> None:
    """Reject lexical symlinks before the shared resolver canonicalizes them."""

    root = Path(ctx.workspace).expanduser().resolve(strict=False)
    for raw in paths:
        if not isinstance(raw, (str, Path)) or not str(raw).strip():
            continue
        lexical = Path(raw)
        if not lexical.is_absolute():
            lexical = root / lexical
        try:
            lexical.relative_to(root)
        except ValueError:
            continue
        if lexical.is_symlink():
            raise ValueError("自带项目 source.paths 不能经过符号链接")
        ancestor = lexical.parent
        while ancestor != root and root in ancestor.parents:
            if ancestor.is_symlink():
                raise ValueError("自带项目 source.paths 不能经过符号链接目录")
            ancestor = ancestor.parent


def _project_source(arguments: dict[str, Any], payload: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    raw = payload.get("source", _first(arguments, "source", "provenance", default=None))
    if raw is None:
        raise ValueError("自带项目必须提供 source，说明 URL、上传文件或本地导入材料来源")
    raw_kind = str(raw.get("kind") or "").strip().casefold() if isinstance(raw, dict) else str(raw).strip().casefold()
    local_kinds = {"upload", "local_import", "local_path", "local", "imported_material"}
    if raw_kind in local_kinds and isinstance(raw, dict):
        for field in ("paths", "path", "source_paths", "code_paths", "source_code_paths"):
            declared = raw.get(field)
            if declared is None:
                continue
            _reject_local_symlink_paths(ctx, _paths({"paths": declared}, "paths"))
    source_for_validation = deepcopy(raw) if isinstance(raw, dict) else raw
    if isinstance(source_for_validation, dict):
        if raw_kind not in local_kinds:
            # Repository-relative paths supplied alongside a remote URL are
            # claims that the GitHub verifier must replace with tree evidence;
            # they are not workspace paths for the generic provenance guard.
            for key in ("paths", "path", "source_paths"):
                source_for_validation.pop(key, None)
        for key in (
            "code_verified",
            "code_evidence",
            "source_verified",
            "source_evidence",
        ):
            if key not in source_for_validation and key in payload:
                source_for_validation[key] = deepcopy(payload[key])
    source_arguments = {"source": source_for_validation}
    source = _source_evidence(
        ctx,
        source_arguments,
        artifact="自带项目",
        allowed_kinds=_PROJECT_SOURCE_KINDS,
    )
    if source["kind"] == "agent_argument":
        raise ValueError("自带项目 source.kind 不能是 agent_argument")
    # Accept canonical code provenance at the project level as well as under
    # source; the normalized candidate always stores it under source.
    for key in (
        "code_verified",
        "code_paths",
        "code_evidence",
        "source_verified",
        "source_paths",
        "source_evidence",
    ):
        if key not in source and key in payload:
            source[key] = deepcopy(payload[key])
    # ``code_paths`` is an explicit, optional refinement for callers that
    # import a directory and want to distinguish implementation files from
    # README/data attachments.  Resolve it through the same workspace guard as
    # ordinary source paths before inspecting file names.
    if source["kind"] in local_kinds:
        _reject_local_symlink_paths(ctx, _paths({"paths": source.get("paths")}, "paths"))
        declared_code_paths = source.get("code_paths", source.get("source_code_paths"))
        if declared_code_paths is not None:
            code_paths = _paths({"paths": declared_code_paths}, "paths")
            _reject_local_symlink_paths(ctx, code_paths)
            source["paths"] = [*(source.get("paths") or []), *code_paths]
            _validate_source_paths(ctx, source, "自带项目")
        # A caller may provide the canonical ``source_paths`` field at the
        # project level.  Treat it as the local material list before applying
        # the guarded path normalization, just like ``code_paths`` above.
        if not source.get("paths") and source.get("source_paths") is not None:
            source["paths"] = source.get("source_paths")
            _reject_local_symlink_paths(ctx, _paths({"paths": source["paths"]}, "paths"))
            _validate_source_paths(ctx, source, "自带项目")
    raw_url = payload.get("url") or payload.get("html_url") or source.get("url")
    if raw_url is not None:
        safe_url = _safe_project_url(raw_url)
        source["url"] = safe_url
    elif source["kind"] == "github":
        # The compact project form may identify a private repository by
        # ``full_name``/``repository`` without duplicating its public URL.
        # Normalize the identity before turning it into the fixed GitHub URL;
        # the verifier below still performs the authenticated tree read.
        from .legacy.tools.github_search import _repository_identity

        repository = _repository_identity(
            payload.get("full_name") or payload.get("repository") or source.get("repository")
        )
        if not repository:
            raise ValueError("github 项目必须提供有效的 full_name/repository 或 source.url")
        source["url"] = f"https://github.com/{repository}"
    elif source["kind"] in {"user_url", "url"}:
        raise ValueError("URL 项目必须提供 source.url 或项目 url")
    if not source.get("paths"):
        material_paths = payload.get("paths") or payload.get("materials") or payload.get("material_paths")
        if material_paths is not None:
            source["paths"] = material_paths
            _validate_source_paths(ctx, source, "自带项目")
    if source["kind"] in {"upload", "local_import", "local_path", "local", "imported_material"}:
        if not source.get("paths"):
            raise ValueError(f"{source['kind']} 项目必须提供已导入 workspace 的 source.paths")
    # Validation above deliberately uses the shared absolute-path guard.  Do
    # not persist those host-specific absolute paths in a project candidate:
    # workspace-relative paths remain readable by the same guarded tools and
    # keep source-derived project identities portable across workspace copies.
    if source.get("paths"):
        try:
            root = Path(ctx.workspace).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ValueError("项目来源 workspace 无法解析") from exc
        relative_paths: list[str] = []
        for raw_path in source["paths"]:
            try:
                path = Path(raw_path).resolve(strict=False)
                relative = path.relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError) as exc:
                raise ValueError("项目 source.paths 必须位于当前 workspace 内") from exc
            if not relative or relative == ".":
                raise ValueError("项目 source.paths 必须指向 workspace 内的材料")
            relative_paths.append(relative)
        source["paths"] = relative_paths
    _verify_registered_project_source(source, ctx)
    if source["kind"] in local_kinds and source.get("source_paths"):
        try:
            root = Path(ctx.workspace).expanduser().resolve(strict=False)
            for field in ("source_paths", "code_paths"):
                if source.get(field):
                    source[field] = [
                        Path(raw_path).resolve(strict=False).relative_to(root).as_posix()
                        for raw_path in source[field]
                    ]
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("项目 source.source_paths 必须位于当前 workspace 内") from exc
    return source


def _verify_registered_project_source(source: dict[str, Any], ctx: ToolContext) -> None:
    """Require usable implementation-source provenance for user projects.

    Local imports are checked from the bounded, already-guarded workspace
    paths.  GitHub URLs are checked through the same API client used by search,
    which allows a workspace-scoped token to prove access to a private owner
    repository.  Other URLs and conversation-only descriptions cannot prove
    source availability by themselves and fail with an actionable message.
    """

    from .legacy.tools.github_search import is_source_code_path

    kind = str(source.get("kind") or "").casefold()
    local_kinds = {"upload", "local_import", "local_path", "local", "imported_material"}
    if kind in local_kinds:
        raw_paths = source.get("paths") or []
        code_paths: list[str] = []
        root = Path(ctx.workspace).expanduser().resolve(strict=False)
        for raw in raw_paths:
            try:
                candidate_path = Path(raw)
                if not candidate_path.is_absolute():
                    candidate_path = root / candidate_path
                # Inspect the lexical path before resolving it.  Resolving
                # first would erase a symlink marker and could make a link to
                # an otherwise in-workspace target look like an ordinary
                # imported source path.
                if candidate_path.is_symlink():
                    continue
                ancestor = candidate_path.parent
                unsafe_ancestor = False
                while ancestor != root and root in ancestor.parents:
                    if ancestor.is_symlink():
                        unsafe_ancestor = True
                        break
                    ancestor = ancestor.parent
                if unsafe_ancestor:
                    continue
                path = candidate_path.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_symlink():
                continue
            if path.is_file():
                try:
                    non_empty = path.stat().st_size > 0
                except OSError:
                    non_empty = False
                if non_empty and is_source_code_path(path.name):
                    code_paths.append(path.as_posix())
                continue
            if not path.is_dir():
                continue
            # Imported manifests contain files rather than directories in the
            # normal UI flow, but accepting a bounded directory keeps direct
            # local-path callers useful without walking unbounded workspaces.
            try:
                children = path.rglob("*")
                visited = 0
                for child in children:
                    visited += 1
                    if visited > _LOCAL_SOURCE_MAX_ENTRIES:
                        break
                    if len(code_paths) >= 50:
                        break
                    if child.is_symlink() or not child.is_file():
                        continue
                    try:
                        resolved_child = child.resolve(strict=False)
                        resolved_child.relative_to(root)
                        # Do not accept files reached through a reparse/symlink
                        # ancestor even when the leaf itself is a regular file.
                        ancestor = child.parent
                        unsafe_ancestor = False
                        while ancestor != root and root in ancestor.parents:
                            if ancestor.is_symlink():
                                unsafe_ancestor = True
                                break
                            ancestor = ancestor.parent
                        if unsafe_ancestor:
                            continue
                        non_empty = resolved_child.stat().st_size > 0
                    except (OSError, ValueError):
                        continue
                    if non_empty and is_source_code_path(resolved_child.name):
                        code_paths.append(resolved_child.as_posix())
            except OSError:
                continue
        if not code_paths:
            raise ValueError(
                "自带项目无法核验源码：source.paths 中至少要有一个非空实现源码文件；"
                "README、论文、数据或说明文件不能作为项目实现"
            )
        source["source_verified"] = True
        source["source_paths"] = code_paths[:50]
        source["source_evidence"] = [f"workspace:{Path(path).relative_to(root).as_posix()}" for path in code_paths[:50]]
        source["code_verified"] = True
        source["code_paths"] = list(source["source_paths"])
        source["code_evidence"] = list(source["source_evidence"])
        return

    raw_url = source.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        parsed = urlsplit(raw_url)
        host = (parsed.hostname or "").casefold()
        parts = [part for part in parsed.path.split("/") if part]
        if host in {"github.com", "www.github.com"} and len(parts) >= 2:
            repository = f"{parts[0]}/{parts[1]}".removesuffix(".git")
            from .legacy.tools.github_search import verify_github_repository_source

            with github_token_context(ctx.workspace):
                verified = verify_github_repository_source(repository)
            if verified.get("verified"):
                source["source_verified"] = True
                source["source_paths"] = list(verified.get("paths") or [])
                source["source_evidence"] = list(verified.get("evidence") or [])
                source["code_verified"] = True
                source["code_paths"] = list(source["source_paths"])
                source["code_evidence"] = list(source["source_evidence"])
                source["source_branch"] = verified.get("branch")
                source["private"] = bool(verified.get("private"))
                return
            reason = str(verified.get("reason") or "GitHub 源码目录未返回可验证文件")
            raise ValueError(f"无法核验项目源码：{reason}")

    raise ValueError(
        "无法核验项目源码：请提供可读取的 GitHub 仓库或已导入 workspace 的代码文件；"
        "模型声明、普通 URL、论文、数据集和 README 不能单独证明实现"
    )


def _paper_metadata(payload: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("paper") or payload.get("paper_metadata")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("paper 必须是对象")
    paper: dict[str, Any] = {}
    for key in ("title", "url", "venue"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"paper.{key} 必须是非空字符串")
        paper[key] = value.strip()[:1024]
    if paper.get("url"):
        paper["url"] = _safe_project_url(paper["url"])
    count = raw.get("citation_count")
    if count is not None:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("paper.citation_count 必须是非负整数")
        paper["citation_count"] = count
    evidence = raw.get("evidence", raw.get("evidence_urls", []))
    if isinstance(evidence, str):
        evidence = [evidence] if evidence.strip() else []
    if not isinstance(evidence, list) or not all(isinstance(item, str) and item.strip() for item in evidence):
        raise ValueError("paper.evidence 必须是非空字符串列表")
    paper["evidence"] = [item.strip()[:2048] for item in evidence]
    # Venue and citation claims are preserved only when the claimed paper has
    # an identifiable title and canonical public URL plus explicit evidence.
    # A repository star count, search query, or arbitrary one-line assertion is
    # never accepted as evidence for a paper identity or its impact.
    identified = bool(paper.get("title") and paper.get("url"))
    if any(key in paper for key in ("venue", "citation_count")) and (
        not identified or not paper["evidence"]
    ):
        raise ValueError("paper 的 venue/citation_count 必须附带 paper.title、paper.url 和 paper.evidence")
    paper["verified"] = bool(identified and paper["evidence"])
    return paper


def register_project(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    """Register a user-supplied reproduction project as a durable candidate.

    The active agent may inspect user materials first, but registration also
    performs a bounded source-availability check: local/imported paths must
    contain a non-empty implementation file, and GitHub URLs must expose a
    readable repository tree (including through the workspace token for a
    private owner repository).  It never executes project code or selects the
    project implicitly.
    """

    state = _state(ctx)
    raw = _first(arguments, "project", "candidate", "project_candidate", default=None)
    if raw is None:
        raw = {key: value for key, value in arguments.items() if key not in {"source", "provenance"}}
    if not isinstance(raw, dict):
        raise ValueError("project 必须是对象")
    payload = dict(raw)
    title = _project_title(payload)
    # Validate declarative paper metadata and the source-kind contract before
    # any source inspection/network work so malformed calls fail for the
    # caller's actual schema mistake.
    paper = _paper_metadata(payload, {})
    declared_source_kind = payload.get("source_kind")
    raw_source = payload.get("source", _first(arguments, "source", "provenance", default=None))
    raw_source_kind = raw_source.get("kind") if isinstance(raw_source, dict) else raw_source
    if declared_source_kind is not None and str(declared_source_kind).strip().casefold() != str(raw_source_kind or "").strip().casefold():
        raise ValueError("project.source_kind 必须与 source.kind 一致")
    source = _project_source(arguments, payload, ctx)

    source_kind = str(declared_source_kind or source.get("kind") or "user_url").strip().casefold()
    if source_kind not in _PROJECT_SOURCE_KINDS:
        raise ValueError("project.source_kind 无效")
    if declared_source_kind is not None and source_kind != str(source.get("kind") or "").strip().casefold():
        raise ValueError("project.source_kind 必须与 source.kind 一致")
    url = source.get("url")
    full_name = payload.get("full_name")
    repository = payload.get("repository")
    if full_name is not None and (not isinstance(full_name, str) or not full_name.strip()):
        raise ValueError("project.full_name 必须是非空字符串")
    if repository is not None and (not isinstance(repository, str) or not repository.strip()):
        raise ValueError("project.repository 必须是非空字符串")
    description = payload.get("description", "")
    if not isinstance(description, str):
        raise ValueError("project.description 必须是字符串")
    description = description.strip()[:4096]
    reason = payload.get("reason", payload.get("assessment", ""))
    if isinstance(reason, dict):
        reason = reason.get("summary", reason.get("reason", ""))
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        raise ValueError("project.reason/assessment 必须是字符串或对象")
    reason = reason.strip()[:2048]

    candidate: dict[str, Any] = {
        "project_key": "",
        "name": title,
        "full_name": str(full_name).strip() if full_name else title,
        "html_url": url or "",
        "description": description,
        "language": payload.get("language") if isinstance(payload.get("language"), str) else None,
        "stargazers_count": 0,
        "updated_at": str(payload.get("updated_at") or "")[:128],
        "reason": reason,
        "match_score": float(payload.get("match_score", 0.0) or 0.0),
        "source_kind": source_kind,
        "source": source,
        "source_verified": bool(source.get("source_verified")),
        "source_paths": [str(path) for path in source.get("source_paths", []) if str(path).strip()],
        "source_evidence": [str(item) for item in source.get("source_evidence", []) if str(item).strip()],
        "code_verified": bool(source.get("code_verified")),
        "code_paths": [str(path) for path in source.get("code_paths", []) if str(path).strip()],
        "code_evidence": [str(item) for item in source.get("code_evidence", []) if str(item).strip()],
        "user_provided": True,
    }
    if not 0.0 <= candidate["match_score"] <= 1.0:
        raise ValueError("project.match_score 必须在 0 到 1 之间")
    if repository:
        candidate["repository"] = str(repository).strip()
    if paper is not None:
        candidate["paper_candidate"] = True
        candidate["paper_verified"] = bool(paper.get("verified"))
        candidate["paper_title"] = paper.get("title")
        candidate["paper_url"] = paper.get("url")
        candidate["venue"] = paper.get("venue")
        candidate["citation_count"] = paper.get("citation_count")
        candidate["paper_evidence"] = list(paper.get("evidence", []))
        candidate["paper"] = paper
    else:
        candidate["paper_candidate"] = source_kind == "paper_implementation"
        candidate["paper_verified"] = False
        candidate["paper_evidence"] = []
    candidate["project_key"] = _stable_user_project_key(candidate, source)

    user_projects = state.setdefault("user_projects", [])
    if not isinstance(user_projects, list):
        user_projects = []
        state["user_projects"] = user_projects
    _merge_candidate(user_projects, candidate)
    # Keep ``repos`` as the visible compatibility projection, retaining the
    # order of current search results and updating/replacing this candidate.
    repos = state.setdefault("repos", [])
    if not isinstance(repos, list):
        repos = []
        state["repos"] = repos
    _merge_candidate(repos, candidate)
    if _project_key(state.get("selected_project")) == candidate["project_key"]:
        state["selected_project"] = deepcopy(candidate)
    state.setdefault("user_projects_history", []).append(
        {"at": _now(), "project": deepcopy(candidate), "source": deepcopy(source)}
    )
    _event(
        state,
        "project_registered",
        project_key=candidate["project_key"],
        source_kind=source_kind,
        paper_verified=bool(candidate.get("paper_verified")),
    )
    return _result("自带项目已加入候选", project=deepcopy(candidate), state=_public_state(state))


# Keep a Python-level compatibility spelling for embedders that imported the
# helper directly.  Only ``register_project`` is exposed to the model so the
# tool list stays compact and unambiguous.
add_project = register_project


def _project_has_verified_source(project: Any) -> bool:
    """Return whether a candidate has grounded source proof for selection."""

    if not isinstance(project, dict):
        return False
    source = project.get("source") if isinstance(project.get("source"), dict) else {}
    code_verified = project.get("code_verified")
    source_verified = project.get("source_verified")
    if code_verified is False or source_verified is False:
        return False
    if code_verified is not True and source_verified is not True:
        return False
    paths = (
        project.get("code_paths")
        or project.get("source_paths")
        or source.get("code_paths")
        or source.get("source_paths")
    )
    evidence = (
        project.get("code_evidence")
        or project.get("source_evidence")
        or source.get("code_evidence")
        or source.get("source_evidence")
    )
    return isinstance(paths, list) and bool(paths) and isinstance(evidence, list) and bool(evidence)


def _require_selectable_project(project: dict[str, Any], *, historical_continuation: bool = False) -> None:
    """Enforce source provenance at the final recommendation boundary."""

    if historical_continuation and project.get("source_verified") is not True:
        # A project that is already active may be revisited after a restart;
        # requiring a fresh network check here would strand its progress.  This
        # exception is intentionally limited to the exact active identity and
        # never applies to a newly selected stale candidate.
        return
    if not _project_has_verified_source(project):
        raise ValueError(
            "无法选择未核验源码的项目：请重新 search_projects，或登记带有实现源码路径和核验依据的项目"
        )


def select_project(arguments: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    state = _state(ctx)
    records = _ensure_project_history(state)
    repos = state.get("repos") or []
    user_projects = state.get("user_projects") if isinstance(state.get("user_projects"), list) else []
    # ``repos`` is a compatibility snapshot and can be empty in a migrated or
    # manually edited session.  Merge durable user candidates for lookup while
    # appending only unseen keys, preserving every existing candidate index.
    candidate_repos: list[dict[str, Any]] = [item for item in repos if isinstance(item, dict)]
    existing_keys = {_project_key(item) for item in candidate_repos}
    for candidate in user_projects:
        if not isinstance(candidate, dict):
            continue
        key = _project_key(candidate)
        if key and key not in existing_keys:
            candidate_repos.append(candidate)
            existing_keys.add(key)
    if not candidate_repos and not records:
        raise ValueError("请先使用 search_projects 获取项目候选")
    requested_key = _first(arguments, "project_key", "identity_key", default=None)
    requested_name = _first(arguments, "full_name", "name", default=None)
    requested_url = _first(arguments, "html_url", "url", default=None)
    requested_index = _first(arguments, "index", default=None)
    selected: dict[str, Any] | None = None
    selected_from_history = False
    if requested_index is not None:
        try:
            index = int(requested_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("项目 index 必须是整数") from exc
        # Ranks exposed to users are intentionally one-based; accepting zero
        # as well would make model-generated calls ambiguous.
        if 1 <= index <= len(candidate_repos):
            selected = candidate_repos[index - 1]
        else:
            raise ValueError("项目 index 必须是从 1 开始的候选排名")
    else:
        for repo in candidate_repos:
            if requested_key and _project_key(repo) == requested_key:
                selected = repo
                break
            if requested_name and repo.get("full_name") == requested_name:
                selected = repo
                break
            if requested_name and repo.get("name") == requested_name:
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
                if requested_key and record.get("project_key") == requested_key:
                    selected = project
                    selected_from_history = True
                    break
                if requested_name and (
                    project.get("full_name") == requested_name or project.get("name") == requested_name
                ):
                    selected = project
                    selected_from_history = True
                    break
                if requested_url and project.get("html_url") == requested_url:
                    selected = project
                    selected_from_history = True
                    break
    if selected is None:
        raise ValueError("请选择 search_projects 返回的候选项目或历史练习项目")
    previous = state.get("selected_project") or {}
    previous_key = _project_key(previous)
    selected_key = _project_key(selected)
    _require_selectable_project(
        selected,
        historical_continuation=previous_key == selected_key,
    )
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
            "code_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "source_code_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "source_verified": {"type": "boolean"},
            "code_verified": {"type": "boolean"},
            "verified": {"type": "boolean"},
            "source_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "code_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "repository": {"type": "string", "minLength": 1},
            "url": {"type": "string", "minLength": 1},
        },
        # Canonical source/code evidence is accepted as the provenance field;
        # runtime validation still requires one non-empty evidence list for
        # every non-agent source.
        "required": ["kind"],
        "anyOf": [
            {"required": ["evidence"]},
            {"required": ["source_evidence"]},
            {"required": ["code_evidence"]},
        ],
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
                "set_project_filter",
                "保存当前会话的项目筛选偏好；后续检索会继承这些用户约束。",
                {
                    "project_filter": {
                        "type": "object",
                        "properties": {
                            "min_stars": {"type": "integer", "minimum": 0},
                            "include": {"type": "array", "items": {"type": "string"}},
                            "exclude": {"type": "array", "items": {"type": "string"}},
                            "other": {"type": "string"},
                            "paper_projects": {"type": "boolean"},
                            "rerank": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
                required=["project_filter"],
            ),
            handler=set_project_filter,
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
                    "include_paper_implementations": {"type": "boolean"},
                },
            ),
            handler=search_projects,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "register_project",
                (
                    "登记用户自己提供的复现项目候选。主体 Agent 必须先用受控 URL/文件读取工具检查材料；"
                    "工具会对本地代码或 GitHub 仓库执行有界的源码可用性核验，保存核验来源，不执行或自动选择项目。"
                ),
                {
                    "project": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "title": {"type": "string"},
                            "full_name": {"type": "string"},
                            "repository": {"type": "string"},
                            "url": {"type": "string"},
                            "html_url": {"type": "string"},
                            "description": {"type": "string"},
                            "language": {"type": "string"},
                            "reason": {"type": "string"},
                            "assessment": {},
                            "source_kind": {"type": "string", "enum": sorted(_PROJECT_SOURCE_KINDS)},
                            "source": _source_schema(_PROJECT_SOURCE_KINDS),
                            "source_verified": {"type": "boolean"},
                            "source_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
                            "source_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
                            "code_verified": {"type": "boolean"},
                            "code_paths": {"type": "array", "items": {"type": "string", "minLength": 1}},
                            "code_evidence": {"type": "array", "items": {"type": "string", "minLength": 1}},
                            "paper": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "url": {"type": "string"},
                                    "venue": {"type": "string"},
                                    "citation_count": {"type": "integer", "minimum": 0},
                                    "evidence": {"type": "array", "items": {"type": "string"}},
                                    "evidence_urls": {"type": "array", "items": {"type": "string"}},
                                },
                                "additionalProperties": False,
                            },
                        },
                        "required": ["name", "source"],
                        "additionalProperties": True,
                    },
                    "source": _source_schema(_PROJECT_SOURCE_KINDS),
                },
                required=["project"],
            ),
            handler=register_project,
            skill=SKILL_NAME,
        ),
        ToolDefinition(
            spec=_spec(
                "select_project",
                "从已保存的候选中选择实际要练习的项目。",
                {
                    "project_key": {"type": "string"},
                    "full_name": {"type": "string"},
                    "name": {"type": "string"},
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
    "set_project_filter",
    "normalize_project_filter",
    "build_plan",
    "update_profile",
    "plan",
    "search_projects",
    "register_project",
    "add_project",
    "select_project",
    "record_progress",
    "register_job_radar_tools",
]
