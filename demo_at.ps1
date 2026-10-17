param([switch]$NoBrowser, [switch]$Stop, [int]$Port = 8780)
$ErrorActionPreference = 'Stop'
$radarDemoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $radarDemoRoot
$radarDemoData = Join-Path $radarDemoRoot 'data\at-demo'
$radarDemoUrl = "http://127.0.0.1:$Port"
$radarDemoPidFile = Join-Path $radarDemoData 'server.pid'
$radarDemoHealth = $null
try { $radarDemoHealth = Invoke-RestMethod "$radarDemoUrl/demo/status" -TimeoutSec 2 } catch {}
if ($radarDemoHealth) {
    if ($radarDemoHealth.purpose -ne 'at-demo-v1' -or $radarDemoHealth.root -ne $radarDemoRoot -or $radarDemoHealth.data -ne $radarDemoData) {
        throw 'This port belongs to another app or demo. Nothing was stopped.'
    }
    if (-not $Stop) {
        if ($radarDemoHealth.version -ne '0.7.4') { throw 'Demo version differs. Stop it with Stop_Demo_AT.cmd, then launch again.' }
        if (-not $NoBrowser) { Start-Process $radarDemoUrl }
        exit 0
    }
} elseif ($Stop) { Write-Host 'Demo is not running.'; exit 0 }
if ($Stop) {
    $radarDemoState = Invoke-RestMethod "$radarDemoUrl/api/state" -TimeoutSec 3
    if ($radarDemoState.server.running_jobs.Count -or $radarDemoState.server.open_profiles.Count) { throw 'Finish collection and close the profile window before stopping the demo.' }
    if (-not (Test-Path -LiteralPath $radarDemoPidFile)) { throw 'Demo PID file is missing. No process was stopped.' }
    'stop' | Set-Content -LiteralPath (Join-Path $radarDemoData 'stop.request')
    for ($radarDemoAttempt = 0; $radarDemoAttempt -lt 60; $radarDemoAttempt++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
            Write-Host 'Demo stopped. Data is preserved.'
            exit 0
        }
    }
    throw 'Graceful shutdown is still in progress. No other process was stopped.'
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is occupied. Nothing was stopped." }
$radarDemoPython = Join-Path $radarDemoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $radarDemoPython)) {
    & python -m venv (Join-Path $radarDemoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
$radarDemoHash = (Get-FileHash -LiteralPath 'requirements.txt' -Algorithm SHA256).Hash
$radarDemoStamp = Join-Path $radarDemoRoot '.venv\radar-ready.txt'
if (-not (Test-Path -LiteralPath $radarDemoStamp) -or (Get-Content -LiteralPath $radarDemoStamp -Raw).Trim() -ne $radarDemoHash) {
    & $radarDemoPython -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $radarDemoPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
    $radarDemoHash | Set-Content -LiteralPath $radarDemoStamp
}
New-Item -ItemType Directory -Path $radarDemoData -Force | Out-Null
$env:RADAR_DATA_DIR = $radarDemoData
$env:RADAR_MODE = 'local'
$env:RADAR_AUTH = '0'
$env:RADAR_COOKIE_SECURE = '0'
$env:RADAR_EMBEDDED_WORKER = '1'
$env:RADAR_MAX_BROWSERS = '1'
$env:RADAR_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:RADAR_VAULT_KEY_FILE = ''
$env:RADAR_OCR = 'tesseract'
$env:RADAR_OCR_LANGS = 'rus+eng'
$radarDemoTessdata = Join-Path $env:LOCALAPPDATA 'Tesseract-OCR\tessdata'
if (Test-Path -LiteralPath (Join-Path $radarDemoTessdata 'rus.traineddata')) { $env:RADAR_TESSDATA_DIR = $radarDemoTessdata }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$radarDemoProcess = Start-Process -FilePath $radarDemoPython -ArgumentList @('-m','tools.at_demo','--port',"$Port") -WorkingDirectory $radarDemoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $radarDemoData 'server.stdout.log') -RedirectStandardError (Join-Path $radarDemoData 'server.stderr.log')
$radarDemoProcess.Id | Set-Content -LiteralPath $radarDemoPidFile
for ($radarDemoAttempt = 0; $radarDemoAttempt -lt 60; $radarDemoAttempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $radarDemoHealth = Invoke-RestMethod "$radarDemoUrl/demo/status" -TimeoutSec 1
        if ($radarDemoHealth.purpose -eq 'at-demo-v1' -and $radarDemoHealth.root -eq $radarDemoRoot -and $radarDemoHealth.data -eq $radarDemoData) {
            Write-Host "AT demo: $radarDemoUrl"
            if (-not $NoBrowser) { Start-Process $radarDemoUrl }
            exit 0
        }
    } catch {}
    if ($radarDemoProcess.HasExited) { break }
}
throw 'Demo did not start. See data/at-demo/server.stderr.log.'
