"""导师 Agent 运行时：在用户本机「按需确认」地执行操作，带用户做项目。

通用 Agent 能力 + 校招雷达定向改进：
- 只读操作（列目录 / 读文件 / 抓取网页）自动执行，可访问电脑任意路径（敏感文件除外）；
- 写文件、执行任意命令、git clone 一律先生成待确认动作，用户批准后才执行；
- 默认工作区仍作为推荐落盘位置（项目文件放这里），但不再强制限制路径；
- 网络读取（GitHub 页面 / raw 文件 / API 文本）通过 fetch_url，跟随 git 全局代理。
"""

from __future__ import annotations

import json
import os
import difflib
import hashlib
from urllib.parse import urlsplit
from .credentials import github_token
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.schema import ToolSpec
from ..sidecar import paths

OUTPUT_LIMIT = 30000          # 命令输出截断
FILE_READ_LIMIT = 20000       # 读文件截断
FETCH_LIMIT = 20000           # 网页抓取截断

# 凭据/敏感文件：读取时自动跳过，避免密钥进对话与日志
SENSITIVE_RE = re.compile(
    r"(^|[/\\])(\.env|\.ssh|\.aws|\.npmrc|\.pypirc|\.git-credentials|credentials|secrets?|"
    r"\.kube|\.config/gcloud|id_rsa|id_ed25519|id_dsa|.*\.pem|.*\.key|.*\.p12|.*\.pfx|"
    r".*token.*|.*secret.*|.*password.*|.*credential.*)(\.|$|[/\\])",
    re.IGNORECASE,
)

AGENT_SYSTEM_PROMPT = """你是「校招雷达」的实操导师 Agent，能操作这台电脑、能上网，亲手带用户把项目做出来、把技能差距补上。

你的主线任务（来自「计划 & 项目」页，必须据此推进，不要当白板）：
{plan_ctx}

推进规则：
1. 主线是「按计划补齐技能点」：优先处理差距分析里 missing / weak 的技能，按分阶段计划从第一阶段推进；推荐项目用于实践验证——把项目拆成能对应某个技能点的练习，而不是笼统地"把项目跑起来"；
2. 每完成一个技能点，先让用户自检（能讲出原理 / 独立做出小练习 / 跑通对应代码），确认掌握再进入下一步；
3. 动手前先告诉用户：这一步对应哪个技能点、要做什么、预期结果；
4. 一次推进一个明确目标，完成一步就用中文汇报：做了什么、对应补了哪个技能点、结果要点、下一步打算。

可用工具：
- list_dir(path)：列目录，自动执行，可访问电脑任意路径；
- read_file(path)：读文本文件，自动执行，可访问电脑任意路径（凭据/密钥类文件会被跳过）；
- fetch_url(url)：抓取网页 / GitHub 页面 / raw 文件 / API 的文本内容，自动执行，跟随系统代理；
- write_file(path, content)：写/覆盖文件，需要用户确认；
- run_command(command, cwd)：执行 shell 命令，需要用户确认；
- git_clone(url, target_dir)：克隆仓库，需要用户确认。

工作方式：
1. 通用能力：用户让你看电脑上的文件、查 GitHub 上的仓库/README/文档，直接用 list_dir / read_file / fetch_url 去看，不要推脱"看不到"；git_clone 可以把仓库拉到本地再精读；
2. 该动手时就动手：能自动做的直接做（读文件、列目录、抓网页）；要写文件/跑命令/克隆就先发起工具调用，等用户确认后结果会返回给你，你继续推进；
3. 出错时先读错误信息自己排查（看日志、试命令），不要一上来就让用户操作；
4. 涉及删除、覆盖、危险命令时在工具调用前先说明理由，让用户理解为什么需要；
5. 项目文件建议放在工作区里统一管理：{workspace}；
6. 与用户的对话全部用中文，简洁，不用客套。"""


@dataclass
class PendingAction:
    action_id: str
    kind: str                  # run_command / write_file / git_clone
    summary: str               # 前端确认卡显示的一句话
    tool_call_id: str
    payload: dict = field(default_factory=dict)


AGENT_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="list_dir",
        description="列出某个目录的内容（自动执行，无需确认；相对路径基于工作区，也可用绝对路径访问电脑任意目录）",
        parameters={"type": "object", "properties": {"path": {"type": "string", "description": "目录路径，相对工作区或绝对路径", "default": "."}}},
    ),
    ToolSpec(
        name="read_file",
        description="读取一个文本文件的内容（自动执行，超长自动截断；凭据/密钥类文件会被跳过）",
        parameters={"type": "object", "properties": {"path": {"type": "string", "description": "文件路径"}}, "required": ["path"]},
    ),
    ToolSpec(
        name="fetch_url",
        description="抓取网页/GitHub 页面/raw 文件/API 的文本内容（自动执行，跟随系统代理；适合查 README、文档、仓库代码页）",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的 URL，如 https://github.com/cc6554/project-assistant"},
                "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 8000", "default": 8000},
            },
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="write_file",
        description="新建或覆盖一个文件（需要用户确认；相对路径基于工作区，也可用绝对路径写电脑任意位置）",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件完整内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="run_command",
        description="执行一条 shell 命令（需要用户确认）。命令会真实运行在用户电脑上，Windows 用 PowerShell 语法，Linux/macOS 用 bash 语法",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "cwd": {"type": "string", "description": "命令工作目录，相对工作区或绝对路径，默认工作区根", "default": "."},
            },
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="git_clone",
        description="把 GitHub 仓库克隆到本地（需要用户确认）",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "仓库 git 地址或 https 页面地址"},
                "target_dir": {"type": "string", "description": "克隆到的目录（绝对路径，或相对工作区的目录名），默认用仓库名", "default": ""},
            },
            "required": ["url"],
        },
    ),
]


def _fmt_output(text: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（输出过长已截断，共 {len(text)} 字符）"


class AgentRuntime:
    """本地操作执行器：默认工作区为基准，允许任意路径，敏感文件受保护。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 路径解析（不强制限制，敏感文件除外）──────────────────────

    def _resolve(self, path: str | Path) -> Path:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    @staticmethod
    def _is_sensitive(p: Path) -> bool:
        return bool(SENSITIVE_RE.search(str(p)))

    # ── 工具分发：自动执行返回文本，需确认返回 PendingAction ─────

    def run_tool(self, name: str, args: dict, tool_call_id: str) -> str | PendingAction:
        args = args or {}
        if name == "list_dir":
            return self.list_dir(args.get("path", "."))
        if name == "read_file":
            return self.read_file(args.get("path", ""))
        if name == "fetch_url":
            return self.fetch_url(args.get("url", ""), int(args.get("max_chars", 8000) or 8000))
        if name == "write_file":
            return self.write_file(tool_call_id, args.get("path", ""), args.get("content", ""))
        if name == "run_command":
            return self.run_command(
                tool_call_id, args.get("command", ""), args.get("cwd", ".")
            )
        if name == "git_clone":
            return self.git_clone(tool_call_id, args.get("url", ""), args.get("target_dir", ""))
        raise ValueError(f"未知工具：{name}")

    # ── 自动执行：只读 ───────────────────────────────────────────

    def list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return f"（不是目录：{p}）"
        lines = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if self._is_sensitive(child):
                continue
            kind = "DIR " if child.is_dir() else "    "
            lines.append(f"{kind} {child.name}")
        return "\n".join(lines) or "（空目录）"

    def read_file(self, path: str) -> str:
        p = self._resolve(path)
        if self._is_sensitive(p):
            return "（敏感文件已跳过：凭据/密钥类文件不读取）"
        if not p.is_file():
            raise FileNotFoundError(f"文件不存在：{p}")
        data = p.read_bytes()
        text = data.decode("utf-8", errors="replace")
        return _fmt_output(text, FILE_READ_LIMIT)

    def fetch_url(self, url: str, max_chars: int = 8000) -> str:
        if not url.startswith(("http://", "https://")):
            return "（只支持 http/https 链接）"
        import requests

        proxy = self._git_proxy()
        token = self._github_token() if self._trusted_github_url(url) else None
        attempts = []
        if proxy:
            attempts.append({"proxies": {"http": proxy, "https": proxy}})
        attempts.append({"proxies": None})  # 代理失败降级直连
        last_err: Exception | None = None
        for attempt in attempts:
            try:
                kw = {"timeout": 10, "headers": {"User-Agent": "job-radar-agent/0.2"}}
                if attempt["proxies"]:
                    kw["proxies"] = attempt["proxies"]
                resp = requests.get(url, **kw)
                # 匿名 401/403/404 且是 GitHub 链接：用用户显式 Token 认证重试
                if resp.status_code in (401, 403, 404) and token and (
                    self._trusted_github_url(url)
                ):
                    resp = requests.get(
                        url,
                        headers={**kw["headers"], "Authorization": f"Bearer {token}"},
                        allow_redirects=False,
                        **{k: v for k, v in kw.items() if k != "headers"},
                    )
                if resp.status_code != 200:
                    # 私有仓库网页 404：降级到 GitHub API 读 README + 顶层结构
                    if token and self._trusted_github_url(url) and urlsplit(url).hostname == "github.com" and "/blob/" not in url:
                        api_text = self._github_api_read(url, token, kw)
                        if api_text is not None:
                            return _fmt_output(api_text, min(int(max_chars), FETCH_LIMIT))
                    return f"（HTTP {resp.status_code}，抓取失败：{url}）"
                text = resp.text
                # 去掉 script/style，减少噪音
                text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return _fmt_output(text, min(int(max_chars), FETCH_LIMIT))
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        return f"（抓取失败：代理与直连均不可用。{last_err}）"

    @staticmethod
    def _github_token() -> str | None:
        return github_token()

    @staticmethod
    def _trusted_github_url(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.hostname in {"github.com", "api.github.com", "raw.githubusercontent.com"}
                    and parsed.port in (None, 443) and not parsed.username and not parsed.password)
        except ValueError:
            return False

    def _github_api_read(self, url: str, token: str, kw: dict) -> str | None:
        """对 github.com/{owner}/{repo} 用 API 拉 README + 顶层文件结构（私有仓库可见）。"""
        import requests

        m = re.match(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/?#]|$)", url)
        if not m:
            return None
        owner, repo = m.group(1), m.group(2)
        headers = {**kw.get("headers", {}), "Authorization": f"Bearer {token}"}
        proxies = kw.get("proxies")
        get_kw = {"headers": headers, "timeout": 10, "allow_redirects": False}
        if proxies:
            get_kw["proxies"] = proxies
        parts: list[str] = []
        try:
            r = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers={**headers, "Accept": "application/vnd.github.raw"},
                timeout=10, proxies=proxies or {}, allow_redirects=False,
            )
            if r.status_code == 200:
                parts.append("README:\n" + r.text[:6000])
        except Exception:  # noqa: BLE001
            pass
        try:
            r = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/",
                timeout=10, headers=headers, proxies=proxies or {}, allow_redirects=False,
            )
            if r.status_code == 200:
                items = r.json()
                if isinstance(items, list):
                    lines = [
                        ("DIR  " if it.get("type") == "dir" else "     ") + it.get("name", "")
                        for it in items
                    ]
                    parts.append("仓库顶层结构（进入仓库后请用 github_repo 逐层查看）：\n" + "\n".join(lines))
        except Exception:  # noqa: BLE001
            pass
        return "\n\n".join(parts) if parts else None

    def _git_proxy(self) -> str | None:
        """复用 git 全局代理（与用户 git 行为一致），失败则返回 None。"""
        try:
            out = subprocess.run(
                ["git", "config", "--global", "http.proxy"],
                capture_output=True, text=True, timeout=5,
            )
            proxy = (out.stdout or "").strip()
            return proxy or None
        except Exception:  # noqa: BLE001
            return None

    # ── 需确认：只准备，不执行 ──────────────────────────────────

    def write_file(self, tool_call_id: str, path: str, content: str) -> PendingAction:
        p = self._resolve(path)
        existed = p.exists()
        action = PendingAction(
            action_id=uuid.uuid4().hex[:10],
            kind="write_file",
            summary=f"{'覆盖' if existed else '新建'}文件 {p}（{len(content)} 字符）",
            tool_call_id=tool_call_id,
            payload={"path": str(p), "content": content,
                     "before_hash": hashlib.sha256(p.read_bytes()).hexdigest() if existed else None,
                     "diff": "".join(difflib.unified_diff((p.read_text(encoding="utf-8", errors="replace") if existed and not self._is_sensitive(p) else "（原文件内容属于敏感信息，不自动展示）" if existed else "").splitlines(keepends=True), content.splitlines(keepends=True), fromfile=str(p), tofile=str(p)))},
        )
        return action

    def run_command(self, tool_call_id: str, command: str, cwd: str = ".") -> PendingAction:
        workdir = self._resolve(cwd)
        if not workdir.is_dir():
            raise ValueError(f"工作目录不存在：{workdir}")
        action = PendingAction(
            action_id=uuid.uuid4().hex[:10],
            kind="run_command",
            summary=f"执行命令（在 {workdir}）：{command}",
            tool_call_id=tool_call_id,
            payload={"command": command, "cwd": str(workdir), "shell": "PowerShell" if os.name == "nt" else "bash"},
        )
        return action

    def git_clone(self, tool_call_id: str, url: str, target_dir: str = "") -> PendingAction:
        self._validate_clone_url(url)
        name = target_dir.strip() or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dest = self._resolve(name)
        action = PendingAction(
            action_id=uuid.uuid4().hex[:10],
            kind="git_clone",
            summary=f"git clone {url} → {dest}",
            tool_call_id=tool_call_id,
            payload={"url": url, "dest": str(dest)},
        )
        return action

    # ── 执行已批准的动作 ─────────────────────────────────────────

    def execute(self, action: PendingAction) -> str:
        if action.kind == "write_file":
            path = Path(action.payload["path"])
            if "before_hash" in action.payload:
                actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                if actual != action.payload["before_hash"]:
                    raise ValueError("文件在审批期间发生变化，请重新生成审批")
            path.parent.mkdir(parents=True, exist_ok=True)
            content = action.payload["content"]
            path.write_text(content, encoding="utf-8")
            return f"已写入 {len(content)} 字符 → {path}"
        if action.kind == "run_command":
            result = self._shell(action.payload["command"], action.payload["cwd"])
            return result
        if action.kind == "git_clone":
            url, dest = action.payload["url"], action.payload["dest"]
            self._validate_clone_url(url)
            result = self._run_process(["git", "clone", "--", url, dest], str(self.root))
            return result
        return f"（未知动作类型：{action.kind}）"

    @staticmethod
    def _validate_clone_url(url: str) -> None:
        parsed = urlsplit(url)
        if any(c.isspace() for c in url) or parsed.scheme not in {"https", "http", "ssh"} or not parsed.hostname or parsed.password:
            raise ValueError("克隆地址必须是有效的 HTTPS/HTTP/SSH URL，不能包含密码或空白")

    def _shell(self, command: str, cwd: str, timeout: int = 300) -> str:
        if os.name == "nt":
            executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
            args = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            args = ["/bin/bash", "-lc", command]
        return self._run_process(args, cwd, timeout)

    def _run_process(self, args: list[str], cwd: str, timeout: int = 300) -> str:
        try:
            proc = subprocess.run(args, shell=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"命令超时（>{timeout}s），已终止"
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if proc.returncode != 0:
            return f"退出码 {proc.returncode}：\n{_fmt_output(out.strip())}"
        return _fmt_output(out.strip() or "(无输出)")
