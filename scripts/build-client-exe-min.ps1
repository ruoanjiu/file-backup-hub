$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Push-Location (Join-Path $root "frontend")
npm.cmd ci
npm.cmd run build
Pop-Location

$venv = Join-Path $root ".venv-client-build"
$python311 = $null
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
  $python311 = (& $uv.Source python find 3.11 2>$null)
}
if (-not $python311) {
  $python311 = (py -3.11 -c "import sys; print(sys.executable)" 2>$null)
}
if (-not $python311) {
  throw "Python 3.11 is required for the client build environment."
}

if (-not (Test-Path $venv)) {
  & $python311 -m venv $venv
}

$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --upgrade pip setuptools wheel
& $python -m pip install -r client\requirements-gui.txt
& $python -m pip install pyinstaller

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClient
Remove-Item -Force -ErrorAction SilentlyContinue dist\FileBackupClient.exe

& $python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --onefile `
  --name FileBackupClient `
  --icon "assets\app-icon.ico" `
  --add-data "frontend\dist;frontend\dist" `
  --collect-all webview `
  --collect-submodules tzdata `
  --exclude-module fastapi `
  --exclude-module uvicorn `
  --exclude-module sqlalchemy `
  --exclude-module pydantic `
  --exclude-module pytest `
  --exclude-module server `
  run_client_gui.py

& $python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --onefile `
  --name FileBackupClientAgent `
  --icon "assets\app-icon.ico" `
  --collect-submodules tzdata `
  --exclude-module fastapi `
  --exclude-module uvicorn `
  --exclude-module sqlalchemy `
  --exclude-module pydantic `
  --exclude-module pytest `
  --exclude-module server `
  run_client_agent.py

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClient
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClientAgent
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupClient.spec
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupClientAgent.spec

Write-Host "Built GUI executable: $root\dist\FileBackupClient.exe"
Write-Host "Built background agent: $root\dist\FileBackupClientAgent.exe"
