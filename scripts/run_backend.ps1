$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot ".."))
$backend = Join-Path $root "backend"
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\\python.exe"
$logs = Join-Path $backend ".logs"

if (-not $env:OPENCOMP_BACKEND_PORT) {
  $env:OPENCOMP_BACKEND_PORT = "8000"
}

New-Item -ItemType Directory -Force -Path $logs | Out-Null
Set-Location $backend
$env:PYTHONPATH = $backend
$env:VIRTUAL_ENV = $venv

& $python -m uvicorn opencomp.app:app --host 127.0.0.1 --port $env:OPENCOMP_BACKEND_PORT *>&1 |
  Tee-Object -FilePath (Join-Path $logs "uvicorn.launch.log")
"exit $LASTEXITCODE" | Out-File (Join-Path $logs "uvicorn.launch.exit.log")
