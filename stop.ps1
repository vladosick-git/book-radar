$ErrorActionPreference = 'Stop'
$radarRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $radarRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'Нет сохранённого процесса сервера.'; exit 0 }
$radarServerPid = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$radarServerPid"
if ($process -and $process.CommandLine -like '*uvicorn*backend.main:app*' -and $process.ExecutablePath -eq (Join-Path $radarRoot '.venv\Scripts\python.exe')) {
    Stop-Process -Id $radarServerPid
    Write-Host 'Сервер остановлен.'
} else { Write-Host 'Сохранённый процесс уже завершён или не принадлежит этой программе.' }
