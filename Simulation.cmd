@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала установите зависимости по README.md. Нужны Python и Chromium Playwright.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\simulation.py --open
pause
