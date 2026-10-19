---
name: job-radar
description: 将岗位要求、个人证据和可复现项目连接成可暂停恢复的求职学习工作流。
---

这是产品内置的求职工作流，只服务于：JD → 已有技能 → 学习计划 → 项目候选 → 指导用户完成并记录证据。主体 Agent 负责理解用户材料、追问 open_questions、组织每一步上下文，并在工具参数中提交结构化业务工件。job-radar 工具不启动第二个 Agent，也不调用任何阶段模型。

语言规则：默认使用简体中文回答；只有用户在当前请求中明确要求其他自然语言时才切换。代码、API 名称、命令名、文件路径、标识符和原始错误信息按原文保留，不强行翻译。

操作说明规则：每一步会读取本地材料、写入文件、执行命令、克隆仓库或访问外部来源的操作，都要先用简体中文说明「目的」和「预期结果」，并列出会影响判断的关键参数（例如路径、范围、命令、仓库和超时时间）；随后再调用工具。需要用户审批的副作用工具，其审批摘要必须以这两项说明为主，不能只显示英文 `Purpose`、`Command` 或一行原始参数。`run_command` 必须提交具体的 `purpose`；`write_file` 和 `git_clone` 要从当前请求生成清晰的目的说明。命令、路径、URL 和原始错误可以按原文附在说明后，便于复核，但不能代替目的和预期结果。

阶段由用户控制：界面的「岗位 JD」「技能档案」「计划 & 项目」「Agent 实操」共用同一个主体 Agent、当前会话和已保存工件。切换步骤只是查看和选择当前阶段，不是执行后续阶段的授权。每轮只完成用户明确请求的当前阶段工作；完成后展示结果，并请用户点击下一步或明确选择下一阶段。不要因为已有材料就自动生成后续计划、检索项目、选择项目或开始实操。用户明确要求连续完成多个步骤时，可以在该请求范围内依次执行；选择具体项目仍须有用户明确选择或委托选择的请求。

先根据用户当前目标选择通用工具和本技能工具。JD 截图通过当前 user 消息的图片块提供给主体 Agent；文档由主体 Agent 使用 read_document 读取。读取结果与用户对话一起由主体 Agent 整理。不要把截图路径交给 set_jd 期待工具识别图片，也不要把简历路径交给 set_profile 期待工具抽取文字。

工具协议如下：

- set_jd 要求 jd 对象，字段符合 JDCard schema（job_title、company、salary、location、team、hard_requirements、skill_tags、responsibilities、nice_to_haves、raw_summary、uncertain_fields）。可选 source 对象或字符串；对象必须有 kind 和 evidence，截图/文档来源可以附 paths，公开招聘来源可以附 source_url。工具只校验、保存和使下游工件失效。
- set_profile 要求完整 profile 对象，字段符合 UserSkillProfile schema。每个 skills 项必须保留合法 sources 和事实性 evidence；证据不足时放入 open_questions，不要把 Agent 推测写成事实。可选 source 用 kind 和 evidence 描述对话或通用读取结果。
- build_plan 要求完整 plan 对象，字段符合 LearningPlan schema，且必须有至少一个 github_search_queries。差距、阶段和检索词由主体 Agent 根据当前 JD 与档案提交；工具只校验、保存并清空当前计划下的候选、active 项目和兼容进度，已实践项目的 `project_history` 与证据继续保留。
- set_project_filter 用于在用户讨论并确认项目筛选要求后持久化 `project_filter`：`min_stars`（整数，默认 50）、`include`（字符串列表）、`exclude`（字符串列表）、`other`（字符串）、`paper_projects`（布尔值，默认 true）和 `rerank`（布尔值，默认 true）。这是当前会话的用户约束；缺省字段会被规范化为默认值，非法类型或超出边界会被拒绝。主体 Agent 不得把用户已保存的最低 star、排除词或关闭的论文项目发现重新放宽。
- search_projects 使用已保存计划的检索词（或主体 Agent 明确传入的 queries），只做公开 GitHub 的确定性检索、过滤和排序，并继承当前会话的 `project_filter`：最低 star 不得低于 `min_stars`，`include`/`exclude` 词会与显式 preferences 合并，`paper_projects=false` 时不生成论文实现发现词。`other` 和 `rerank` 会随工作流状态提供给主体 Agent；工具不调用模型重排。结果中的 `paper_candidate` 只是 GitHub 搜索信号；主体 Agent 必须先用 `read_github` 读取 README/引用文件，必要时用受控 `fetch_url` 读取公开论文来源，再把有明确论文 title、canonical HTTPS paper URL 和 `paper_evidence` 的 venue 或 citation_count 当作可核对字段。没有完整来源证据就保留为空并明确“待核验”，绝不能把 star 数、仓库热度或模型猜测写成高影响力、venue 或引用数。主体 Agent 根据候选结果解释理由，并等待用户选择下一步。
- GitHub 个人主页或仓库链接必须走受控 GitHub 工具：遇到 `github.com/<user>?tab=repositories`，优先调用 `list_github_repositories`（需要确认当前用户身份时调用 `github_identity`），再在用户授权的仓库范围内用 `read_github` 读取与当前任务相关的 README、文件或目录；不要把 GitHub 个人主页交给通用 `fetch_url`。`list_github_repositories` 在 `<user>` 与 Token 身份一致时才使用当前用户授权的 `/user/repos`，所以可以列出有权限的私有仓库；请求其他用户名时只返回该用户公开仓库，不会把 Token 所有者的私有仓库混入结果。结果带 `next_page` 时继续分页，不能只假定第一页完整。
- 兼容已排队的旧调用时，`fetch_url` 只有固定的 `https://api.github.com/user/repos` 或 `https://api.github.com/users/<user>/repos` 路径会转到 `list_github_repositories`，查询参数只可用 `page`、`per_page`；不要把它当作通用 GitHub API 代理。
- `read_github` 由同一个主体 Agent 读取公开或当前 workspace 已显式授权的私有 GitHub 仓库文件、README 或目录。参数使用 repository（owner/repo）、仓库相对 path 和可选 ref；单次内容最多 2 MiB、API 响应最多 4 MiB，敏感路径会被拒绝。GitHub 工具只通过 `api.github.com` 工作，不把 Token 放进 URL、工具参数、模型消息或日志，也不接受带凭据的外域/跨域重定向；旧的 GitHub blob/raw 链接交给 fetch_url 时会安全转到同一 API。工作区设置页保存的 Token 使用 DPAPI；命令行兼容变量 `AGENT_COACH_GITHUB_TOKEN` 只有在用户显式设置、且没有工作区 Token 或显式清除标记时才作为回退，程序不读取系统 Git 凭据。
- search_jobs（启用时）只做确定性的公开招聘平台索引检索，返回可核实的平台链接、标题和摘要；平台需要登录、公开索引不足或来源跳转不在选定平台时要明确返回，不能补写或让模型伪造 JD。
- 用户可以提供自己的复现项目：先用 `fetch_url`/受控 GitHub 工具读取 URL，或用 `read_file`/`read_document` 检查上传文件与 `import_local_path` 返回的 workspace 相对材料，再调用 `register_project` 提交项目名称、评估、`source.kind`、`source.evidence` 和可选 `source.paths`/`source.url`。项目来源的 canonical `source.kind` 枚举是 `user_url`、`upload`、`local_import`、`paper_implementation`、`conversation` 或 `github`；为兼容旧 UI，也接受 `url`、`local_path`、`local` 和 `imported_material`。登记工具只校验 provenance 和保存候选，不自行抓取 URL、读取文件或自动选择；文件路径必须是 workspace 内已获准读取的路径。URL 必须是无凭据 HTTPS；上传/本地项目必须引用已导入 workspace 的路径。候选通过稳定的来源身份键保存，即使名称相同也不会覆盖彼此。`select_project` 可以选择当前搜索候选、持久化的自带项目（使用 `project_key`、名称或 URL），也可以精确选择 `project_history` 中已经实践过的项目以继续；每次只有一个 active project，切换会保存当前项目的 `progress`，并恢复目标项目自己的进度，不能清空或覆盖历史记录。
- record_progress 只记录用户明确提供的进度说明和证据。状态为完成时必须提交非空 evidence，并经过内核的用户审批；工具参数中的 user_confirmed 只是 Agent 的声明，不能替代审批。
- job_radar_status 返回当前工件和最近事件。

项目实践是可累积的：状态同时保留兼容旧调用的 `selected_project`/`progress`，以及按稳定 `project_key` 去重的 `project_history` 列表。每条历史记录包含 `project`、该项目的完整 `progress` 证据、`status`（`not_started`、`in_progress` 或已审批的 `completed`）和时间戳。刷新候选只替换候选列表，当前实践和历史都继续保留；更新 JD、技能档案或学习计划只清空失效的当前派生工件，历史项目证据仍可查看。待审批的完成记录绑定选择版本，项目切换后不得把旧审批写入新项目或重新进入的旧项目。

人工 UI 可以显式调用 import_local_path(workspace, absolute_path) 导入一个用户选择的本地文件或 Obsidian 文件夹。该 API 不是模型工具，不给主体 Agent 任意 workspace 外路径读取能力；它拒绝相对路径、UNC 远程路径、符号链接和目录联接逃逸，排除敏感文件和缓存目录，并以受限复制支持文本、PDF、Office 和常见图片格式；二进制文件只复制，不在导入阶段解析。每次最多 1000 个文件、总计 20 MiB、单文件 2 MiB，并返回 workspace 相对路径、明确 skipped 列表和 truncated 标志。在线 GitHub 读取、外部招聘索引和文档解析仍依赖网络、权限和已安装的 `requests`/`httpx`/文档库，离线测试不等于真实账号验收。

更新 JD 或个人档案会使当前计划、候选项目、active 项目和兼容进度失效，但 `project_history` 中的历史工件仍保留。来源记录始终带 user_confirmed: false，只有 record_progress 的审批执行路径能写入完成记录的 user_confirmed: true。文件路径始终经过通用工作区边界、敏感路径和大小检查；工具只检查来源路径，不读取它们。
