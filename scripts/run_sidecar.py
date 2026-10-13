"""PyInstaller 打包入口：以绝对导入启动 sidecar。

main.py 位于包内使用相对导入，直接作为 PyInstaller 入口会报
"attempted relative import with no known parent package"；
此脚本放在包外，用绝对导入规避，行为与 `python -m coach.sidecar.main` 一致。
"""

from __future__ import annotations

from coach.sidecar.main import main

if __name__ == "__main__":
    main()
