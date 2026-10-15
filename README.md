# 校招雷达 0.5

校招雷达是一个持续的求职教练。它把岗位 JD、技能证据、学习计划、练习项目、实践进度、对话记录和审批记录放在同一会话上下文中，帮助用户从岗位分析一直推进到可核对的实践结果。

项目基于 Python 3.10+，提供命令行入口和 localhost 浏览器工作台；Windows 原生窗口可以由 `packaged_launcher.py` 构建。程序只在用户明确确认后写文件、执行命令或记录实践结果。

## 快速开始

在项目根目录执行：

```powershell
.\setup.ps1
.\start-ui.cmd --demo
```

`setup.ps1` 在项目内创建 `.venv` 并安装开发依赖。`--demo` 使用离线演示模型，不需要 API Key；启动后会打开本地浏览器工作台。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe .\run-web.py --workspace .\work --demo
```

启动脚本会把运行数据放在项目内的 `work`，该目录已加入 `.gitignore`。如果端口被占用，可追加 `--port 0` 让系统选择空闲端口；自动化或不打开浏览器时可追加 `--no-browser`。

## 连接模型

复制无密钥模板并填写 provider 地址和模型名：

```powershell
Copy-Item .\config\providers.example.yaml .\config\providers.yaml
$env:AGENT_COACH_API_KEY = '在当前终端临时设置'
.\start-ui.cmd --config .\config\providers.yaml
```

配置文件只保存端点、模型和环境变量名，实际密钥通过环境变量提供。`config/providers.yaml`、`.env`、工作区设置和会话数据不会进入 Git。命令行也支持：

```powershell
.\.venv\Scripts\python.exe .\run.py init --config .\config\providers.yaml
.\.venv\Scripts\python.exe .\run.py doctor --config .\config\providers.yaml
.\.venv\Scripts\python.exe .\run.py --workspace .\work --config .\config\providers.yaml
```

浏览器侧栏左下角的“管理 API 配置”用于维护多个模型配置。配置保存在本地工作区，并对所有岗位会话共享；顶部选择器可以切换当前使用的配置。已保存的密钥不会回显，清除密钥需要显式操作。GitHub Token 也只在用户主动设置时使用。

## 工作台与求职流程

工作台按四个步骤组织：岗位 JD、技能档案、计划与项目、Agent 实操。步骤页和下方对话使用同一个 Agent、会话和求职状态。可以粘贴文本、上传材料，或显式导入本地文件；图片支持 PNG、JPEG、GIF 和 WebP，单张不超过 10 MiB，每轮不超过 20 MiB。

练习项目按项目身份保存历史。任意时刻只有一个活动项目，但已完成或暂停的项目会保留；重新选择候选项目不会抹掉其他项目的进度。更新 JD 或技能资料会使依赖它们的当前计划重新生成，历史证据仍可追溯。

所有会话都使用同一套模型配置和工具协议，但每个会话仍有独立的消息与求职状态。文件读取、网页访问、Git 操作、写入和命令执行都经过工作区边界检查；有副作用的操作先生成审批预览。

## 入口与架构

| 入口 | 用途 |
| --- | --- |
| `run.py` | 源码方式启动 CLI，调用 `agent_coach.cli` |
| `run-web.py` | 源码方式启动 localhost 浏览器工作台，调用 `agent_coach.web` |
| `start.cmd` | 使用 `.venv` 或 `AGENT_COACH_PYTHON` 启动 CLI |
| `start-ui.cmd` / `启动界面.cmd` | 使用本地配置或离线 demo 启动浏览器工作台 |
| `packaged_launcher.py` | Windows 原生窗口的 PyInstaller 入口，共用同一套 HTTP 服务 |

核心调用链是：浏览器或 CLI → `AgentEngine` → 当前会话模型 → 工具注册表 → 求职状态与工作区。`job_radar.py` 负责 JD、技能、计划、项目和实践进度；`tools.py` 负责文件、文档、网页、Git、命令和审批；`store.py` 负责工作区绑定、会话持久化、互斥锁和原子写入；`legacy/` 保存兼容的模型适配器、文档处理和求职算法。

完整模块关系见 [docs/architecture.md](docs/architecture.md)。

## Windows 原生窗口（可选）

浏览器工作台已经可以直接使用。需要构建原生窗口时，先安装桌面依赖和 PyInstaller：

```powershell
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e '.[desktop]'
.\.venv\Scripts\python.exe -m pip install --no-cache-dir pyinstaller
```

然后使用当前项目的解释器运行构建脚本；不要依赖构建脚本的默认解释器路径：

```powershell
.\build_windows_exe.ps1 -Python (Resolve-Path .\.venv\Scripts\python.exe) -InstallDesktopDeps -PackageName XiaozhaoRadar
```

构建结果位于 `dist\XiaozhaoRadar`，生成的程序标题为“校招雷达”。Windows 需要 WebView2 Runtime。构建缓存和输出目录已加入 `.gitignore`，便携包不要与 `work` 或 `.agent-coach` 一起分发。

## 测试与资料

运行离线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖消息转换、模型路由、会话恢复、审批、文件边界、图片附件、求职状态、项目历史和浏览器 API。测试不代表真实模型、在线 GitHub 搜索或本机麦克风已经验收；这些能力需要用户提供配置后单独验证。

- [架构说明](docs/architecture.md)
- [验证范围](docs/validation.md)
- [历史需求参考](docs/需求清单.md)
- [无密钥 provider 模板](config/providers.example.yaml)

项目来源和迁移范围见 [docs/origin.json](docs/origin.json)。发布内容不包含原仓库的 `.git`、虚拟环境、真实 provider 配置、Token、会话、工作区、构建输出或本地验收缓存。
