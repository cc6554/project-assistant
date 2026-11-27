# 工作区系统选择器

Windows 原生包的主 pywebview 页面通过 `window.pywebview.api.workspace_picker()` 请求系统文件夹选择器。这个方法只在用户点击工作区对话框中的“选择文件夹”按钮后调用；启动、服务初始化和页面加载不会自动扫描电脑或枚举目录。

Python bridge 的实现名是 `pick_workspace_folder(initial_path="")`，并保留 `workspace_picker`（前端首选）和 `select_workspace_folder` 两个同语义别名，方便其他桌面集成复用同一契约。

返回值是 JSON 对象：

```json
{"ok": true, "available": true, "selected": true, "cancelled": false, "path": "F:\\resume-projects"}
```

取消时返回 `ok: true`、`available: true`、`selected: false`、`cancelled: true` 和空 `path`。系统选择器不可用或选择结果不是可用本地目录时返回 `ok: false` 与 `error`，页面应保留手动输入绝对路径的回退。浏览器运行没有 `window.pywebview.api`，因此直接显示该回退说明即可。

返回的目录在 bridge 内经过与 `POST /api/workspace` 相同的绝对路径、目录存在性和符号链接/目录联接校验。前端把 `path` 回填到原有工作区切换表单，再提交原有接口；复制、打开、旧目录保留、待审批/执行中阻断和令牌轮换语义不变。
