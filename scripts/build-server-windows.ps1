$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Push-Location (Join-Path $root "frontend")
npm.cmd ci
npm.cmd run build
Pop-Location

$venv = Join-Path $root ".venv-server-build"
$python311 = (py -3.11 -c "import sys; print(sys.executable)" 2>$null)
if (-not $python311) {
  throw "Python 3.11 is required for the Server build environment."
}
if (-not (Test-Path $venv)) {
  & $python311 -m venv $venv
}

$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --upgrade pip setuptools wheel
& $python -m pip install -r server\requirements-app.txt pyinstaller

& $python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --onefile `
  --name FileBackupServer `
  --icon "assets\app-icon.ico" `
  --add-data "frontend\dist;frontend\dist" `
  --collect-all webview `
  --collect-submodules uvicorn `
  run_server_app.py

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupServer
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupServer.spec
Write-Host "Built Server executable: $root\dist\FileBackupServer.exe"
