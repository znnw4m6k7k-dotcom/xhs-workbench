@echo off
rem Stop the workbench on Windows
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 stop.py %*) else (python stop.py %*)
