param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$radarRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $radarRoot
$radarUrl = 'http://127.0.0.1:8765'
$env:RADAR_SIMULATION = '0'
$env:RADAR_VISUAL_PREVIEW = '0'
$env:RADAR_EMBEDDED_WORKER = '1'
try {
    $existing = Invoke-RestMethod "$radarUrl/api/health" -TimeoutSec 2
    if ($existing.app -eq 'book-radar') {
        $existingState = Invoke-RestMethod "$radarUrl/api/state" -TimeoutSec 2
        if ($existingState.server.simulation_enabled -or $existingState.server.preview_fixture -or -not $existingState.server.worker_enabled) {
            throw 'Этот процесс запущен в другом режиме. Для обычной панели со сборщиком запустите Radar.cmd.'
        }
        if (-not $NoBrowser) { Start-Process $radarUrl }; exit 0
    }
    throw 'Порт 8765 занят другой программой.'
} catch {
    if ($_.Exception.Message -like '*занят другой*' -or $_.Exception.Message -like '*в другом режиме*') { throw }
}
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Порт 8765 занят. Закройте использующую его программу.'
}
$radarPython = Join-Path $radarRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $radarPython)) {
    Write-Host 'Подготовка Python. Это нужно только при первом запуске.'
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Для первого запуска нужен Python 3.11 или новее.' }
    & $pythonCommand.Source -m venv (Join-Path $radarRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python.' }
}
$radarSetupStamp = Join-Path $radarRoot '.venv\radar-ready.txt'
$radarRequirementsHash = (Get-FileHash -LiteralPath (Join-Path $radarRoot 'requirements.txt') -Algorithm SHA256).Hash
if (-not (Test-Path -LiteralPath $radarSetupStamp) -or (Get-Content -LiteralPath $radarSetupStamp -Raw).Trim() -ne $radarRequirementsHash) {
    & $radarPython -m pip install -r (Join-Path $radarRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости.' }
    & $radarPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить Chromium.' }
    $radarRequirementsHash | Set-Content -LiteralPath $radarSetupStamp
}
New-Item -ItemType Directory -Force -Path (Join-Path $radarRoot 'data') | Out-Null
$process = Start-Process -FilePath $radarPython -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8765') -WorkingDirectory $radarRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $radarRoot 'data\server.stdout.log') -RedirectStandardError (Join-Path $radarRoot 'data\server.stderr.log')
$process.Id | Set-Content -LiteralPath (Join-Path $radarRoot 'data\server.pid')
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $health = Invoke-RestMethod "$radarUrl/api/health" -TimeoutSec 1; if ($health.app -eq 'book-radar') { if (-not $NoBrowser) { Start-Process $radarUrl }; exit 0 } } catch {}
    if ($process.HasExited) { break }
}
throw 'Сервер не запустился. Подробности в data\server.stderr.log.'
