$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller `
  --noconsole `
  --onefile `
  --name TradingBackupClient `
  --hidden-import pystray._win32 `
  --hidden-import PIL._tkinter_finder `
  run_client_gui.py

Write-Host "Built GUI executable: $root\dist\TradingBackupClient.exe"
