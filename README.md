# 简历项目助手

简历项目助手是一个面向求职的持续对话助手。用户从目标岗位 JD、简历或已有技能开始，和同一个 Agent 在同一会话中多轮交流；Agent 会整理岗位要求、补充技能证据、规划练习、筛选候选项目，并根据真实文件和命令结果更新实践进度。

求职状态、对话消息、工具调用和审批记录一起保存在本地会话中。文件修改和命令执行仍需用户确认，Agent 不会把模型的口头结论当作已完成的实践证据。

本项目基于原 `job-radar-coach` 的模型适配、文档处理和求职业务模块重建。原项目、桌面程序、虚拟环境和 Git 仓库未迁移或覆盖。来源见 [docs/origin.json](docs/origin.json)，架构见 [docs/architecture.md](docs/architecture.md)。

## 0.6 分支更新

- 在“计划与项目”生成候选项目后，进入“Agent 实操”直接选择。选择结果保存在当前会话中，重新打开会话时可恢复。
- 打开主对话弹窗时，消息列表会定位到最新消息。
- 桌面离线语音使用本地 faster-whisper，累计更新包含录音、转写和语音指令识别的修复。

## 浏览器前端

在 Windows PowerShell 中进入仓库根目录，安装依赖并启动离线演示。

```powershell
.\setup.ps1
.\start-ui.cmd --demo
```

安装依赖后也可以双击 `启动界面.cmd`。启动脚本把工作区设为仓库内的 `work`。默认使用 `config/providers.yaml`，文件缺失时进入离线演示。可通过 `--config .\config\providers.yaml` 指定配置，通过 `--port` 指定端口，或通过 `--no-browser` 跳过自动打开浏览器。服务失败时，启动窗口会保留错误信息并暂停。

浏览器工作台通过 localhost 提供页面和接口。启动脚本优先使用 `AGENT_COACH_PYTHON` 指定的解释器。`setup.ps1` 默认在仓库内创建 `.venv`，启动脚本会自动使用该环境。

工作台以对话为中心，左侧保留会话历史，右侧显示目标 JD、已有技能、候选/选中练习项目和实践进度。上传简历、JD 或图片后，前端先把文件放入工作区，再在同一条用户消息中提交图片路径；引擎校验工作区边界，把图片转换为 `ImageBlock`，并和文字一起交给当前会话使用的模型。图片仅支持 PNG、JPEG、GIF、WebP，单张不超过 10 MiB，同一轮图片总量不超过 20 MiB；AVIF、BMP、TIFF 等格式需先转换。文本和文档材料则通过工作区路径交给 Agent 使用文件工具读取。

## Windows 原生桌面程序

`dist\JobCoachDesktop` 是独立的窗口版交付目录。`简历项目助手.exe` 启动后在本机 loopback 的随机端口运行同一套 HTTP 服务，并用 pywebview 的 EdgeChromium/WebView2 创建原生窗口；它不会调用系统浏览器，也不会弹出控制台窗口。关闭窗口会停止本地服务。

构建需要 Windows WebView2 Runtime，以及一次网络安装桌面构建依赖。建议使用默认不继承系统 `site-packages` 的独立虚拟环境，并通过 `-Python` 显式传入，避免 Anaconda 全局可选库被 PyInstaller 收集；依赖和 PyInstaller 的缓存全部放在项目的 `build` 目录：

```powershell
py -3.12 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install --no-cache-dir pyinstaller
.\build_windows_exe.ps1 -Python (Resolve-Path .\.venv-build\Scripts\python.exe) -ExecutableName '简历项目助手.exe' -InstallDesktopDeps
```

构建结果为 `dist\JobCoachDesktop\简历项目助手.exe` 和 `dist\JobCoachDesktop-windows.zip`。如果只需重建而不重新下载依赖，可以省略 `-InstallDesktopDeps`；缺少 `build\windows-exe\desktop-deps` 时脚本会给出明确提示。pywebview 官方文档说明 Windows EdgeChromium 使用 WebView2 Runtime，PyInstaller 打包时需要收集 pywebview 模块；本项目通过 `--collect-all webview` 和隔离依赖目录完成这一步。

在桌面版的工作区切换对话框中点击“选择文件夹”会打开系统文件夹选择器；选择结果会回填路径，再沿用原有的复制或打开规则。浏览器运行时没有系统选择器接口，仍可手动输入绝对路径。

桌面语音识别使用本地 faster-whisper；录音开始后需要再次点击“结束录音”，随后在本机转写，识别不依赖云语音服务，也不会把录音上传到第三方。构建脚本会在 `build\offline-speech\model-base` 和 `build\offline-speech\pip-target` 都完整存在时自动打包模型与运行时；也可以显式传入 `-SpeechModelDir` 和 `-SpeechDepsPath`，脚本会只读校验模型文件和运行时包，校验失败即停止。语音资源缺失且未显式指定时仍可生成旧的非语音构建。模型和运行时准备路径分别是 `build\offline-speech\model-base` 与 `build\offline-speech\pip-target`。

双击 exe 时，`work`、`.agent-coach` 和 `config` 都以 exe 所在目录为根。模型设置保存在 `work\.agent-coach\model-settings.json`，WebView2 的缓存保存在 `work\.agent-coach\webview`，因此不会把桌面程序运行数据写到 C 盘临时目录。首次启动没有设置时，程序会使用旁边的 `config\providers.yaml`（若存在），否则进入离线 demo；在窗口的“模型设置”中保存配置后，后续重启优先恢复该工作区设置。

`--headless` 和兼容别名 `--no-browser` 用于冒烟或自动化验收：它们只启动 loopback 服务，不创建原生窗口，也不会打开浏览器。带控制台运行时会打印带 token 的本地 URL；windowed exe 的 URL 会写入 `work\.agent-coach\headless-url.txt`，仅供本机验收脚本读取。

## 命令行启动

按上面的安装步骤创建虚拟环境后，在仓库根目录启动命令行演示。

```powershell
.\start.cmd --demo
```

进入后输入 JD、经历或技能描述。离线脚本使用明确标注的虚构 JD 和技能档案，依次驱动 `set_jd`、`set_profile`、`build_plan`，再在真实 `write_file` 写入 `demo-output.txt` 前提出审批；查看预览后输入 `/approve 完整审批ID`，或 `/reject 完整审批ID`。`--demo` 是离线脚本，不代表接入了智能模型，也不会把虚构材料写入你的个人档案。

需要 Python 3.10+，安装依赖需要网络。`setup.ps1 -Python` 和环境变量 `AGENT_COACH_PYTHON` 分别用于指定创建虚拟环境和启动程序的解释器。

## 连接真实模型

桌面版直接点击右上角“模型设置”，选择协议，填写 API 地址、模型名称和 API Key。点击“测试连接”会发出一条不含求职资料的工具调用探测请求；“保存并应用”立即切换当前服务的模型，建议新建会话后开始使用。关闭设置不会回显密钥，留空保留已有密钥，显式勾选清除才会删除密钥。

界面保存的密钥使用 Windows 当前用户 DPAPI 加密，保存在 `work/.agent-coach/model-settings.key`；其他设置保存在同目录的 `model-settings.json`。复制到另一台电脑或另一 Windows 用户后，需要重新输入密钥。换 API 地址时也需要重新填写密钥或明确清除旧密钥。

以下手动配置方式供命令行使用。先生成无密钥模板：

```powershell
python .\run.py init --config .\config\providers.yaml
```

修改模板中的 provider 地址和模型名称。连续求职会话需要同一个模型支持工具调用；如果会话要直接处理图片材料，该模型还需要支持视觉输入。纯文本 JD、简历和工具结果只要求工具调用能力。配置保留 `tasks.default` 和 `tasks.agent` 两个入口，它们应指向同一条 Agent 模型链，不再按解析、规划或面试拆成不同的模型任务。密钥只引用环境变量，不写入配置文件：

```powershell
$env:AGENT_COACH_API_KEY = '你的密钥'
python .\run.py doctor --config .\config\providers.yaml
python .\run.py --workspace .\work --config .\config\providers.yaml
```

上述 `python` 可以替换为现有解释器的完整路径。`doctor` 只检查本地配置，不发送网络请求，也不验证服务可用性。程序不自动读取原项目配置、`.env` 或 Git 凭据；GitHub 集成只兼容读取用户显式设置的 `AGENT_COACH_GITHUB_TOKEN`。

支持 OpenAI 兼容的 Chat Completions 和 Anthropic Messages 适配器，以及显式配置的本地兼容端点。免费公共 GitHub 搜索可能限流；需要令牌时可以在工作区设置页保存 Token，也可以为命令行临时显式设置 `AGENT_COACH_GITHUB_TOKEN`。已保存的工作区 Token 优先于环境变量；UI 显式清除 Token 会留下工作区清除标记，因此不会意外回落到仍存在的环境变量。程序不读取系统 Git credential helper。

GitHub 与本地材料集成有明确边界：`read_github` 只访问 `https://api.github.com`，支持公开仓库或当前工作区授权的私有仓库文件、README 和目录；单次文件内容最多 2 MiB、API 响应最多 4 MiB，并拒绝敏感路径和跨域认证重定向。`fetch_url` 遇到 GitHub blob/raw 链接时会转到同一安全 API。

招聘 `search_jobs` 默认做全网公开网页检索，不添加平台 `site:` 限制；一次只接受一个目标岗位，按原始岗位名和固定招聘语境后缀生成有界变体，收集去重后的 15–20 条候选线索。每条结果都标记为 `source_kind=search_lead`、`evidence_status=unverified`；`job_detail` 是优先核实的详情线索，`search_index` 仅是聚合/列表线索，二者都不能直接当作 JD 证据。工具结果中的 `diagnostics` 保留变体、引擎尝试、失败原因、候选计数和网络路由探测；模型必须再用受控 `fetch_url` 读取 3–5 个独立公开详情页，只有实际可读正文才计入 `sample_count` 和 `source.evidence`，不足 3 个时标记 `insufficient_samples`，不能补写 JD。

工作区导入由人工 UI 显式提供绝对本地路径，支持文件或 Obsidian 文件夹；每次最多 1000 个文件、总计 20 MiB、单文件 2 MiB。文本会按 UTF-8 检查，受支持的 PDF、Office 和图片文件按大小受限的二进制复制并在 manifest 标明“未在导入阶段解析”；未知二进制、敏感文件、缓存目录、UNC 路径、符号链接和目录联接会被拒绝或跳过。导入结果只返回 workspace 相对路径。上述在线访问、私有仓库权限和本地文档解析仍取决于已安装依赖、网络和用户配置，离线测试使用 mock，不代表已完成真实账号验收。

## 使用方式

新会话可以直接输入：

```text
这是目标岗位 JD，请结合我上传的简历，先整理硬性要求和已有技能证据。
```

之后继续在同一会话中补充经历、修正 JD、选择练习项目或询问下一步。Agent 会保留前面的消息和工具结果，并在同一上下文里重复执行“模型回复 → 工具调用 → 工具结果 → 下一轮模型回复”，直到给出答复、等待审批、遇到未知执行结果或达到循环上限。用户不需要按固定按钮逐步调用模型。

内置工具覆盖工作区目录和文件、文档读取、网页访问、Git 仓库、文件写入、命令执行、求职状态和项目检索。工具在 Python 进程内执行，不会为了生成 JD、技能档案、学习计划或项目排序再启动另一个模型请求；结构化求职工具直接更新会话状态，所有模型推理都由当前 Agent 完成。

常用控制命令：

| 命令 | 用途 |
| --- | --- |
| `/help` | 查看当前完整命令表 |
| `/status` | 查看会话、执行和待审批状态 |
| `/plan` | 查看当前求职计划 |
| `/approve ID` / `/reject ID` | 对具体操作作出决定 |
| `/new` / `/resume ID` | 新建或切换会话 |
| `/continue` | 恢复未完成的工具队列或暂停的循环 |
| `/resolve ID 备注` | 人工核实不确定结果，记录结论而不重放操作 |
| `/quit` | 退出并保留会话 |

也可执行单次请求或恢复指定会话：

```powershell
python .\run.py sessions --workspace .\work
python .\run.py --workspace .\work --config .\config\providers.yaml run '请根据当前求职上下文整理目标岗位的硬性要求和下一步练习计划'
python .\run.py --workspace .\work --config .\config\providers.yaml --session 会话ID
```

单次 `run` 不会自动批准操作：遇审批返回退出码 3；失败或暂停返回非零状态。会话记录保存在工作区 `.agent-coach/sessions`，其中可能包含对话、文档内容和工具结果，按本地个人数据管理。

## 求职工件与审批

求职状态保存在每个会话的求职状态区，包含当前 JD、已有技能和证据、学习计划、候选项目、选中项目、实践进度及历史版本。更新 JD 或技能资料会使依赖它们的计划、项目和进度失效；旧结果仍保留在历史中，避免把过期建议当作当前结论。

Agent 可以从对话和工作区材料中建立求职资料，再使用项目搜索工具返回候选练习项目。项目文件的阅读、修改和命令执行仍由同一个 Agent 完成；实践完成记录需要可核对的证据，并在必要时进入人工审批。

浏览器上传接口只负责把文件安全地放进当前工作区。图片路径会作为同一用户 turn 的 `attachments` 发送给服务，由引擎在工作区边界内读取并生成 `ImageBlock`；路径、大小、媒体类型和敏感文件策略在引擎侧再次校验。Bearer token 只存在当前标签页的 `sessionStorage`，不会写入 URL 以外的日志或界面内容。

外部副作用使用 prepare/execute 审批分离。批准时要求当前会话的完整审批 ID；进程在执行标记写入后退出，恢复路径会把结果标为未知并禁止自动重放，用户核实后才能继续。

## 验证与边界

```powershell
python -m pytest -q
```

测试覆盖模型消息转换、路由回退、持久化、同一 Agent 的工具循环、审批、文件变更冲突、路径保护、图片附件（PNG/JPEG/GIF/WebP，单张 10 MiB、每轮 20 MiB）、求职流程及失效处理。实际验证结果见 [docs/validation.md](docs/validation.md)。

这是可运行的求职 Agent 基础版本，提供 localhost 浏览器工作台和 Windows 原生窗口交付。目前没有 MCP 客户端、多 Agent 调度、自动上下文压缩或操作系统级沙箱。批准的命令使用当前用户权限，能够产生工作区外的副作用；文件工具的路径约束不等于命令沙箱。网页访问进行公网地址及跳转检查，但仍存在 DNS 解析与连接之间的变化窗口。命令超时不保证终止全部孙进程。

离线测试不消耗模型额度；真实服务的密钥、工具调用质量、视觉理解和在线 GitHub 结果需要使用你配置的提供方验收。
