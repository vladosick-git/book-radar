@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\local_panel.py --stop
if errorlevel 1 pause
