"""导师 Agent 运行时：在用户本机「按需确认」地执行操作，带用户做项目。

设计原则（与用户全局执行模式一致）：
- 只读操作（列目录 / 读文件）自动执行；
- 写文件、执行任意命令、git clone 一律先生成待确认动作，用户批准后才执行；
- 所有路径必须落在工作区内（防止越界读写），命令在指定目录内执行；
- 命令在用户真实系统上运行，无沙箱；由确认机制兜底风险。
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.schema import ToolSpec

OUTPUT_LIMIT = 30000          # 命令输出截断
FILE_READ_LIMIT = 20000       # 读文件截断

AGENT_SYSTEM_PROMPT = """你是「校招雷达」的实操导师 Agent，能操作这台电脑，亲手带用户把项目做出来、把技能差距补上。

你的主线任务（来自「计划 & 项目」页，必须据此推进，不要当白板）：
{plan_ctx}

推进规则：
1. 主线是「按计划补齐技能点」：优先处理差距分析里 missing / weak 的技能，按分阶段计划从第一阶段推进；推荐项目用于实践验证——把项目拆成能对应某个技能点的练习，而不是笼统地"把项目跑起来"；
2. 每完成一个技能点，先让用户自检（能讲出原理 / 独立做出小练习 / 跑通对应代码），确认掌握再进入下一步；
3. 动手前先告诉用户：这一步对应哪个技能点、要做什么、预期结果；
4. 一次推进一个明确目标，完成一步就用中文汇报：做了什么、对应补了哪个技能点、结果要点、下一步打算。

工作区：{workspace}
你只能在这个工作区里读写文件和执行命令（工具会自动校验路径，越界会报错）。

可用工具：
- list_dir(path)：列目录，自动执行；
- read_file(path)：读文件，自动执行；
- write_file(path, content)：写/覆盖文件，需要用户确认；
- run_command(command, cwd)：执行命令，需要用户确认；
- git_clone(url, target_dir)：克隆仓库到工作区，需要用户确认。

工作方式：
1. 先侦察再动手：先 list_dir / read_file 摸清现状（已克隆的项目、已有代码），再规划下一步；
2. 该动手时就动手：能自动做的直接做（读文件、列目录），要写文件/跑命令/克隆就先发起工具调用，等用户确认后结果会返回给你，你继续推进；
3. 出错时先读错误信息自己排查（看日志、试命令），不要一上来就让用户操作；
4. 涉及删除、覆盖、危险命令时在工具调用前先说明理由，让用户理解为什么需要；
5. 与用户的对话全部用中文，简洁，不用客套。"""


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
        description="列出工作区内某个目录的内容（自动执行，无需确认）",
        parameters={"type": "object", "properties": {"path": {"type": "string", "description": "目录路径，相对工作区或绝对路径", "default": "."}}},
    ),
    ToolSpec(
        name="read_file",
        description="读取工作区内一个文本文件的内容（自动执行，超长自动截断）",
        parameters={"type": "object", "properties": {"path": {"type": "string", "description": "文件路径"}}, "required": ["path"]},
    ),
    ToolSpec(
        name="write_file",
        description="在工作区内新建或覆盖一个文件（需要用户确认）",
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
        description="在工作区内执行一条 shell 命令（需要用户确认）。命令会真实运行在用户电脑上，Windows 用 PowerShell 语法，Linux/macOS 用 bash 语法",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "cwd": {"type": "string", "description": "命令工作目录，相对工作区，默认工作区根", "default": "."},
            },
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="git_clone",
        description="把 GitHub 仓库克隆到工作区（需要用户确认）",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "仓库 git 地址或 https 页面地址"},
                "target_dir": {"type": "string", "description": "克隆到的目录名，默认用仓库名", "default": ""},
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
    """工作区受限的本地操作执行器。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 路径安全 ─────────────────────────────────────────────────

    def _safe(self, path: str | Path) -> Path:
        p = Path(str(path)).expanduser()
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"路径超出工作区，已拒绝：{p}")
        return p

    # ── 工具分发：自动执行返回文本，需确认返回 PendingAction ─────

    def run_tool(self, name: str, args: dict, tool_call_id: str) -> str | PendingAction:
        args = args or {}
        if name == "list_dir":
            return self.list_dir(args.get("path", "."))
        if name == "read_file":
            return self.read_file(args.get("path", ""))
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
        p = self._safe(path)
        if not p.is_dir():
            return f"（不是目录：{p}）"
        lines = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            kind = "DIR " if child.is_dir() else "    "
            lines.append(f"{kind} {child.name}")
        return "\n".join(lines) or "（空目录）"

    def read_file(self, path: str) -> str:
        p = self._safe(path)
        if not p.is_file():
            raise FileNotFoundError(f"文件不存在：{p}")
        data = p.read_bytes()
        text = data.decode("utf-8", errors="replace")
        return _fmt_output(text, FILE_READ_LIMIT)

    # ── 需确认：只准备，不执行 ──────────────────────────────────

    def write_file(self, tool_call_id: str, path: str, content: str) -> PendingAction:
        p = self._safe(path)
        existed = p.exists()
        action = PendingAction(
            action_id=uuid.uuid4().hex[:10],
            kind="write_file",
            summary=f"{'覆盖' if existed else '新建'}文件 {p}（{len(content)} 字符）",
            tool_call_id=tool_call_id,
            payload={"path": str(p), "content": content},
        )
        return action

    def run_command(self, tool_call_id: str, command: str, cwd: str = ".") -> PendingAction:
        workdir = self._safe(cwd)
        if not workdir.is_dir():
            raise ValueError(f"工作目录不存在：{workdir}")
        action = PendingAction(
            action_id=uuid.uuid4().hex[:10],
            kind="run_command",
            summary=f"执行命令（在 {workdir}）：{command[:200]}",
            tool_call_id=tool_call_id,
            payload={"command": command, "cwd": str(workdir)},
        )
        return action

    def git_clone(self, tool_call_id: str, url: str, target_dir: str = "") -> PendingAction:
        name = target_dir.strip() or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dest = self._safe(name)
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
            path.parent.mkdir(parents=True, exist_ok=True)
            content = action.payload["content"]
            path.write_text(content, encoding="utf-8")
            return f"已写入 {len(content)} 字符 → {path}"
        if action.kind == "run_command":
            result = self._shell(action.payload["command"], action.payload["cwd"])
            return result
        if action.kind == "git_clone":
            url, dest = action.payload["url"], action.payload["dest"]
            result = self._shell(f"git clone {url} {dest}", str(self.root))
            return result
        return f"（未知动作类型：{action.kind}）"

    def _shell(self, command: str, cwd: str, timeout: int = 300) -> str:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"命令超时（>{timeout}s），已终止"
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()
        if proc.returncode != 0:
            return f"退出码 {proc.returncode}：\n{_fmt_output(out)}"
        return _fmt_output(out or "(无输出)")
