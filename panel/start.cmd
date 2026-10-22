@echo off
rem Start the workbench on Windows (double-click or run from Codex)
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 start.py %*) else (python start.py %*)
