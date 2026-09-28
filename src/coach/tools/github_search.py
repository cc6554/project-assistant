"""工具⑤：GitHub 项目检索与「可复现/学习价值」打分。

两阶段：
1. 确定性检索与预筛（GitHub Search API）：多查询召回 → 去重 →
   star 数与近期活跃度打分，过滤归档/过老/过冷仓库；
2. LLM 复现适配度重排（可关）：读候选仓库 README 摘要，结合用户技能档案，
   判断「以该用户当前水平能否复现、复现能否补差距」给出分数与理由。

设置环境变量 GITHUB_TOKEN 可把搜索限额从 10 次/分钟提高到 30 次/分钟。
"""

from __future__ import annotations

import base64
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx
from pydantic import BaseModel

from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import GitHubRepo, UserSkillProfile

API_ROOT = "https://api.github.com"
README_CHAR_LIMIT = 3000
RERANK_POOL_WITH_TOKEN = 8
RERANK_POOL_NO_TOKEN = 4  # 无 token 限额 10 次/分钟：4 查询 + 4 README = 8 次，留余量


def _rerank_pool_size() -> int:
    return RERANK_POOL_WITH_TOKEN if os.environ.get("GITHUB_TOKEN") else RERANK_POOL_NO_TOKEN


def _max_queries() -> int:
    return 8 if os.environ.get("GITHUB_TOKEN") else 4

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
) -> list[GitHubRepo]:
    if not queries:
        return []

    candidates = _collect_candidates(queries, min_stars, recency_days)
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
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(timeout=20.0, headers=headers)


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
            q = f"{query} stars:>{min_stars - 1} pushed:>{pushed_after}"
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
                if item.get("archived") or item.get("disabled"):
                    continue
                full_name = item["full_name"]
                scored = _score_repo(item)
                if full_name not in seen or scored["det_score"] > seen[full_name]["det_score"]:
                    seen[full_name] = scored
    return list(seen.values())


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
        "age_days": age_days,
        "det_score": det,
        "readme": "",
    }


def _default_reason(item: dict) -> str:
    lang = f"（{item['language']}）" if item.get("language") else ""
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
    )
