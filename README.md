# 校招雷达 · Job Radar Coach

给求职者的一站式「岗位要求 → 学习计划 → 动手实操」桌面应用：把 BOSS 直聘等平台的**岗位截图**丢给它，AI 解析出岗位要求，对比你的技能档案做**差距分析**，输出**分阶段学习计划**并检索 **GitHub 可复现项目**，最后由一个能操作你电脑的 **Agent** 按计划带你逐个补足技能点。

## 工作流

```
① 岗位 JD（截图） → ② 技能档案（简历/自填/访谈） → ③ 计划 & 项目（差距分析+分阶段计划+GitHub 项目）
→ ④ Agent 实操（按计划补技能点，操作真实电脑，按需确认） → ⚙ 模型配置（多 provider）
```

- **① JD 识别**：多张/长截图输入，视觉模型提取岗位信息，识别后可手动修正
- **② 技能档案**：上传简历 / 工作日志，或与 AI 访谈逐步建立技能画像
- **③ 计划 & 项目**：差距分析（missing / weak / evidence_insufficient / met）+ 2~5 阶段学习计划 + GitHub 项目检索（star 过滤 + LLM 重排）
- **④ Agent 实操**：Codex 式操作面板——对话与真实操作实时可见，写文件 / 跑命令 / git clone 前需你确认；Agent 以计划为主线带用户补技能点（③ 的差距、阶段、项目自动注入 Agent 上下文）
- **⚙ 模型配置**：任意 OpenAI 兼容 / Anthropic 端点，YAML 注册 + 环节级模型路由 + 失败自动降级

## 技术架构

| 层 | 技术 | 说明 |
|---|---|---|
| 桌面壳 | Tauri 2 (Rust) | 启动 sidecar、开窗口；窗口直接加载 `http://127.0.0.1:17689/` |
| 后端 | Python FastAPI（PyInstaller 打包为 sidecar） | 业务内核 + 同源托管前端 + API |
| 前端 | React + Vite + TypeScript | 五环节界面 |
| LLM 接入 | 多 provider 注册表 | OpenAI 兼容 / Anthropic，YAML 配置，任务级 fallback 链 |

- sidecar 只监听回环地址 `127.0.0.1:17689`，不走系统代理，避免被代理/TUN 拦截
- 跨平台：Windows / Linux / macOS（代码零平台耦合，见下方构建）

```
src/coach/
├── core/
│   ├── schema.py              # 内部统一 Message / ToolCall / LLMResponse
│   └── llm/
│       ├── base.py            # LLMClient 抽象 + 可重试错误判定
│       ├── openai_compat.py   # DeepSeek/Kimi/智谱/豆包/OpenAI/Ollama/vLLM
│       ├── anthropic_adapter.py
│       ├── registry.py        # providers.yaml 声明式注册表
│       └── router.py          # 环节路由、fallback、结构化 JSON 解析
├── domain/schemas.py          # JDCard / UserSkillProfile / LearningPlan / GitHubRepo
├── tools/
│   ├── jd_parser.py           # ① 多截图 → JDCard（视觉模型）
│   ├── profile_builder.py     # ② 材料 → 技能档案（增量合并）
│   ├── interviewer.py         # ②b 一次一问的技能访谈
│   ├── planner.py             # ③ 差距分析 + 分阶段计划（含异常重试）
│   ├── github_search.py       # ③ GitHub 检索 + README/LLM 重排
│   ├── pipeline.py            # 一键跑完整条管线
│   ├── agent.py               # ④ Agent 运行时（工作区受限、按需确认）
│   └── session.py             # 会话状态持久化
└── sidecar/
    ├── main.py                # FastAPI：同源托管前端 + API + SSE 流式 Agent
    ├── paths.py               # 跨平台路径定位（开发态/打包态）
    └── store.py               # 会话存储
```

## 快速开始（开发态）

```bash
# 后端
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/providers.example.yaml config/providers.yaml
cp .env.example .env        # 填入你的 DEEPSEEK_API_KEY 等
python -m coach.sidecar.main   # 启动 127.0.0.1:17689

# 前端（另开终端）
npm install
npm run build

# 桌面壳（可选）
cd src-tauri && cargo build --release
```

浏览器打开 `http://127.0.0.1:17689/` 即可使用（无需 Rust 壳）。

## 构建桌面应用

### Windows
```powershell
pip install pyinstaller
pyinstaller --noconfirm src-tauri/job-radar-sidecar.spec
npm run build
Copy-Item dist\* src-tauri\target\release\dist\ -Recurse -Force
cd src-tauri; cargo build --release
```

### Linux（Ubuntu 22.04+）
```bash
sudo apt install -y libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev \
  python3-venv python3-pip nodejs npm
bash build-linux.sh     # 一键：venv → sidecar → 前端 → Rust 壳 → 组装
```

## 配置模型

`config/providers.yaml` 是唯一需要了解的配置：任何 OpenAI 兼容端点只需加一段注册，`tasks` 定义每个环节的 fallback 链（如识图用视觉模型、规划用强推理模型）。API key 一律放 `.env`（`api_key_env` 指向的环境变量），不进代码。

## 安全

- Agent 能操作真实电脑：只读操作（列目录/读文件）自动执行；写文件、跑命令、git clone 一律**先弹确认**，你批准才执行
- Agent 文件操作被限制在应用工作区目录内（路径越界自动拒绝）
- 会话数据（岗位截图、技能档案、计划）只存本机 `data/`

## License

MIT
