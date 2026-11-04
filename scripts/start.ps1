$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Install the Python virtual environment first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'node_modules'))) { throw 'Run npm.cmd install first.' }
$backendProcess = $null
Push-Location $projectRoot
try {
    $backendLog = Join-Path $projectRoot 'work\backend.log'
    $backendErrorLog = Join-Path $projectRoot 'work\backend-error.log'
    New-Item -ItemType Directory -Force (Join-Path $projectRoot 'work') | Out-Null
    $backendProcess = Start-Process -FilePath $projectPython -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $backendLog -RedirectStandardError $backendErrorLog
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($backendProcess.HasExited) { throw "Backend failed to start. See $backendErrorLog" }
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 15
            if ($health.status -eq 'ok') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 300 }
    }
    if (-not $ready) { throw "Backend did not become ready. See $backendErrorLog" }
    Write-Host 'Backend: http://127.0.0.1:8000 | Frontend: http://127.0.0.1:3000'
    Write-Host 'Stop with Ctrl+C. Backend logs are in work/.'
    & npm.cmd run dev
} finally {
    if ($backendProcess -and -not $backendProcess.HasExited) { Stop-Process -Id $backendProcess.Id -ErrorAction SilentlyContinue }
    Pop-Location
}
