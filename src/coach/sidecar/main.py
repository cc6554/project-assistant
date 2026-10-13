"""Tauri 桌面端 sidecar：FastAPI 本地服务（127.0.0.1:17689）。

把 coach 内核的确定性工具 + LLM 路由暴露为 HTTP API：
  会话管理 / JD 截图解析 / 技能档案 / 访谈 / 计划与 GitHub 项目 / 导师指导 / 模型配置。
仅监听回环地址，供本机 Tauri 窗口调用。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from threading import RLock
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml
from dotenv import load_dotenv, set_key
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.llm.registry import VALID_API_MODES, load_config
from ..core.llm.router import ModelRouter
from ..core.schema import Message
from ..domain.schemas import JDCard
from ..tools import job_sources, documents, github_search, interviewer, jd_parser, planner, profile_builder, web_search
from ..tools.agent import AGENT_SYSTEM_PROMPT, AGENT_TOOLS, AgentRuntime, PendingAction
from ..tools.pipeline import plan_and_recommend
from . import paths
from .store import SessionNotFound, SessionStore, SessionConflict
from .auth import API_TOKEN, INSTANCE_ID, ORIGINS, LocalAuthMiddleware

PORT = 17689

app = FastAPI(title="job-radar-coach sidecar", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ORIGINS),  # 仅回环监听；Tauri webview 来源 http://127.0.0.1:17689
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheMiddleware(BaseHTTPMiddleware):
    """所有响应禁用缓存：前端每次构建后，窗口/浏览器立即拿到最新版，避免旧缓存。"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


app.add_middleware(NoCacheMiddleware)
app.add_middleware(LocalAuthMiddleware)

@app.exception_handler(SessionConflict)
async def session_conflict(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)

@app.exception_handler(ValueError)
async def invalid_input(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)

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
        state = store.get(session_id)
        if state.agent_running and (state.agent_execution or {}).get("instance_id") != INSTANCE_ID:
            with store.lock(session_id):
                state = store.get(session_id)
                state.agent_running = False
                state.agent_execution = {**(state.agent_execution or {}), "status": "interrupted"}
                if state.agent_pending is None:
                    _complete_missing_tools(state)
                _agent_log(state, type="message", role="system", content="上次运行中断。操作结果可能不完整，请先检查实际文件和进程；不会自动重执行已领取的审批。")
                store.save(state)
        return state
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc


# ── 基础 ─────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"ok": True, "port": PORT, "instance_id": INSTANCE_ID}


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
            "extra_body": spec.get("extra_body") or {},
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


class ModelSelectInput(BaseModel):
    provider: str | None = None
    model: str | None = None


class GithubTokenInput(BaseModel):
    token: str


@app.get("/api/settings/github-token")
def get_github_token() -> dict:
    """返回 GitHub Token 是否已配置（只回掩码，不回明文）。"""
    p = paths.github_token_path()
    token = ""
    if p.exists():
        try:
            token = (json.loads(p.read_text(encoding="utf-8")) or {}).get("token", "") or ""
        except Exception:  # noqa: BLE001
            token = ""
    return {"configured": bool(token), "masked": "********" if token else ""}


@app.put("/api/settings/github-token")
def put_github_token(body: GithubTokenInput) -> dict:
    """保存用户显式输入的 GitHub Token（本地 config/github_token.json，不入 git）。空串=清除。"""
    token = (body.token or "").strip()
    p = paths.github_token_path()
    if token:
        _atomic_write(p, json.dumps({"token": token}, ensure_ascii=False, indent=2).encode("utf-8"))
    else:
        p.unlink(missing_ok=True)
    return {"configured": bool(token), "masked": "********" if token else ""}


@app.get("/api/model")
def get_model() -> dict:
    """全局当前模型 + 可选模型列表（来自 providers.yaml 的任务链，与配置的 key 同步）。"""
    raw = yaml.safe_load(paths.config_path().read_text(encoding="utf-8")) or {}
    options: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for chain in (raw.get("tasks") or {}).values():
        for item in chain:
            key = (item.get("provider"), item["model"])
            if key not in seen:
                seen.add(key)
                options.append({"provider": key[0], "model": item["model"]})
    current = None
    cur_path = paths.config_path().parent / "current_model.json"
    if cur_path.exists():
        try:
            cur = json.loads(cur_path.read_text(encoding="utf-8")) or {}
            if any(o == {"provider": cur.get("provider"), "model": cur.get("model")} for o in options):
                current = {"provider": cur["provider"], "model": cur["model"]}
        except Exception:  # noqa: BLE001
            current = None
    return {"options": options, "current": current}


@app.put("/api/model")
def put_model(body: ModelSelectInput) -> dict:
    """设置全局当前模型（持久化到 config/current_model.json，所有对话立即生效）。"""
    cur_path = paths.config_path().parent / "current_model.json"
    if not body.provider and not body.model:
        cur_path.unlink(missing_ok=True)
        return {"ok": True, "current": None}
    current = {"provider": body.provider, "model": body.model}
    if current not in get_model()["options"]:
        raise HTTPException(status_code=400, detail="所选模型不在当前任务配置中")
    _atomic_write(cur_path, json.dumps(current).encode("utf-8"))
    return {"ok": True, "current": current}


class ProviderInput(BaseModel):
    name: str
    api_mode: str = "chat_completions"
    base_url: str
    api_key_env: str | None = None
    api_key: str | None = None  # 提供则写入 .env
    timeout: float = 120.0
    extra_body: dict | None = None


class ConfigInput(BaseModel):
    providers: list[ProviderInput]
    tasks: dict[str, list[dict[str, str]]]


_config_lock = RLock()


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@app.put("/api/config")
def put_config(cfg: ConfigInput) -> dict:
    from urllib.parse import urlsplit
    with _config_lock:
        if not cfg.providers or len({p.name for p in cfg.providers}) != len(cfg.providers):
            raise HTTPException(status_code=400, detail="Provider 不能为空或重名")
        config_file, env_file = paths.config_path(), paths.env_path()
        old_config = config_file.read_bytes() if config_file.exists() else None
        old_env = env_file.read_bytes() if env_file.exists() else None
        raw = yaml.safe_load(old_config or b"") or {}
        providers = {}
        for provider in cfg.providers:
            if not provider.name.strip() or provider.api_mode not in VALID_API_MODES or provider.timeout <= 0:
                raise HTTPException(status_code=400, detail="Provider 名称、模式或超时无效")
            parsed = urlsplit(provider.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise HTTPException(status_code=400, detail="Endpoint 必须是 HTTP(S) 地址")
            if provider.api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", provider.api_key_env):
                raise HTTPException(status_code=400, detail="密钥环境变量名称无效")
            if provider.api_key and not provider.api_key_env:
                raise HTTPException(status_code=400, detail="填写密钥时必须指定环境变量名称")
            entry = dict((raw.get("providers") or {}).get(provider.name, {}))
            entry.update(api_mode=provider.api_mode, base_url=provider.base_url, timeout=provider.timeout)
            if provider.api_key_env:
                entry["api_key_env"] = provider.api_key_env
            else:
                entry.pop("api_key_env", None)
            if provider.extra_body is not None:
                entry["extra_body"] = provider.extra_body
            providers[provider.name] = entry
        if not cfg.tasks.get("default"):
            raise HTTPException(status_code=400, detail="default 任务必须有有效模型")
        for chain in cfg.tasks.values():
            if not chain or any(t.get("provider") not in providers or not t.get("model", "").strip() for t in chain):
                raise HTTPException(status_code=400, detail="任务链为空或引用了无效模型/Provider")
        raw.update(providers=providers, tasks=cfg.tasks)
        temporary = config_file.with_name(f".validate-{uuid.uuid4().hex}.yaml")
        env_temporary = env_file.with_name(f".env.{uuid.uuid4().hex}.tmp")
        keys = {provider.api_key_env: provider.api_key for provider in cfg.providers if provider.api_key}
        committed = False
        try:
            temporary.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
            load_config(temporary)
            if keys:
                env_temporary.write_bytes(old_env or b"")
                for name, key in keys.items():
                    set_key(str(env_temporary), name, key)
            temporary.replace(config_file)
            committed = True
            if keys:
                env_temporary.replace(env_file)
            os.environ.update(keys)
        except Exception as exc:
            if committed:
                if old_config is None:
                    config_file.unlink(missing_ok=True)
                else:
                    _atomic_write(config_file, old_config)
            raise HTTPException(status_code=400, detail=f"配置保存失败，旧配置保留：{exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
            env_temporary.unlink(missing_ok=True)
        return {"ok": True}


# ── 会话管理 ─────────────────────────────────────────────────────

@app.get("/api/sessions")
def list_sessions() -> list[dict]:
    return store.list_sessions()


class CreateSessionInput(BaseModel):
    source_session_id: str | None = None


@app.post("/api/sessions")
def create_session(body: CreateSessionInput | None = None) -> dict:
    """新建岗位会话；可选从已有会话复制技能档案（profile 深拷贝）。"""
    state = store.create()
    if body and body.source_session_id:
        try:
            src = _session(body.source_session_id)
        except HTTPException:
            raise HTTPException(status_code=404, detail="源会话不存在")
        if src.profile is not None:
            state.profile = src.profile.model_copy(deep=True)
            store.save(state)
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
    contents: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        contents.append((f.filename or "screenshot.png", data))
    if not contents:
        raise HTTPException(status_code=400, detail="上传文件均为空")
    paths_ = store.save_uploads(session_id, contents)

    router = get_router()
    try:
        jd = jd_parser.parse_jd_screenshots(router, paths_)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"JD 识别失败：{exc}") from exc

    state.jd = jd
    state.jd_sources = []
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
    platform: str = "boss"


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
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    last_err: Exception | None = None
    for q in queries:
        try:
            for source in job_sources.search(q, platform=body.platform, n=6):
                line = source["title"] + "：" + source["text"] + "\n来源：" + source["url"]
                key = source["url"]
                if key not in seen:
                    seen.add(key)
                    texts.append(line)
                    sources.append(source)
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
    state.jd_sources = sources
    store.save(state)
    return {"jd": jd.model_dump(exclude_none=True), "sources": len(texts), "source_details": sources}


class JdImportInput(BaseModel):
    url: str
    text: str = ""


@app.post("/api/sessions/{session_id}/jd/import")
def import_jd(session_id: str, body: JdImportInput) -> dict:
    state = _session(session_id)
    try:
        source = job_sources.import_text(body.url, body.text)
        jd = jd_parser.parse_jd_text(get_router(), [source["text"]], task="parsing")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"岗位导入失败：{exc}") from exc
    state.jd = jd
    state.jd_sources = [source]
    store.save(state)
    return {"jd": jd.model_dump(exclude_none=True), "sources": 1, "source_details": [source]}


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
    """上传任意类型文件：可解析的并入档案；不支持的格式跳过并提示，不中断整批。"""
    state = _session(session_id)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")
    contents: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if data:
            contents.append((f.filename or "doc.bin", data))
    paths_ = store.save_uploads(session_id, contents)

    # 逐文件预检：可读的进解析列表，不可读的进 failed
    readable: list[str] = []
    failed: list[str] = []
    for p in paths_:
        try:
            if not documents.read_document(p).strip():
                raise ValueError("文件为空或没有可提取文本")
            readable.append(p)
        except Exception as exc:  # noqa: BLE001 - 未知类型/损坏文件跳过
            failed.append(f"{Path(p).name}: {exc}")

    if not readable:
        raise HTTPException(status_code=400, detail={"message": "没有可解析文件", "failed_files": failed})
    profile = state.profile
    if readable:
        router = get_router()
        try:
            profile = profile_builder.build_profile_from_documents(
                router, readable, source=source, existing=state.profile
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"文件解析失败：{exc}") from exc
    if profile is None:
        raise HTTPException(status_code=400, detail={"message": "所有文件均未成功解析", "failed_files": failed})
    state.profile = profile
    store.save(state)
    return {**profile.model_dump(exclude_none=True), "failed_files": failed}


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
    extra = _auto_fetch_context(body.answer)
    answer_text = body.answer
    if extra:
        answer_text += f"\n\n（系统已自动抓取你提到的内容：\n{extra}）"
    try:
        profile = interviewer.apply_interview_answer(
            router, state.profile, body.question, answer_text
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
    read_names: list[str] = []
    for p in selected:
        try:
            if AgentRuntime._is_sensitive(p):
                raise ValueError("敏感文件已跳过")
            text = p.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                raise ValueError("文件为空")
            profile = profile_builder.build_profile_from_text(
                router, text, source="obsidian", existing=profile
            )
            read_names.append(str(p.relative_to(vault)))
        except Exception as exc:  # noqa: BLE001 - 单篇失败跳过，不让整批挂掉
            failed.append(f"{p.name}: {exc}")
            continue
    if profile is None or not read_names:
        raise HTTPException(status_code=400, detail={"message": "所有文件均未成功解析", "failed_files": failed})
    state.profile = profile
    store.save(state)
    return {
        **profile.model_dump(exclude_none=True),
        "read_files": read_names,
        "failed_files": failed,
    }


# ── 环节②c2：通用本地路径读取（任意目录，agent 自己读路径下文件）──────

class LocalPathInput(BaseModel):
    path: str
    max_files: int | None = None  # None = 不限制


_SUPPORTED_DOC_SUFFIXES = documents.SUPPORTED_EXTENSIONS


@app.post("/api/sessions/{session_id}/profile/path")
def profile_from_path(session_id: str, body: LocalPathInput) -> dict:
    """读取本地路径下全部支持的文件（纯文本/JSON/CSV/PDF/Word/Excel/PPT），逐份并入技能档案。"""
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
        raise HTTPException(status_code=400, detail=f"该路径下没有找到支持的文件（纯文本/JSON/CSV/PDF/Word/Excel/PPT）：{root}")
    if body.max_files and body.max_files > 0:
        files = files[: body.max_files]

    router = get_router()
    profile = state.profile
    failed: list[str] = []
    read_names: list[str] = []
    for p in files:
        try:
            if AgentRuntime._is_sensitive(p):
                raise ValueError("敏感文件已跳过")
            text = documents.read_document(p)
            if not text.strip():
                raise ValueError("文件没有可提取文本")
            profile = profile_builder.build_profile_from_text(
                router, text, source="local_path", existing=profile
            )
            read_names.append(str(p.relative_to(root)) if root.is_dir() else p.name)
        except Exception as exc:  # noqa: BLE001 - 单文件失败跳过
            failed.append(f"{p.name}: {exc}")
            continue
    if profile is None or not read_names:
        raise HTTPException(status_code=400, detail={"message": "所有文件均未成功解析", "failed_files": failed})
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


class ClarifyChatInput(BaseModel):
    message: str
    history: list[dict] = []


@app.get("/api/sessions/{session_id}/profile/clarify/start")
def clarify_start(session_id: str) -> dict:
    """打开澄清对话：返回完整提纲 + AI 自然开场白（把第一条问题口语化问出）。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    questions = state.profile.open_questions
    if not questions:
        return {"outline": [], "opening": "没有待澄清问题了。", "remaining": 0, "done": True}
    # 打开时自动清理提纲里的同主题重复变体（保留首次出现），存量污染也能自愈
    deduped: list[str] = []
    for q in questions:
        if any(profile_builder.question_similar(q, e) >= 0.6 for e in deduped):
            continue
        deduped.append(q)
    if len(deduped) != len(questions):
        state.profile.open_questions = deduped
        store.save(state)
        questions = deduped
    router = get_router()
    try:
        opening = interviewer.clarify_opening(router, questions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"澄清开场失败：{exc}") from exc
    return {
        "outline": questions,
        "opening": opening,
        "remaining": len(questions),
        "done": False,
        "profile": state.profile.model_dump(exclude_none=True),
    }


class ClarifyRemoveInput(BaseModel):
    question: str


@app.post("/api/sessions/{session_id}/profile/clarify/remove")
def clarify_remove(session_id: str, body: ClarifyRemoveInput) -> dict:
    """删除一条待澄清问题（针对误生成/用户没问过的问题）。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    q = body.question.strip()
    if not q:
        raise HTTPException(status_code=400, detail="question 不能为空")
    state.profile.open_questions = [
        x for x in state.profile.open_questions if x.strip() != q
    ]
    store.save(state)
    return {"profile": state.profile.model_dump(exclude_none=True)}


@app.post("/api/sessions/{session_id}/profile/clarify/chat")
def clarify_chat(session_id: str, body: ClarifyChatInput) -> dict:
    """自由对话式待澄清：用户可回答当前问题，也可随时追问「什么意思」、
    解释、跳过或补充背景；仅 answer/skip 会消耗并更新档案。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    profile = state.profile
    questions = profile.open_questions
    if not questions:
        return {
            "kind": "done",
            "reply": "没有待澄清问题了。",
            "remaining": 0,
            "done": True,
            "reason": "exhausted",
            "profile": profile.model_dump(exclude_none=True),
        }

    router = get_router()
    # 自动预取：用户消息里带链接/本地路径时，系统先抓取/读取并注入上下文，
    # 让澄清对话的 AI 也能"看到"GitHub 页面或本地文件（无需工具循环）
    extra_context = _auto_fetch_context(body.message)
    try:
        decision = interviewer.clarify_turn(
            router,
            profile,
            questions,
            body.message,
            body.history,
            answered=[q for q, _ in state.interview_history],
            extra_context=extra_context,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"澄清对话失败：{exc}") from exc

    if decision.intent in ("answer", "skip"):
        # 用户回应的目标问题：target_index 有效则按序号，否则默认第一条
        idx = decision.target_index if 0 <= decision.target_index < len(questions) else 0
        target = questions[idx]
        if decision.intent == "answer":
            try:
                _answer_text = body.message
                if extra_context:
                    _answer_text += f"\n\n（系统已自动抓取你提到的内容：\n{extra_context}）"
                profile = interviewer.apply_interview_answer(
                    router, profile, target, _answer_text
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"答案并入档案失败：{exc}") from exc
        else:
            # 明确跳过：移除目标问题
            profile.open_questions = [
                q for q in profile.open_questions if q.strip() != target.strip()
            ]
        state.profile = profile
        state.interview_history.append([target, body.message])
        store.save(state)
        remaining = profile.open_questions
        return {
            "kind": decision.intent,
            "reply": decision.reply,
            "remaining": len(remaining),
            "done": not remaining,
            "reason": "exhausted" if not remaining else "progress",
            "profile": profile.model_dump(exclude_none=True),
        }

    # explain / other：不消耗问题，仅回话
    return {
        "kind": decision.intent,
        "reply": decision.reply,
        "remaining": len(questions),
        "done": False,
        "profile": None,
    }


# ── 环节③④：计划 + GitHub 项目 ──────────────────────────────────

class PlanInput(BaseModel):
    min_stars: int = 50
    llm_rerank: bool = True
    include: list[str] | None = None
    exclude: list[str] | None = None


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
            preferences={"include": body.include if body.include is not None else state.repository_preferences.get("include", []), "exclude": body.exclude if body.exclude is not None else state.repository_preferences.get("exclude", [])},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生成计划失败：{exc}") from exc
    state.plan = plan
    state.repository_preferences = {"include": body.include if body.include is not None else state.repository_preferences.get("include", []), "exclude": body.exclude if body.exclude is not None else state.repository_preferences.get("exclude", [])}
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
    # 完整对话历史
    for user_turn, asst_turn in state.coach_history:
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


_URL_RE = re.compile(r"https?://[^\s，。；、]+", re.IGNORECASE)


def _auto_fetch_context(message: str) -> str:
    """从用户消息里提取链接/本地路径，自动抓取/读取，返回可注入 LLM 上下文的转述文本。

    只做只读操作（fetch_url / read_file / list_dir），不产生副作用。
    """
    from pathlib import Path

    import time as _time

    rt = _agent_runtime()
    parts: list[str] = []
    _deadline = _time.monotonic() + 15.0  # 总预算 15 秒，超时跳过剩余链接，不卡对话

    urls = _URL_RE.findall(message)
    seen_urls: set[str] = set()
    for u in urls:
        if _time.monotonic() > _deadline:
            parts.append("（其余链接抓取超时，已跳过）")
            break
        u = u.rstrip(".,;，。；)")
        if u in seen_urls:
            continue
        seen_urls.add(u)
        fetched = rt.fetch_url(u, 4000)
        if not fetched.startswith("（"):
            parts.append(f"链接 {u} 的内容：\n{fetched}")
        else:
            parts.append(f"链接 {u}：{fetched}")

    for tok in re.findall(
        r"(?:[A-Za-z]:\\[^\s，。；、]+|/(?:home|Users|data|tmp)/[^\s，。；、]+)", message
    ):
        p = Path(tok.rstrip(".,;，。；)"))
        if p.is_file():
            try:
                parts.append(f"本地文件 {tok} 的内容：\n{rt.read_file(str(p))}")
            except Exception as exc:  # noqa: BLE001
                parts.append(f"本地文件 {tok} 读取失败：{exc}")
        elif p.is_dir():
            parts.append(f"本地目录 {tok} 的内容：\n{rt.list_dir(str(p))}")

    return "\n\n".join(parts)


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
        elif role in ("user", "assistant"):
            out.append(Message(role=role, content=content))
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
        return f"执行命令：{args.get('command', '')}"
    if name == "git_clone":
        return f"git clone {args.get('url', '')}"
    return f"调用 {name}"


# ── SSE 流式 agent 循环 ─────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _complete_missing_tools(state) -> None:
    """Repair interrupted legacy batches without inventing successful execution."""
    thread = state.agent_thread or []
    repaired = []
    waiting = {}
    for message in thread:
        if message.get("role") != "tool" and waiting:
            for call_id in waiting:
                repaired.append({"role": "tool", "tool_call_id": call_id, "content": "此前工具批次中断，未取得结果。请检查实际状态，不能假定已执行。"})
            waiting = {}
        repaired.append(message)
        if message.get("tool_calls"):
            waiting = {call["id"]: call for call in message["tool_calls"]}
        if message.get("role") == "tool":
            waiting.pop(message.get("tool_call_id"), None)
    for call_id in waiting:
        repaired.append({"role": "tool", "tool_call_id": call_id, "content": "此前工具批次中断，未取得结果。请检查实际状态。"})
    state.agent_thread = repaired
    state.agent_queue = []


def _agent_stream(state, rt: AgentRuntime, router: ModelRouter) -> Iterator[str]:
    steps = 0
    while steps < AGENT_MAX_STEPS:
        while state.agent_queue:
            call = state.agent_queue.pop(0)
            summary = _describe_call(call["name"], call.get("arguments", {}))
            yield _sse("tool_start", {"kind": call["name"], "summary": summary})
            try:
                result = rt.run_tool(call["name"], call.get("arguments", {}), call["id"])
            except Exception as exc:
                result = f"工具调用失败：{exc}"
            if isinstance(result, PendingAction):
                state.agent_pending = {"action_id": result.action_id, "kind": result.kind,
                    "summary": result.summary, "tool_call_id": result.tool_call_id, "payload": result.payload}
                _agent_log(state, type="pending", kind=result.kind, summary=result.summary)
                store.save(state)
                yield _sse("pending", {"pending": state.agent_pending})
                return
            state.agent_thread.append({"role": "tool", "tool_call_id": call["id"], "content": str(result)})
            _agent_log(state, type="tool", kind=call["name"], summary=summary, output=str(result))
            store.save(state)
            yield _sse("tool", {"kind": call["name"], "summary": summary, "output": str(result)})
        steps += 1
        yield _sse("thinking", {})
        _complete_missing_tools(state)
        try:
            resp = router.complete("agent", _agent_thread_messages(state), tools=AGENT_TOOLS, temperature=0.3, max_tokens=4096)
        except Exception as exc:
            yield _sse("error", {"detail": f"模型调用失败：{exc}"})
            return
        message = {"role": "assistant", "content": resp.text or ""}
        if resp.tool_calls:
            message["tool_calls"] = [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in resp.tool_calls]
            state.agent_queue = list(message["tool_calls"])
        state.agent_thread.append(message)
        if resp.text:
            _agent_log(state, type="message", role="assistant", content=resp.text)
        store.save(state)
        if resp.text:
            yield _sse("message", {"role": "assistant", "content": resp.text})
        if not resp.tool_calls:
            yield _sse("done", {"history": _agent_history_for_ui(state)})
            return
    # Drain queued calls next turn only after an explicit interruption notice.
    _complete_missing_tools(state)
    store.save(state)
    yield _sse("message", {"role": "assistant", "content": "本轮操作预算已用完，未执行的工具已取消。可继续发消息推进。"})


def _managed_stream(state, generator) -> Iterator[str]:
    try:
        yield from generator
    except Exception as exc:
        yield _sse("error", {"detail": str(exc)})
    finally:
        with store.lock(state.session_id):
            latest = store.get(state.session_id)
            latest.agent_running = False
            if (latest.agent_execution or {}).get("status") == "claimed":
                latest.agent_execution["status"] = "interrupted"
                _complete_missing_tools(latest)
                _agent_log(latest, type="message", role="system", content="审批领取后的连接中断，结果未知。请检查实际状态；原审批不能重放。")
            store.save(latest)


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
    router = get_router()
    with store.lock(session_id):
        state = _session(session_id)
        msg = body.message.strip()
        if not msg or state.plan is None:
            raise HTTPException(status_code=400, detail="请先生成计划并输入消息")
        if state.agent_pending or state.agent_running:
            raise HTTPException(status_code=409, detail="请先处理待审批动作或等待当前操作结束")
        _complete_missing_tools(state)
        if not state.agent_thread:
            state.agent_thread = [{"role": "system", "content": _agent_system_prompt(state)}]
        else:
            state.agent_thread[0] = {"role": "system", "content": _agent_system_prompt(state)}
        state.agent_thread.append({"role": "user", "content": msg})
        _agent_log(state, type="message", role="user", content=msg)
        state.agent_running = True
        state.agent_execution = {"instance_id": INSTANCE_ID, "status": "conversation"}
        store.save(state)
    return _sse_response(_managed_stream(state, _agent_stream(state, _agent_runtime(), router)))


class AgentConfirmInput(BaseModel):
    action_id: str
    approve: bool


@app.post("/api/sessions/{session_id}/agent/confirm")
def agent_confirm(session_id: str, body: AgentConfirmInput) -> StreamingResponse:
    try:
        router = get_router()
    except HTTPException:
        if body.approve:
            raise
        router = None
    with store.lock(session_id):
        state = _session(session_id)
        pending = state.agent_pending
        if state.agent_running or pending is None or pending.get("action_id") != body.action_id:
            raise HTTPException(status_code=409, detail="动作已处理、正在执行或已过期")
        # Persist the claim before any side effect or response generator starts.
        state.agent_pending = None
        state.agent_running = True
        state.agent_execution = {"instance_id": INSTANCE_ID, "status": "claimed", "action_id": body.action_id,
                                 "tool_call_id": pending["tool_call_id"], "kind": pending["kind"]}
        store.save(state)
    rt = _agent_runtime()
    def generate():
        if body.approve:
            yield _sse("tool_start", {"kind": pending["kind"], "summary": pending["summary"]})
            try:
                result = rt.execute(PendingAction(**pending))
                state.agent_execution["status"] = "finished"
            except Exception as exc:
                result = f"执行失败：{exc}。请检查实际状态；该审批不能重放。"
                state.agent_execution["status"] = "failed"
        else:
            result = "用户拒绝了这个操作。请使用其他方式继续。"
            state.agent_execution["status"] = "rejected"
        state.agent_thread.append({"role": "tool", "tool_call_id": pending["tool_call_id"], "content": result})
        _agent_log(state, type="tool", kind=pending["kind"], summary=pending["summary"], output=result)
        if router is None:
            _complete_missing_tools(state)
        store.save(state)
        yield _sse("tool", {"kind": pending["kind"], "summary": pending["summary"], "output": result})
        if router is not None:
            yield from _agent_stream(state, rt, router)
        else:
            yield _sse("error", {"detail": "操作已拒绝，剩余工具已取消；修复模型配置后可继续对话"})
    return _sse_response(_managed_stream(state, generate()))


@app.post("/api/sessions/{session_id}/agent/reset")
def agent_reset(session_id: str) -> dict:
    with store.lock(session_id):
        state = _session(session_id)
        if state.agent_running:
            raise HTTPException(status_code=409, detail="请等待当前操作结束后再重置")
        state.agent_thread = None
        state.agent_pending = None
        state.agent_queue = []
        state.agent_logs = []
        state.agent_execution = None
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

    print(f"打开本机应用：http://127.0.0.1:{PORT}/#token={API_TOKEN}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
