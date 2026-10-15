@echo off
setlocal
set PYTHONUTF8=1
if defined AGENT_COACH_PYTHON (
  "%AGENT_COACH_PYTHON%" "%~dp0run.py" --workspace "%~dp0work" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0run.py" --workspace "%~dp0work" %*
) else (
  python "%~dp0run.py" --workspace "%~dp0work" %*
)
exit /b %ERRORLEVEL%
