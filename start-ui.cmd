@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PROJECT_DIR=%~dp0"
set "PYTHONUTF8=1"
set "FORWARD_ARGS=%*"
set "HAS_CONFIG=0"
set "HAS_DEMO=0"

:scan_args
if "%~1"=="" goto choose_python
if /I "%~1"=="--config" set "HAS_CONFIG=1"
set "CURRENT_ARG=%~1"
if /I "%CURRENT_ARG:~0,9%"=="--config=" set "HAS_CONFIG=1"
if /I "%~1"=="--demo" set "HAS_DEMO=1"
shift
goto scan_args

:choose_python
if defined AGENT_COACH_PYTHON (
  set "PYTHON_EXE=%AGENT_COACH_PYTHON%"
) else if exist "%PROJECT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
) else if exist "F:\Doubao\job-radar-coach\.venv\Scripts\python.exe" (
  set "PYTHON_EXE=F:\Doubao\job-radar-coach\.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

set "DEFAULT_ARGS=--workspace "%PROJECT_DIR%work""
if not "%HAS_CONFIG%"=="0" goto run_web
if not "%HAS_DEMO%"=="0" goto run_web
if exist "%PROJECT_DIR%config\providers.yaml" goto use_config
set "DEFAULT_ARGS=%DEFAULT_ARGS% --demo"
goto run_web

:use_config
set "DEFAULT_ARGS=%DEFAULT_ARGS% --config "%PROJECT_DIR%config\providers.yaml""

:run_web
"%PYTHON_EXE%" "%PROJECT_DIR%run-web.py" %DEFAULT_ARGS% %FORWARD_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%
