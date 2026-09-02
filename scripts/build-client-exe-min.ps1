$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Push-Location (Join-Path $root "frontend")
npm.cmd ci
npm.cmd run build
Pop-Location

$webviewRuntime = $env:FILEBACKUP_WEBVIEW2_RUNTIME_DIR
if (-not $webviewRuntime) {
  $runtimeRoot = Join-Path $root "build-tools\webview2-fixed-x64"
  $runtimeExe = Get-ChildItem `
    -LiteralPath $runtimeRoot `
    -Recurse `
    -File `
    -Filter "msedgewebview2.exe" `
    -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($runtimeExe) {
    $webviewRuntime = $runtimeExe.Directory.FullName
  }
}
if (-not $webviewRuntime -or -not (Test-Path -LiteralPath (Join-Path $webviewRuntime "msedgewebview2.exe"))) {
  throw "Microsoft WebView2 Fixed Version x64 runtime is required. Set FILEBACKUP_WEBVIEW2_RUNTIME_DIR to its directory."
}
$webviewExe = Join-Path $webviewRuntime "msedgewebview2.exe"
$webviewSignature = Get-AuthenticodeSignature -LiteralPath $webviewExe
if ($webviewSignature.Status -ne "Valid" -or $webviewSignature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
  throw "WebView2 runtime signature is not a valid Microsoft signature: $webviewExe"
}
$webviewData = "$webviewRuntime;webview2-runtime"
Write-Host "Bundling WebView2 Fixed Runtime: $($webviewSignature.Path)"
Write-Host "WebView2 version: $((Get-Item -LiteralPath $webviewExe).VersionInfo.FileVersion)"

$venv = Join-Path $root ".venv-client-build"
$python311 = $env:FILEBACKUP_PYTHON311
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ((-not $python311 -or -not (Test-Path -LiteralPath $python311)) -and $uv) {
  $python311 = (& $uv.Source python find 3.11 2>$null)
}
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $python311 = (& $pyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null)
  }
}
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  throw "Python 3.11 is required for the client build environment. Set FILEBACKUP_PYTHON311 to python.exe."
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
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClientAgent
Remove-Item -Force -ErrorAction SilentlyContinue dist\FileBackupClientAgent.exe

& $python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --onefile `
  --name FileBackupClient `
  --icon "assets\app-icon.ico" `
  --add-data "frontend\dist;frontend\dist" `
  --add-data $webviewData `
  --collect-all webview `
  --hidden-import pystray._win32 `
  --collect-submodules tzdata `
  --exclude-module fastapi `
  --exclude-module uvicorn `
  --exclude-module sqlalchemy `
  --exclude-module pydantic `
  --exclude-module pytest `
  --exclude-module server `
  run_client_gui.py

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClient
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupClientAgent
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupClient.spec
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupClientAgent.spec

Write-Host "Built Client executable with embedded --agent mode: $root\dist\FileBackupClient.exe"
