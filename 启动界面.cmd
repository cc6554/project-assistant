@echo off
chcp 65001 >nul
title 简历项目助手
call "%~dp0start-ui.cmd" %*
exit /b %ERRORLEVEL%
