$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

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
  --hidden-import pystray._win32 `
  --hidden-import PIL._tkinter_finder `
  --collect-submodules tzdata `
  --exclude-module fastapi `
  --exclude-module uvicorn `
  --exclude-module sqlalchemy `
  --exclude-module pydantic `
  --exclude-module pytest `
  --exclude-module server `
  run_client_gui.py

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClient
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupClient.spec

Write-Host "Built GUI executable: $root\dist\FileBackupClient.exe"
