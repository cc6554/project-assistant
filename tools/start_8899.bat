@echo off
rem 8899 → 17689 重定向服务（无窗口后台运行）
rem 用途：浏览器打开 http://127.0.0.1:8899/ 时自动跳到应用最新前端 17689
rem 用法：双击本文件，或开机后手动运行一次；应用(17689)未启动时重定向目标暂不可达属正常。
cd /d "%~dp0"
start "" pythonw.exe "%~dp0redirect_8899.py"
echo 8899 redirect service started. Press any key to close.
pause >nul
