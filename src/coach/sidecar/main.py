"""Tauri 桌面端 sidecar：FastAPI 本地服务（127.0.0.1:17689）。

把 coach 内核的确定性工具 + LLM 路由暴露为 HTTP API：
  会话管理 / JD 截图解析 / 技能档案 / 访谈 / 计划与 GitHub 项目 / 导师指导 / 模型配置。
仅监听回环地址，供本机 Tauri 窗口调用。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.llm.registry import VALID_API_MODES, load_config
from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import JDCard
from ..tools import documents, github_search, interviewer, jd_parser, planner, profile_builder, web_search
from ..tools.agent import AGENT_SYSTEM_PROMPT, AGENT_TOOLS, AgentRuntime, PendingAction
from ..tools.pipeline import plan_and_recommend
from . import paths
from .store import SessionNotFound, SessionStore

PORT = 17689

app = FastAPI(title="job-radar-coach sidecar", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 仅回环监听；Tauri webview 来源 http://127.0.0.1:17689
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv(paths.env_path())
store = SessionStore()


# ── 路由获取（每次读 yaml，支持运行时改配置）───────────────────────

def get_router() -> ModelRouter:
    try:
        return ModelRouter.from_yaml(paths.config_path())
    except Exception as exc:  # noqa: BLE001 - 转成对前端友好的错误
        raise HTTPException(
            status_code=400,
            detail=f"模型配置无法加载：{exc}（请在『模型配置』页检查）",
        ) from exc


def _session(session_id: str):
    try:
        return store.get(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc


# ── 基础 ─────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"ok": True, "port": PORT}


# ── 模型配置 ─────────────────────────────────────────────────────

@app.get("/api/config")
def get_config() -> dict:
    raw = yaml.safe_load(paths.config_path().read_text(encoding="utf-8")) or {}
    providers = raw.get("providers") or {}
    out_providers: dict[str, dict] = {}
    models_in_use: list[str] = []
    for name, spec in providers.items():
        key_env = spec.get("api_key_env")
        key_configured = bool(key_env) and bool(os.environ.get(key_env))
        out_providers[name] = {
            "api_mode": spec.get("api_mode", "chat_completions"),
            "base_url": spec.get("base_url"),
            "api_key_env": key_env,
            "timeout": spec.get("timeout", 120),
            "key_configured": key_configured,
        }
        for chain in (raw.get("tasks") or {}).values():
            for item in chain:
                if item.get("provider") == name and item.get("model") not in models_in_use:
                    models_in_use.append(item["model"])
    return {
        "providers": out_providers,
        "tasks": raw.get("tasks") or {},
        "models_in_use": models_in_use,
        "config_path": str(paths.config_path()),
    }


class ProviderInput(BaseModel):
    name: str
    api_mode: str = "chat_completions"
    base_url: str
    api_key_env: str | None = None
    api_key: str | None = None  # 提供则写入 .env
    timeout: float = 120.0


class ConfigInput(BaseModel):
    providers: list[ProviderInput]
    tasks: dict[str, list[dict[str, str]]]


@app.put("/api/config")
def put_config(cfg: ConfigInput) -> dict:
    if not cfg.providers:
        raise HTTPException(status_code=400, detail="至少需要一个 provider")
    for p in cfg.providers:
        if p.api_mode not in VALID_API_MODES:
            raise HTTPException(status_code=400, detail=f"{p.name} 的 api_mode 不支持")
        if not p.base_url:
            raise HTTPException(status_code=400, detail=f"{p.name} 缺少 base_url")

    providers_map: dict[str, dict[str, Any]] = {}
    for p in cfg.providers:
        entry: dict[str, Any] = {
            "api_mode": p.api_mode,
            "base_url": p.base_url,
            "timeout": p.timeout,
        }
        if p.api_key_env:
            entry["api_key_env"] = p.api_key_env
        providers_map[p.name] = entry

    doc = {
        "providers": providers_map,
        "tasks": cfg.tasks,
    }
    with paths.config_path().open("w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)

    for p in cfg.providers:
        if p.api_key and p.api_key_env:
            set_key(paths.env_path(), p.api_key_env, p.api_key)
            os.environ[p.api_key_env] = p.api_key

    # 写完后校验配置合法
    try:
        load_config(paths.config_path())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"配置写入但校验失败：{exc}") from exc
    return {"ok": True}


# ── 会话管理 ─────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return store.list_sessions()


@app.post("/api/sessions")
def create_session() -> dict:
    state = store.create()
    return {"id": state.session_id}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    return _session(session_id).model_dump(exclude_none=True)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    store.delete(session_id)
    return {"ok": True}


# ── 环节①：JD 截图解析 ──────────────────────────────────────────

@app.post("/api/sessions/{session_id}/jd")
async def parse_jd(session_id: str, files: list[UploadFile] = File(...)) -> dict:
    state = _session(session_id)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一张岗位截图")
    contents: dict[str, bytes] = {}
    for f in files:
        data = await f.read()
        if not data:
            continue
        contents[f.filename or "screenshot.png"] = data
    if not contents:
        raise HTTPException(status_code=400, detail="上传文件均为空")
    paths_ = store.save_uploads(session_id, contents)

    router = get_router()
    try:
        jd = jd_parser.parse_jd_screenshots(router, paths_)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"JD 识别失败：{exc}") from exc

    state.jd = jd
    store.save(state)
    return {"jd": jd.model_dump(exclude_none=True), "uploads": paths_}


@app.put("/api/sessions/{session_id}/jd")
def update_jd(session_id: str, jd: JDCard) -> dict:
    state = _session(session_id)
    state.jd = jd
    store.save(state)
    return {"ok": True, "jd": jd.model_dump(exclude_none=True)}


class JdSearchInput(BaseModel):
    query: str


_SEARCH_QUERY_PROMPT = "你是招聘信息检索助手。用户想找某类岗位的招聘 JD。\n\n输入：{query}\n\n要求：生成 2~3 条用于搜索的完整查询词（中文为主），每条聚焦一个角度（岗位名+方向 / 岗位名+城市 / 岗位名+要求关键词），能直接用于搜索引擎；严格通过 emit_result 工具输出 queries 字段。"


class _SearchQueries(BaseModel):
    queries: list[str] = []


@app.post("/api/sessions/{session_id}/jd/search")
def search_jd(session_id: str, body: JdSearchInput) -> dict:
    """方式 A：用户说岗位 → 联网搜索招聘 JD 文本 → 解析为 JDCard。"""
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请填写岗位名称或方向")
    state = _session(session_id)
    router = get_router()

    try:
        qraw = router.complete_json(
            "default",
            [Message(role="user", content=_SEARCH_QUERY_PROMPT.format(query=query))],
            _SearchQueries.model_json_schema(),
            tool_name="emit_result",
            tool_description="提交搜索查询词列表",
            temperature=0.2,
        )
        queries = (_SearchQueries.model_validate(qraw).queries or [query])[:3]
    except Exception:  # noqa: BLE001 - 生成搜索词失败就直接用原文搜
        queries = [query]

    texts: list[str] = []
    seen: set[str] = set()
    last_err: Exception | None = None
    for q in queries:
        try:
            for line in web_search.search_texts(q, n=6):
                key = line[:60]
                if key not in seen:
                    seen.add(key)
                    texts.append(line)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        if len(texts) >= 8:
            break
    if not texts:
        raise HTTPException(
            status_code=502,
            detail=f"搜索没有拿到任何岗位文本：{last_err or '未知错误'}，请稍后重试或改用上传截图",
        )

    try:
        jd = jd_parser.parse_jd_text(router, texts, task="parsing")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"岗位信息解析失败：{exc}") from exc

    state.jd = jd
    store.save(state)
    return {"jd": jd.model_dump(exclude_none=True), "sources": len(texts)}


# ── 环节②：技能档案 ─────────────────────────────────────────────

class ProfileTextInput(BaseModel):
    text: str
    source: str  # resume / work_log / self_report / chat


@app.post("/api/sessions/{session_id}/profile/text")
def profile_from_text(session_id: str, body: ProfileTextInput) -> dict:
    state = _session(session_id)
    router = get_router()
    try:
        profile = profile_builder.build_profile_from_text(
            router, body.text, source=body.source, existing=state.profile
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"技能档案解析失败：{exc}") from exc
    state.profile = profile
    store.save(state)
    return profile.model_dump(exclude_none=True)


@app.post("/api/sessions/{session_id}/profile/documents")
async def profile_from_documents(
    session_id: str, source: str = Form(...), files: list[UploadFile] = File(...)
) -> dict:
    state = _session(session_id)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文档")
    contents: dict[str, bytes] = {}
    for f in files:
        data = await f.read()
        if data:
            contents[f.filename or "doc.pdf"] = data
    paths_ = store.save_uploads(session_id, contents)

    router = get_router()
    try:
        profile = profile_builder.build_profile_from_documents(
            router, paths_, source=source, existing=state.profile
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"文档档案解析失败：{exc}") from exc
    state.profile = profile
    store.save(state)
    return profile.model_dump(exclude_none=True)


# ── 环节②b：访谈 ────────────────────────────────────────────────

@app.get("/api/sessions/{session_id}/interview/question")
def interview_question(session_id: str) -> dict:
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案再开始访谈")
    router = get_router()
    try:
        question = interviewer.next_interview_question(
            router, state.profile, state.history_pairs()
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成访谈问题失败：{exc}") from exc
    return {"question": question, "done": question is None}


class InterviewAnswerInput(BaseModel):
    question: str
    answer: str


@app.post("/api/sessions/{session_id}/interview/answer")
def interview_answer(session_id: str, body: InterviewAnswerInput) -> dict:
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    router = get_router()
    try:
        profile = interviewer.apply_interview_answer(
            router, state.profile, body.question, body.answer
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"应用访谈答案失败：{exc}") from exc
    state.profile = profile
    state.interview_history.append([body.question, body.answer])
    store.save(state)
    return profile.model_dump(exclude_none=True)


# ── 环节②c：Obsidian 笔记库读取 ─────────────────────────────────

class ObsidianInput(BaseModel):
    vault_path: str
    max_files: int | None = None  # None = 不限制，读取全部笔记


_SKIP_DIR_PARTS = {
    ".obsidian", ".trash", "attachments", "images", "files", "assets",
    "templates", "template", "node_modules", "venv", ".venv", ".git",
    "__pycache__", "_internal", "target", "dist", "build",
}


@app.post("/api/sessions/{session_id}/profile/obsidian")
def profile_from_obsidian(session_id: str, body: ObsidianInput) -> dict:
    """读取用户 Obsidian 库中最近的 Markdown 经历/日志，并入技能档案。"""
    vault = Path(body.vault_path).expanduser()
    if not vault.is_dir():
        raise HTTPException(status_code=400, detail=f"目录不存在：{vault}")
    state = _session(session_id)

    md_files: list[Path] = []
    for p in vault.rglob("*.md"):
        if any(part in _SKIP_DIR_PARTS for part in p.parts):
            continue
        md_files.append(p)
    if not md_files:
        raise HTTPException(status_code=400, detail=f"该目录下没有找到 Markdown 笔记：{vault}")
    md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # 不限制篇数：全部读取（max_files 为空或 <=0 时）；否则只取最近 N 篇
    if body.max_files and body.max_files > 0:
        selected = md_files[: body.max_files]
    else:
        selected = md_files

    router = get_router()
    profile = state.profile
    failed: list[str] = []
    for p in selected:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            profile = profile_builder.build_profile_from_text(
                router, text, source="obsidian", existing=profile
            )
        except Exception as exc:  # noqa: BLE001 - 单篇失败跳过，不让整批挂掉
            failed.append(f"{p.name}: {exc}")
            continue
    assert profile is not None
    state.profile = profile
    store.save(state)
    return {
        **profile.model_dump(exclude_none=True),
        "read_files": [str(p.relative_to(vault)) for p in selected],
        "failed_files": failed,
    }


# ── 环节②c2：通用本地路径读取（任意目录，agent 自己读路径下文件）──────

class LocalPathInput(BaseModel):
    path: str
    max_files: int | None = None  # None = 不限制


_SUPPORTED_DOC_SUFFIXES = {".md", ".markdown", ".txt", ".log", ".pdf"}


@app.post("/api/sessions/{session_id}/profile/path")
def profile_from_path(session_id: str, body: LocalPathInput) -> dict:
    """读取本地路径下全部支持的文件（md/txt/log/pdf），逐份并入技能档案。"""
    root = Path(body.path).expanduser()
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在：{root}")
    state = _session(session_id)

    files: list[Path] = []
    if root.is_dir():
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in _SUPPORTED_DOC_SUFFIXES:
                continue
            if any(part in _SKIP_DIR_PARTS for part in p.parts):
                continue
            files.append(p)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        files = [root]

    if not files:
        raise HTTPException(status_code=400, detail=f"该路径下没有找到支持的文件（md/txt/log/pdf）：{root}")
    if body.max_files and body.max_files > 0:
        files = files[: body.max_files]

    router = get_router()
    profile = state.profile
    failed: list[str] = []
    read_names: list[str] = []
    for p in files:
        try:
            text = documents.read_document(p)
            profile = profile_builder.build_profile_from_text(
                router, text, source="local_path", existing=profile
            )
            read_names.append(str(p.relative_to(root)) if root.is_dir() else p.name)
        except Exception as exc:  # noqa: BLE001 - 单文件失败跳过
            failed.append(f"{p.name}: {exc}")
            continue
    assert profile is not None
    state.profile = profile
    store.save(state)
    return {
        **profile.model_dump(exclude_none=True),
        "read_files": read_names,
        "failed_files": failed,
    }


# ── 环节②d：待澄清内容 → 对话式澄清（不再只是列表展示）──────────

@app.get("/api/sessions/{session_id}/profile/clarify/next")
def clarify_next(session_id: str) -> dict:
    """取档案中下一条待澄清问题；没有则返回 done。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    questions = state.profile.open_questions
    if not questions:
        return {"question": None, "remaining": 0, "done": True}
    return {"question": questions[0], "remaining": len(questions), "done": False}


class ClarifyAnswerInput(BaseModel):
    question: str
    answer: str


@app.post("/api/sessions/{session_id}/profile/clarify/answer")
def clarify_answer(session_id: str, body: ClarifyAnswerInput) -> dict:
    """用户回答一条待澄清问题 → 并入档案 → 返回下一条待澄清。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    router = get_router()
    try:
        profile = interviewer.apply_interview_answer(
            router, state.profile, body.question, body.answer
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"澄清答案并入档案失败：{exc}") from exc
    state.profile = profile
    state.interview_history.append([body.question, body.answer])
    store.save(state)
    remaining = profile.open_questions
    return {
        "profile": profile.model_dump(exclude_none=True),
        "question": remaining[0] if remaining else None,
        "remaining": len(remaining),
        "done": not remaining,
    }


# ── 环节③④：计划 + GitHub 项目 ──────────────────────────────────

class PlanInput(BaseModel):
    min_stars: int = 50
    llm_rerank: bool = True


@app.post("/api/sessions/{session_id}/plan")
def build_plan(session_id: str, body: PlanInput) -> dict:
    state = _session(session_id)
    if state.jd is None:
        raise HTTPException(status_code=400, detail="请先识别岗位 JD")
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    router = get_router()
    try:
        plan, repos = plan_and_recommend(
            router,
            state.jd,
            state.profile,
            min_stars=body.min_stars,
            llm_rerank=body.llm_rerank,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成计划失败：{exc}") from exc
    state.plan = plan
    state.repos = repos
    store.save(state)
    return {"plan": plan.model_dump(exclude_none=True), "repos": [r.model_dump() for r in repos]}


# ── 环节⑤：导师指导（按计划继续做项目）──────────────────────────

COACH_START_PROMPT = """你是「校招雷达」的项目导师。用户要从零复现一个 GitHub 项目来补齐自己的岗位差距。

用户岗位方向：{direction}
用户差距分析：
{gaps}
用户分阶段计划：
{phases}
用户技能档案：{profile}
选定项目：{repo}（{language}，⭐{stars}）
项目简介：{desc}
推荐理由：{reason}

请输出一份【复现行动路线】，包含：
1. 这个项目主要补用户的哪些差距（对应上面差距分析里的条目）；
2. 3~6 步可执行的复现路线：每步写清楚做什么、怎么做、怎么验证做对了、预计投入；
3. 结合用户当前水平，指出最可能卡住的地方和提前准备。

用中文，markdown 排版，控制在 700 字以内，先给结论再展开。"""

COACH_CHAT_PROMPT = """你是「校招雷达」的项目导师。用户正按学习计划做项目，你要持续指导他。

背景材料：
岗位方向：{direction}
分阶段计划：
{phases}
差距分析：
{gaps}
可复现项目列表：
{repos}
用户技能档案：
{profile}

回答要求：
1. 结合具体阶段、具体项目给出可执行建议，不要泛泛而谈；
2. 用户卡住或报错时，先引导他给出关键信息（报错文本、代码片段、进度），再逐步排查；
3. 涉及下一步动作时给明确的最小步骤（做什么 → 怎么验证）；
4. 每次回答不超过 300 字，可用 markdown 排版。"""


def _plan_ctx(state) -> dict:
    plan = state.plan
    gaps = "\n".join(
        f"- [{g.status}] {g.requirement}" + (f"（你有：{g.matched_skill}）" if g.matched_skill else "")
        for g in (plan.gaps if plan else [])
    ) or "（暂无差距数据）"
    phases = "\n".join(
        f"- {p.phase}：{p.goal}" + (f"，主题：{', '.join(p.topics)}" if p.topics else "")
        for p in (plan.phases if plan else [])
    ) or "（暂无计划数据）"
    repos = "\n".join(
        f"- {r.full_name}（{r.language or '未知'}，⭐{r.stargazers_count}）{r.description[:100]}"
        for r in state.repos
    ) or "（暂无项目）"
    profile = state.profile
    profile_text = (
        profile.summary
        + "\n技能：" + "、".join(f"{s.name}({s.proficiency})" for s in profile.skills)
        if profile
        else "（暂无技能档案）"
    )
    return {
        "direction": plan.direction if plan else (state.jd.job_title if state.jd else "目标岗位"),
        "gaps": gaps,
        "phases": phases,
        "repos": repos,
        "profile": profile_text,
    }


class CoachStartInput(BaseModel):
    project_full_name: str


@app.post("/api/sessions/{session_id}/coach/start")
def coach_start(session_id: str, body: CoachStartInput) -> dict:
    state = _session(session_id)
    if state.plan is None:
        raise HTTPException(status_code=400, detail="请先生成学习计划，再让 AI 带做项目")
    repo = next((r for r in state.repos if r.full_name == body.project_full_name), None)
    if repo is None:
        raise HTTPException(status_code=400, detail="该项目不在当前会话的项目列表里，请先重新生成")
    router = get_router()
    ctx = _plan_ctx(state)
    try:
        resp = router.complete(
            "coach",
            [
                Message(
                    role="system",
                    content=COACH_START_PROMPT.format(
                        direction=ctx["direction"],
                        gaps=ctx["gaps"],
                        phases=ctx["phases"],
                        profile=ctx["profile"],
                        repo=repo.full_name,
                        language=repo.language or "未知",
                        stars=repo.stargazers_count,
                        desc=repo.description or "（无简介）",
                        reason=repo.reason or "（无推荐理由）",
                    ),
                ),
                Message(role="user", content=f"我要跟着 {repo.full_name} 做这个项目，给我一份复现路线。"),
            ],
            temperature=0.5,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成复现路线失败：{exc}") from exc
    text = resp.text.strip()
    state.coach_history.append([f"我要跟着 {repo.full_name} 做这个项目", text])
    store.save(state)
    return {"message": text, "history": state.coach_history}


class CoachChatInput(BaseModel):
    message: str


@app.post("/api/sessions/{session_id}/coach")
def coach_chat(session_id: str, body: CoachChatInput) -> dict:
    state = _session(session_id)
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if state.plan is None:
        raise HTTPException(status_code=400, detail="请先生成学习计划")
    router = get_router()
    ctx = _plan_ctx(state)
    messages = [
        Message(
            role="system",
            content=COACH_CHAT_PROMPT.format(
                direction=ctx["direction"],
                gaps=ctx["gaps"],
                phases=ctx["phases"],
                repos=ctx["repos"],
                profile=ctx["profile"],
            ),
        )
    ]
    # 最近 6 条对话历史
    for user_turn, asst_turn in state.coach_history[-6:]:
        messages.append(Message(role="user", content=user_turn))
        messages.append(Message(role="assistant", content=asst_turn))
    messages.append(Message(role="user", content=msg))
    try:
        resp = router.complete("coach", messages, temperature=0.5, max_tokens=2048)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"导师回复失败：{exc}") from exc
    text = resp.text.strip()
    state.coach_history.append([msg, text])
    store.save(state)
    return {"message": text, "history": state.coach_history}


# ── 环节⑥：实操 Agent（能操作电脑，按需确认）───────────────────

AGENT_MAX_STEPS = 25  # 每轮 turn/confirm 内最多执行的 LLM 决策步数


def _agent_runtime() -> AgentRuntime:
    return AgentRuntime(paths.workspace_dir())


def _agent_system_prompt(state) -> str:
    """组装 Agent 系统提示词：工具规则 + 当前学习计划上下文（差距/阶段/项目/档案）。"""
    ctx = _plan_ctx(state)
    plan_lines = [
        f"岗位方向：{ctx['direction']}",
        "",
        "差距分析（要补的技能点）：",
        ctx["gaps"],
        "",
        "分阶段计划（按这个顺序推进）：",
        ctx["phases"],
        "",
        "可复现项目（用于实践验证）：",
        ctx["repos"],
        "",
        "用户技能档案：",
        ctx["profile"],
    ]
    return AGENT_SYSTEM_PROMPT.format(
        workspace=str(paths.workspace_dir()),
        plan_ctx="\n".join(plan_lines),
    )


def _agent_thread_messages(state) -> list[Message]:
    """把 agent_thread（内部格式 dict）转成内部 Message。"""
    out: list[Message] = []
    for m in state.agent_thread or []:
        role = m.get("role")
        content = m.get("content") or ""
        tc = m.get("tool_calls")
        tcid = m.get("tool_call_id")
        if role == "tool":
            out.append(Message(role="tool", content=content, tool_call_id=tcid))
        elif role == "assistant" and tc:
            from ..core.schema import ToolCall

            tool_calls = [
                ToolCall(
                    id=t["id"],
                    name=t["name"],
                    arguments=t.get("arguments") or {},
                )
                for t in tc
            ]
            out.append(Message(role="assistant", content=content, tool_calls=tool_calls))
        elif role == "user":
            out.append(Message(role="user", content=content))
        elif role == "system":
            out.append(Message(role="system", content=content))
    return out


def _agent_history_for_ui(state) -> list[dict]:
    """前端展示用：只提取 user/assistant 文本对。"""
    history = []
    for m in state.agent_thread or []:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content and not m.get("tool_calls"):
            history.append({"role": role, "content": content})
    return history


# ── Agent 时间线日志（Codex 式展示的历史操作）───────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _agent_log(state, **fields) -> None:
    entry = {"ts": _now_iso()}
    entry.update(fields)
    state.agent_logs.append(entry)
    if len(state.agent_logs) > 300:
        state.agent_logs = state.agent_logs[-300:]


def _describe_call(name: str, args: dict) -> str:
    """工具执行前的可读描述（用于实时显示"正在做什么"）。"""
    args = args or {}
    if name == "list_dir":
        return f"列出目录 {args.get('path', '.')}"
    if name == "read_file":
        return f"读取文件 {args.get('path', '')}"
    if name == "write_file":
        content = args.get("content", "")
        return f"写入文件 {args.get('path', '')}（{len(content)} 字符）"
    if name == "run_command":
        return f"执行命令：{args.get('command', '')[:120]}"
    if name == "git_clone":
        return f"git clone {args.get('url', '')}"
    return f"调用 {name}"


# ── SSE 流式 agent 循环 ─────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _agent_stream(state, rt: AgentRuntime, router: ModelRouter) -> Iterator[str]:
    """跑 agent 循环，逐步 yield SSE 事件（Codex 式实时可见）。"""
    steps = 0
    while steps < AGENT_MAX_STEPS:
        steps += 1
        yield _sse("thinking", {})
        try:
            resp = router.complete(
                "agent",
                _agent_thread_messages(state),
                tools=AGENT_TOOLS,
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"detail": f"模型调用失败：{exc}"})
            return

        if not resp.tool_calls:
            text = resp.text or "(无回复)"
            state.agent_thread.append({"role": "assistant", "content": text})
            _agent_log(state, type="message", role="assistant", content=text)
            store.save(state)
            yield _sse("message", {"role": "assistant", "content": text})
            yield _sse("done", {"history": _agent_history_for_ui(state)})
            return

        asst_msg: dict = {
            "role": "assistant",
            "content": resp.text or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in resp.tool_calls
            ],
        }
        state.agent_thread.append(asst_msg)

        # 模型"带话执行"（如解释为何要做这一步）：先把话推给用户再执行工具
        if resp.text and resp.text.strip():
            _agent_log(
                state, type="message", role="assistant", content=resp.text.strip()
            )
            yield _sse("message", {"role": "assistant", "content": resp.text.strip()})

        for tc in resp.tool_calls:
            summary = _describe_call(tc.name, tc.arguments)
            yield _sse("tool_start", {"kind": tc.name, "summary": summary})
            try:
                result = rt.run_tool(tc.name, tc.arguments, tc.id)
            except Exception as exc:  # noqa: BLE001 - 工具失败转给模型
                result = f"工具调用失败：{exc}"
                state.agent_thread.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
                _agent_log(
                    state, type="tool", kind=tc.name, summary=summary, output="", error=str(exc)
                )
                store.save(state)
                yield _sse("tool", {"kind": tc.name, "summary": f"执行出错：{exc}", "output": ""})
                continue
            if isinstance(result, PendingAction):
                state.agent_pending = {
                    "action_id": result.action_id,
                    "kind": result.kind,
                    "summary": result.summary,
                    "tool_call_id": result.tool_call_id,
                    "payload": result.payload,
                }
                _agent_log(
                    state, type="pending", kind=result.kind, summary=result.summary, output=""
                )
                store.save(state)
                yield _sse("pending", {"pending": state.agent_pending})
                return
            state.agent_thread.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
            _agent_log(state, type="tool", kind=tc.name, summary=summary, output=result)
            store.save(state)
            yield _sse("tool", {"kind": tc.name, "summary": result[:200], "output": result})

    # 步数保护：强制结束本轮
    state.agent_pending = None
    store.save(state)
    yield _sse(
        "message",
        {
            "role": "assistant",
            "content": f"（本轮已达到 {AGENT_MAX_STEPS} 步上限，先停下来让你确认进度；可以继续发消息让我推进）",
        },
    )
    yield _sse("done", {"history": _agent_history_for_ui(state)})


def _sse_response(gen: Iterator[str]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class AgentTurnInput(BaseModel):
    message: str


@app.post("/api/sessions/{session_id}/agent/turn")
def agent_turn(session_id: str, body: AgentTurnInput) -> StreamingResponse:
    state = _session(session_id)
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if state.plan is None:
        raise HTTPException(status_code=400, detail="请先生成学习计划，再让 Agent 带做项目")
    if state.agent_pending is not None:
        raise HTTPException(status_code=409, detail="有一个操作正等你确认，请先处理再继续")

    rt = _agent_runtime()
    if state.agent_thread is None:
        state.agent_thread = [{"role": "system", "content": _agent_system_prompt(state)}]
    else:
        # 计划可能更新过：每次发言前刷新 system 上下文（保留工具规则与最新计划）
        state.agent_thread[0] = {"role": "system", "content": _agent_system_prompt(state)}
    # 本轮提醒：把模型注意力钉在计划上（先给安排，再动手）
    reminder = (
        "【本轮要求】先根据上面的学习计划，用中文告诉用户：这轮从哪个技能点开始、"
        "计划做什么（对应差距/阶段）、预期结果；然后再执行工具。"
    )
    state.agent_thread[0]["content"] = state.agent_thread[0]["content"] + "\n\n" + reminder
    state.agent_thread.append({"role": "user", "content": msg})
    _agent_log(state, type="message", role="user", content=msg)
    store.save(state)
    return _sse_response(_agent_stream(state, rt, get_router()))


class AgentConfirmInput(BaseModel):
    action_id: str
    approve: bool


@app.post("/api/sessions/{session_id}/agent/confirm")
def agent_confirm(session_id: str, body: AgentConfirmInput) -> StreamingResponse:
    state = _session(session_id)
    pending = state.agent_pending
    if pending is None or pending.get("action_id") != body.action_id:
        raise HTTPException(status_code=409, detail="没有待确认的操作，或已过期")

    rt = _agent_runtime()
    tool_call_id = pending.get("tool_call_id", "")
    summary = pending.get("summary", "")
    if body.approve:
        action = PendingAction(
            action_id=pending["action_id"],
            kind=pending["kind"],
            summary=summary,
            tool_call_id=tool_call_id,
            payload=pending.get("payload", {}),
        )
        try:
            result = rt.execute(action)
        except Exception as exc:  # noqa: BLE001
            result = f"执行失败：{exc}"
        _agent_log(state, type="tool", kind=pending["kind"], summary=summary, output=result)
    else:
        result = "用户拒绝了这个操作。请改用不需要该操作的方式继续，或先向用户说明为什么需要它。"
        _agent_log(state, type="message", role="system", content=result)

    state.agent_thread.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
    state.agent_pending = None
    store.save(state)

    def gen() -> Iterator[str]:
        if body.approve:
            yield _sse("tool_start", {"kind": pending["kind"], "summary": summary})
            yield _sse("tool", {"kind": pending["kind"], "summary": result[:200], "output": result})
        else:
            yield _sse("message", {"role": "system", "content": result})
        yield from _agent_stream(state, rt, get_router())

    return _sse_response(gen())


@app.post("/api/sessions/{session_id}/agent/reset")
def agent_reset(session_id: str) -> dict:
    """清空 Agent 对话与待确认状态（工作区文件不受影响）。"""
    state = _session(session_id)
    state.agent_thread = None
    state.agent_pending = None
    store.save(state)
    return {"ok": True}


# ── 前端静态托管（窗口直接加载 http://127.0.0.1:17689/）──────────────────
# 绕过 Tauri 内嵌资源协议（tauri.localhost 在部分代理/TUN 环境下被拦截），
# 由本服务同源提供前端页面 + API，127.0.0.1 回环直连不受代理影响。
if paths.dist_dir().is_dir():
    app.mount("/", StaticFiles(directory=paths.dist_dir(), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    def _frontend_missing() -> dict:
        return {
            "error": "前端构建产物缺失",
            "detail": f"未找到 {paths.dist_dir()}，请先运行 npm run build 或补全分发文件",
        }


# ── 运行入口 ─────────────────────────────────────────────────────

def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
