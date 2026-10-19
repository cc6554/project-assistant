"""工具⑤：GitHub 项目检索与「可复现/学习价值」打分。

两阶段：
1. 确定性检索与预筛（GitHub Search API）：多查询召回 → 去重 →
   star 数与近期活跃度打分，过滤归档/过老/过冷仓库；
2. LLM 复现适配度重排（可关）：读候选仓库 README 摘要，结合用户技能档案，
   判断「以该用户当前水平能否复现、复现能否补差距」给出分数与理由。

认证只使用设置页显式保存的 Token；公共推荐强制过滤私有结果。
"""

from __future__ import annotations

import base64
from contextvars import ContextVar
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import GitHubRepo, UserSkillProfile
from .credentials import github_token

API_ROOT = "https://api.github.com"
README_CHAR_LIMIT = 3000
RERANK_POOL_WITH_TOKEN = 8
RERANK_POOL_NO_TOKEN = 4  # 无 token 限额 10 次/分钟：4 查询 + 4 README = 8 次，留余量
SOURCE_VERIFY_MAX_PATHS = 12
SOURCE_VERIFY_MAX_REQUESTS = 8
# Source checks are deliberately a small, bounded second pass.  Search can
# return many metadata hits, but recommendation quality only needs the top
# deterministic candidates and a few verified fallbacks.
SOURCE_VERIFY_MAX_REPOSITORIES = 12
SOURCE_VERIFY_TARGET_RESULTS = 8
SOURCE_VERIFY_TIMEOUT_SECONDS = 8.0
SOURCE_VERIFY_BUDGET_SECONDS = 20.0
_SOURCE_VERIFY_DEADLINE: ContextVar[float | None] = ContextVar(
    "github_source_verify_deadline", default=None
)

# A repository that only contains README/paper/data material is not a useful
# implementation candidate.  GitHub's language field is only a hint (and is
# absent for many small projects), so source verification is based on actual
# tree paths instead of that metadata.
_CODE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".clj",
        ".cljs",
        ".cpp",
        ".cs",
        ".css",
        ".dart",
        ".ex",
        ".exs",
        ".fs",
        ".fsx",
        ".go",
        ".groovy",
        ".h",
        ".hpp",
        ".hs",
        ".java",
        ".jl",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".m",
        ".mm",
        ".php",
        ".pl",
        ".pm",
        ".py",
        ".r",
        ".rb",
        ".rs",
        ".scala",
        ".scm",
        ".sh",
        ".sql",
        ".swift",
        ".tcl",
        ".ts",
        ".tsx",
        ".vb",
        ".vue",
        ".zig",
        # A notebook contains executable source cells, unlike a plain data
        # export.  It is useful evidence for research implementations.
        ".ipynb",
    }
)
# Build/container manifests are useful provenance but do not themselves prove
# that an implementation is present.  Keep the acceptance rule focused on
# executable/source files (including notebooks with code cells).
_CODE_FILENAMES = frozenset()
_SOURCE_DOC_FILENAMES = frozenset(
    {
        "readme",
        "readme.md",
        "readme.rst",
        "readme.txt",
        "changelog.md",
        "contributing.md",
        "license",
        "license.md",
        "citation.cff",
    }
)
_SOURCE_DATA_EXTENSIONS = frozenset(
    {
        ".arrow",
        ".bin",
        ".csv",
        ".feather",
        ".json",
        ".jsonl",
        ".parquet",
        ".pickle",
        ".pkl",
        ".pt",
        ".pth",
        ".safetensors",
        ".sqlite",
        ".sqlite3",
        ".tsv",
        ".xls",
        ".xlsx",
        ".yaml",
        ".yml",
    }
)
_REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _repository_identity(value: Any) -> str | None:
    """Normalize a GitHub ``owner/repository`` identity from API/user input.

    Search responses are remote input too.  Keep repository and path components
    constrained before interpolating them into a fixed API origin, especially
    when the client carries a private-repository token.
    """

    clean = str(value or "").strip().strip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]
    parts = clean.split("/")
    if len(parts) != 2 or any(not _REPOSITORY_PART_RE.fullmatch(part) for part in parts):
        return None
    return "/".join(parts)


def _repository_path(value: Any) -> str | None:
    """Return a safe repository-relative path for contents fallback calls."""

    raw = str(value or "").replace("\\", "/").strip().strip("/")
    if not raw or "\x00" in raw:
        return None
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _rerank_pool_size() -> int:
    return RERANK_POOL_WITH_TOKEN if github_token() else RERANK_POOL_NO_TOKEN


def _max_queries() -> int:
    return 8 if github_token() else 4


def is_source_code_path(path: Any) -> bool:
    """Return whether a repository/local path can evidence implementation code.

    Names such as ``README.md`` and data/config exports are intentionally not
    counted.  This helper is shared by the collector and the user-project
    registration path, so a local import follows the same acceptance rule as a
    public GitHub candidate.
    """

    if not isinstance(path, str):
        return False
    value = path.replace("\\", "/").strip().strip("/")
    if not value:
        return False
    name = value.rsplit("/", 1)[-1].casefold()
    if name in _SOURCE_DOC_FILENAMES or name.startswith(("readme.", "license.", "changelog.", "citation.")):
        return False
    if name in _CODE_FILENAMES:
        return True
    suffix = os.path.splitext(name)[1]
    return suffix in _CODE_EXTENSIONS and suffix not in _SOURCE_DATA_EXTENSIONS


def _source_url(full_name: str, branch: str, path: str, *, kind: str = "blob") -> str:
    """Build a stable public provenance URL for a verified source path."""

    encoded_branch = quote(str(branch or "HEAD"), safe="/")
    encoded_path = quote(path.strip("/"), safe="/")
    return f"https://github.com/{full_name}/{kind}/{encoded_branch}/{encoded_path}"


def _source_paths_from_tree(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
        return []
    paths: list[str] = []
    for raw in payload["tree"]:
        if not isinstance(raw, dict) or str(raw.get("type") or "").casefold() != "blob":
            continue
        if raw.get("size") is not None:
            try:
                if int(raw.get("size") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        path = _repository_path(raw.get("path"))
        if path and is_source_code_path(path):
            paths.append(path)
            if len(paths) >= SOURCE_VERIFY_MAX_PATHS:
                break
    return paths


def _source_paths_from_contents(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        return []
    paths: list[str] = []
    for raw in payload:
        if not isinstance(raw, dict) or str(raw.get("type") or "").casefold() != "file":
            continue
        if raw.get("size") is not None:
            try:
                if int(raw.get("size") or 0) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
        path = _repository_path(raw.get("path") or raw.get("name"))
        if path and is_source_code_path(path):
            paths.append(path)
            if len(paths) >= SOURCE_VERIFY_MAX_PATHS:
                break
    return paths


def _verify_repository_source(client: httpx.Client, item: dict[str, Any]) -> dict[str, Any]:
    """Verify that a public repository exposes implementation source.

    The GitHub search response contains no trustworthy source-availability
    signal.  Inspect the bounded recursive tree first, then fall back to the
    contents endpoint for GitHub Enterprise/mock responses that do not expose
    the git tree.  A failure is represented as ``verified=False`` and never
    upgraded from a README, paper, language label, or star count.
    """

    full_name = _repository_identity(item.get("full_name"))
    branch = str(item.get("default_branch") or "main").strip() or "main"
    if not full_name:
        return {"verified": False, "paths": [], "reason": "缺少 repository identity"}
    deadline = _SOURCE_VERIFY_DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        return {"verified": False, "paths": [], "reason": "源码核验达到时间上限"}

    tree_endpoint = f"{API_ROOT}/repos/{full_name}/git/trees/{quote(branch, safe='')}"
    try:
        response = client.get(tree_endpoint, params={"recursive": "1"})
    except httpx.HTTPError as exc:
        return {"verified": False, "paths": [], "reason": f"无法核验仓库源码目录：{exc.__class__.__name__}"}
    if response.status_code == 200:
        try:
            paths = _source_paths_from_tree(response.json())
        except (TypeError, ValueError):
            paths = []
        if paths:
            return {
                "verified": True,
                "paths": paths,
                "branch": branch,
                "evidence": [_source_url(full_name, branch, path) for path in paths],
            }
        # Continue to the contents fallback.  A truncated/compatible tree
        # response may legitimately omit blobs even though the repository root
        # exposes them through the contents endpoint.
    if deadline is not None and time.monotonic() >= deadline:
        return {"verified": False, "paths": [], "reason": "源码核验达到时间上限"}

    # Some compatible GitHub APIs do not expose /git/trees.  A bounded
    # contents request still gives useful file-path provenance and is enough
    # to avoid accepting a README/data-only repository.
    endpoint = f"{API_ROOT}/repos/{full_name}/contents"
    try:
        response = client.get(endpoint, params={"ref": branch, "per_page": 100})
    except httpx.HTTPError as exc:
        return {"verified": False, "paths": [], "reason": f"无法核验仓库源码目录：{exc.__class__.__name__}"}
    if response.status_code != 200:
        return {
            "verified": False,
            "paths": [],
            "reason": f"源码目录核验返回 HTTP {response.status_code}",
        }
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = []
    paths = _source_paths_from_contents(payload)
    # A root listing commonly contains ``src/`` or ``lib/`` rather than the
    # files themselves.  Walk only a small set of directory entries to keep
    # this guard bounded; the recursive tree path above is preferred.
    # The tree request and this root contents request are already two calls;
    # include both in the per-repository bound before walking directories.
    requests_used = 2
    if not paths and isinstance(payload, list):
        directories = [
            raw for raw in payload
            if isinstance(raw, dict) and str(raw.get("type") or "").casefold() == "dir"
        ]
        for directory in directories[: SOURCE_VERIFY_MAX_REQUESTS - requests_used]:
            if deadline is not None and time.monotonic() >= deadline:
                break
            directory_path = _repository_path(directory.get("path") or directory.get("name"))
            # Never replay an API-provided URL verbatim: a malicious/odd
            # response must not redirect the authenticated client to another
            # origin.  Reconstruct the fixed GitHub API endpoint from the
            # validated repository identity and path instead.
            if not directory_path:
                continue
            directory_url = f"{API_ROOT}/repos/{full_name}/contents/{quote(directory_path, safe='/')}"
            try:
                nested = client.get(directory_url, params={"ref": branch, "per_page": 100})
            except httpx.HTTPError:
                continue
            requests_used += 1
            if nested.status_code != 200:
                continue
            try:
                paths.extend(_source_paths_from_contents(nested.json()))
            except (TypeError, ValueError):
                continue
            if paths:
                paths = paths[:SOURCE_VERIFY_MAX_PATHS]
                break
    if not paths:
        return {
            "verified": False,
            "paths": [],
            "branch": branch,
            "reason": "仓库目录中没有可识别的实现源码文件",
        }
    return {
        "verified": True,
        "paths": paths[:SOURCE_VERIFY_MAX_PATHS],
        "branch": branch,
        "evidence": [_source_url(full_name, branch, path) for path in paths[:SOURCE_VERIFY_MAX_PATHS]],
    }


def verify_github_repository_source(full_name: str) -> dict[str, Any]:
    """Verify a user-supplied public or token-authorized GitHub repository.

    This is intentionally separate from search: a user may supply a private
    repository URL that is visible through the workspace token even though
    public discovery always uses ``is:public``.  The returned evidence contains
    only repository identity, branch, and source paths; credentials never leave
    the scoped HTTP client.
    """

    clean = _repository_identity(full_name)
    if not clean:
        return {"verified": False, "paths": [], "reason": "GitHub repository identity 无效"}
    metadata_endpoint = f"{API_ROOT}/repos/{clean}"
    with _client() as client:
        try:
            metadata_response = client.get(metadata_endpoint)
        except httpx.HTTPError as exc:
            return {"verified": False, "paths": [], "reason": f"无法读取 GitHub repository：{exc.__class__.__name__}"}
        if metadata_response.status_code != 200:
            return {
                "verified": False,
                "paths": [],
                "reason": f"GitHub repository 返回 HTTP {metadata_response.status_code}（需公开访问或当前 Token 授权）",
            }
        try:
            metadata = metadata_response.json()
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        item = {
            "full_name": clean,
            "default_branch": str(metadata.get("default_branch") or "main"),
        }
        result = _verify_repository_source(client, item)
        result["private"] = bool(metadata.get("private"))
        result["html_url"] = str(metadata.get("html_url") or f"https://github.com/{clean}")
        return result

_RERANK_PROMPT = """你在为一个求学者挑选最适合「动手复现」的 GitHub 项目。

用户情况：
{profile}

学习计划给出的检索意图（按优先级）：
{queries}

下面是候选仓库（含 README 摘要）。请逐一判断：
1. match_score（0~1）：与用户技能缺口的相关度 × 对用户当前水平的可复现性——
   纯论文收藏、教程清单、企业级全家桶部署、需要海量算力的项目要降分；
   代码结构清晰、能在单机跑通、覆盖核心技术点的实现型项目给高分；
2. reason：一句话中文说明推荐/不推荐理由（不超过 60 字）。
只给检索词相关方向的项目高分。严格通过 emit_result 工具输出全部候选的评分。"""


class _RerankItem(BaseModel):
    full_name: str
    match_score: float
    reason: str


class _RerankResult(BaseModel):
    items: list[_RerankItem]


def search_github_projects(
    router: ModelRouter,
    queries: list[str],
    profile: UserSkillProfile | None = None,
    *,
    min_stars: int = 50,
    recency_days: int = 365,
    top_n: int = 10,
    llm_rerank: bool = True,
    task: str = "planning",
    preferences: dict[str, list[str]] | None = None,
    paper_queries: list[str] | None = None,
    paper_recency_days: int | None = None,
) -> list[GitHubRepo]:
    """Search implementation repositories and optional paper candidates.

    ``paper_queries`` is intentionally a separate discovery channel.  A match
    only means that GitHub returned a repository for a paper/reproduction
    query; it does not verify a paper, venue, or citation count.  The caller
    (the main agent) must inspect README and paper sources before making those
    claims.
    """
    if not queries:
        return []

    preferences = preferences or {}
    candidates = _collect_candidates(queries, min_stars, recency_days)
    if paper_queries:
        # Reproduction code for an established paper may be intentionally
        # dormant.  Keep the user's star floor, but use a wider bounded
        # activity window for the paper discovery channel unless the caller
        # explicitly supplies one.
        paper_window = paper_recency_days if paper_recency_days is not None else max(recency_days, 3650)
        paper_candidates = _collect_candidates(paper_queries, min_stars, paper_window)
        # Preserve the deterministic score from the best query while carrying
        # a discovery-only paper flag into the public candidate schema.
        by_name = {item.get("full_name"): item for item in candidates}
        for item in paper_candidates:
            full_name = item.get("full_name")
            if not full_name:
                continue
            item["paper_candidate"] = True
            current = by_name.get(full_name)
            if current is None or item.get("det_score", 0) > current.get("det_score", 0):
                by_name[full_name] = item
            else:
                current["paper_candidate"] = True
        candidates = list(by_name.values())
    # Keep this second guard at the public function boundary as well as inside
    # the HTTP collector.  It protects callers that inject a cached/fixture
    # candidate list and ensures no stale metadata-only candidate reaches
    # ranking or recommendation.
    def verified_source(item: dict[str, Any]) -> bool:
        source_flag = item.get("source_verified")
        code_flag = item.get("code_verified")
        if source_flag is False or code_flag is False:
            return False
        if source_flag is not True and code_flag is not True:
            return False
        paths = item.get("code_paths") or item.get("source_paths")
        evidence = item.get("code_evidence") or item.get("source_evidence")
        return isinstance(paths, list) and bool(paths) and isinstance(evidence, list) and bool(evidence)

    candidates = [item for item in candidates if isinstance(item, dict) and verified_source(item)]
    include = [x.strip().casefold() for x in preferences.get("include", []) if x.strip()]
    exclude = [x.strip().casefold() for x in preferences.get("exclude", []) if x.strip()]
    def allowed(item):
        text = (item["full_name"] + " " + item.get("description", "")).casefold()
        return not any(x in text for x in exclude) and (not include or any(x in text for x in include))
    candidates = [item for item in candidates if allowed(item)]
    if not candidates:
        return []

    ranked = sorted(candidates, key=lambda r: r["det_score"], reverse=True)
    pool = ranked[: _rerank_pool_size()]

    if llm_rerank:
        try:
            pool = _llm_rerank(router, pool, queries, profile, task)
        except Exception:
            # 重排失败不致命：退回纯确定性排序
            for item in pool:
                item["match_score"] = item["det_score"]
                item["reason"] = _default_reason(item)
    else:
        for item in pool:
            item["match_score"] = item["det_score"]
            item["reason"] = _default_reason(item)

    pool.sort(key=lambda r: r["match_score"], reverse=True)
    return [_to_repo(item) for item in pool[:top_n]]


# ── 阶段 1：确定性检索 ─────────────────────────────────────────

def _client() -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(timeout=SOURCE_VERIFY_TIMEOUT_SECONDS, headers=headers)


def _collect_candidates(
    queries: Iterable[str],
    min_stars: int,
    recency_days: int,
) -> list[dict]:
    pushed_after = (
        (datetime.now(timezone.utc) - timedelta(days=recency_days)).date().isoformat()
    )
    seen: dict[str, dict] = {}

    with _client() as client:
        for query in list(queries)[: _max_queries()]:
            q = f"{query} is:public stars:>{min_stars - 1} pushed:>{pushed_after}"
            try:
                resp = client.get(
                    f"{API_ROOT}/search/repositories",
                    params={"q": q, "sort": "stars", "order": "desc", "per_page": 20},
                )
            except httpx.HTTPError:
                continue
            if resp.status_code in (403, 429):
                # 触发限流：保留已召回结果，停止后续查询
                break
            if resp.status_code != 200:
                continue

            for item in resp.json().get("items", []):
                if item.get("private") is not False or item.get("visibility", "public") != "public" or item.get("archived") or item.get("disabled"):
                    continue
                full_name = _repository_identity(item.get("full_name"))
                if not full_name:
                    continue
                item = dict(item)
                item["full_name"] = full_name
                scored = _score_repo(item)
                if full_name not in seen or scored["det_score"] > seen[full_name]["det_score"]:
                    seen[full_name] = scored

        # Verify only the highest deterministic candidates.  This keeps the
        # source proof useful for ranking while placing a hard bound on both
        # repository count and elapsed time when GitHub is slow or partially
        # unavailable.
        ranked = sorted(seen.values(), key=lambda row: row["det_score"], reverse=True)
        deadline = time.monotonic() + SOURCE_VERIFY_BUDGET_SECONDS
        deadline_token = _SOURCE_VERIFY_DEADLINE.set(deadline)
        verified_count = 0
        verified: list[dict[str, Any]] = []
        try:
            for scored in ranked[:SOURCE_VERIFY_MAX_REPOSITORIES]:
                if time.monotonic() >= deadline:
                    break
                source = _verify_repository_source(client, scored)
                if not source.get("verified"):
                    continue
                scored.update(
                    {
                        "source_verified": True,
                        "source_paths": list(source.get("paths") or []),
                        "source_evidence": list(source.get("evidence") or []),
                        "code_verified": True,
                        "code_paths": list(source.get("paths") or []),
                        "code_evidence": list(source.get("evidence") or []),
                        "source_branch": source.get("branch") or scored.get("default_branch") or "main",
                    }
                )
                verified.append(scored)
                verified_count += 1
                if verified_count >= min(SOURCE_VERIFY_TARGET_RESULTS, _rerank_pool_size()):
                    break
        finally:
            _SOURCE_VERIFY_DEADLINE.reset(deadline_token)
    return verified


def _score_repo(item: dict) -> dict:
    stars = item.get("stargazers_count", 0)
    star_score = min(math.log10(max(stars, 1)) / math.log10(5000), 1.0)

    pushed_at = item.get("pushed_at", "")
    age_days = 365
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        age_days = max(
            (datetime.now(timezone.utc) - pushed).days, 0
        )
        recency_score = max(0.0, 1 - age_days / 365)
    except (ValueError, AttributeError):
        recency_score = 0.0

    det = round(0.7 * star_score + 0.3 * recency_score, 3)
    return {
        "full_name": item["full_name"],
        "html_url": item["html_url"],
        "description": (item.get("description") or "").strip(),
        "language": item.get("language"),
        "stargazers_count": stars,
        "updated_at": pushed_at,
        "default_branch": str(item.get("default_branch") or "main"),
        "age_days": age_days,
        "det_score": det,
        "readme": "",
        "paper_candidate": False,
        "source_verified": False,
        "source_paths": [],
        "source_evidence": [],
        "code_verified": False,
        "code_paths": [],
        "code_evidence": [],
        "source_branch": str(item.get("default_branch") or "main"),
    }


def _default_reason(item: dict) -> str:
    lang = f"（{item['language']}）" if item.get("language") else ""
    if item.get("paper_candidate"):
        return f"论文/复现检索命中{lang}，需读取来源核验 venue 与引用信息"
    return f"{item['stargazers_count']} stars{lang}，近一年有更新，可作复现候选"


# ── 阶段 2：README + LLM 重排 ──────────────────────────────────

def _fetch_readme(client: httpx.Client, full_name: str) -> str:
    resp = client.get(f"{API_ROOT}/repos/{full_name}/readme")
    if resp.status_code != 200:
        return ""
    payload = resp.json().get("content", "")
    try:
        text = base64.b64decode(payload).decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text[:README_CHAR_LIMIT]


def _llm_rerank(
    router: ModelRouter,
    pool: list[dict],
    queries: list[str],
    profile: UserSkillProfile | None,
    task: str,
) -> list[dict]:
    with _client() as client:
        for item in pool:
            item["readme"] = _fetch_readme(client, item["full_name"])

    profile_text = (
        profile.summary + "\n技能：" + ", ".join(s.name for s in profile.skills)
        if profile
        else "未知"
    )
    lines = []
    for i, item in enumerate(pool, 1):
        lines.append(
            f"### 候选 {i}: {item['full_name']} "
            f"(stars={item['stargazers_count']}, language={item['language']})\n"
            f"描述：{item['description']}\nREADME 摘要：\n{item['readme'][:2000]}"
        )

    result = router.complete_json(
        task,
        [
            Message(
                role="system",
                content=_RERANK_PROMPT.format(
                    profile=profile_text,
                    queries="\n".join(f"- {q}" for q in queries),
                ),
            ),
            Message(role="user", content="\n\n".join(lines)),
        ],
        _RerankResult.model_json_schema(),
        tool_name="emit_result",
        tool_description="提交全部候选仓库的复现适配度评分",
        temperature=0.1,
        max_tokens=2048,
    )
    scores = {item.full_name: item for item in _RerankResult.model_validate(result).items}

    for item in pool:
        judged = scores.get(item["full_name"])
        if judged is None:
            item["match_score"] = item["det_score"]
            item["reason"] = _default_reason(item)
            continue
        llm_score = max(0.0, min(1.0, judged.match_score))
        item["match_score"] = round(0.65 * llm_score + 0.35 * item["det_score"], 3)
        item["reason"] = judged.reason or _default_reason(item)
    return pool


def _to_repo(item: dict) -> GitHubRepo:
    return GitHubRepo(
        full_name=item["full_name"],
        html_url=item["html_url"],
        description=item["description"],
        language=item["language"],
        stargazers_count=item["stargazers_count"],
        updated_at=item["updated_at"],
        reason=item.get("reason", ""),
        match_score=item.get("match_score", item["det_score"]),
        paper_candidate=bool(item.get("paper_candidate", False)),
        # Search never fabricates paper metadata.  These fields stay empty
        # until the main agent verifies a source explicitly.
        paper_verified=False,
        paper_title=None,
        paper_url=None,
        venue=None,
        citation_count=None,
        paper_evidence=[],
        source_verified=bool(item.get("source_verified")) if "source_verified" in item else None,
        source_paths=[str(path) for path in item.get("source_paths", []) if str(path).strip()],
        source_evidence=[str(evidence) for evidence in item.get("source_evidence", []) if str(evidence).strip()],
        code_verified=bool(item.get("code_verified")) if "code_verified" in item else None,
        code_paths=[str(path) for path in item.get("code_paths", []) if str(path).strip()],
        code_evidence=[str(evidence) for evidence in item.get("code_evidence", []) if str(evidence).strip()],
    )
