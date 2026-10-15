# 验证范围

这份说明记录校招雷达 0.5 的离线验证范围。它是随源代码发布的验证说明，不代表本次打包提交重新运行了全部测试；提交前只调整了发布文档、历史需求参考和文件清单，生产 Python/JavaScript 源码未改动。

## 本地复核

先按 README 创建项目内环境，再在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check .\src\agent_coach\web_static\app.js
```

如果没有桌面依赖，可以只运行 CLI、浏览器工作台和离线测试。原生窗口构建需要 Windows WebView2 Runtime、`.[desktop]` 依赖和 PyInstaller，命令见 README。

## 覆盖范围

- OpenAI 兼容与 Anthropic 消息转换、模型路由、失败回退和连续会话上下文。
- 同一 Agent 的模型回复、工具调用、工具结果回传、审批暂停和恢复。
- 岗位 JD、技能档案、差距分析、学习计划、候选项目、活动项目和累计项目历史。
- 会话持久化、锁、revision 冲突、未知执行结果和人工确认，不自动重放有副作用的操作。
- 工作区边界、敏感路径、符号链接、文件哈希、原子写入、命令超时和审批预览。
- 文本、PDF、Office 和 PNG/JPEG/GIF/WebP 图片材料；图片大小、媒体类型和单轮附件限制。
- 浏览器服务的 Bearer、Host/Origin、编码路径、代理式 URL、JSON 大小和后台 job 保护。
- CLI、浏览器工作台、模型设置、会话列表、文件预览、求职状态接口和离线 demo。

## 运行边界

离线测试不会调用真实模型或在线 GitHub 搜索。真实模型、视觉理解、GitHub 私有仓库和招聘平台结果需要用户提供配置后单独验证。批准的命令仍使用当前用户权限，文件路径保护不等同于操作系统级沙箱；当前版本也没有自动上下文压缩或多 Agent 调度。
