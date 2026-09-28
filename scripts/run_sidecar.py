"""PyInstaller 打包入口：以绝对导入启动 sidecar。

main.py 位于包内使用相对导入，直接作为 PyInstaller 入口会报
"attempted relative import with no known parent package"；
此脚本放在包外，用绝对导入规避，行为与 `python -m coach.sidecar.main` 一致。
"""

from __future__ import annotations

import uvicorn

from coach.sidecar.main import PORT, app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
