@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
py -3.12 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto fail
:run
".venv\Scripts\python.exe" tools\at_onboarding.py --open %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo Не удалось запустить пилот AT. Сохраните текст ошибки из этого окна.
pause
exit /b 1
