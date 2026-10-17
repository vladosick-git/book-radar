@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" tools\local_panel.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Не удалось запустить показ рекламы. Сохраните текст ошибки из этого окна.
pause
exit /b 1
