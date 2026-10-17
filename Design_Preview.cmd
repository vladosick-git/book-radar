@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала установите зависимости по README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\design_preview.py --open
pause
