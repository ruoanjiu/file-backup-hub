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

$venv = $env:FILEBACKUP_SERVER_BUILD_VENV
if (-not $venv) {
  $venv = Join-Path $root ".venv-server-build"
}
$python311 = $env:FILEBACKUP_PYTHON311
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $python311 = (& $pyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null)
  }
}
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  throw "Python 3.11 is required for the Server build environment. Set FILEBACKUP_PYTHON311 to python.exe."
}
if (-not (Test-Path $venv)) {
  & $python311 -m venv $venv
}

$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --upgrade pip setuptools wheel
& $python -m pip install -r server\requirements-app.txt pyinstaller

$runningServer = @(Get-Process FileBackupServer -ErrorAction SilentlyContinue)
if ($runningServer.Count -gt 0) {
  throw "FileBackupServer.exe is still running. Close process IDs $($runningServer.Id -join ', ') before rebuilding."
}

& $python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --onefile `
  --name FileBackupServer `
  --icon "assets\app-icon.ico" `
  --add-data "frontend\dist;frontend\dist" `
  --add-data $webviewData `
  --collect-all webview `
  --hidden-import pystray._win32 `
  --collect-submodules uvicorn `
  run_server_app.py
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller Server build failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath (Join-Path $root "dist\FileBackupServer.exe"))) {
  throw "PyInstaller did not create dist\FileBackupServer.exe."
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\FileBackupServer
Remove-Item -Force -ErrorAction SilentlyContinue FileBackupServer.spec
Write-Host "Built Server executable: $root\dist\FileBackupServer.exe"
