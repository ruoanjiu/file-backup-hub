$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Checking Windows build prerequisites..." -ForegroundColor Cyan

$python311 = $env:FILEBACKUP_PYTHON311
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $python311 = (& $pyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null)
  }
}
if (-not $python311 -or -not (Test-Path -LiteralPath $python311)) {
  throw "Python 3.11 x64 is required. Set FILEBACKUP_PYTHON311 to python.exe if py.exe is unavailable."
}
$python = (& $python311 -c "import platform,sys; print(sys.executable); print(platform.architecture()[0]); print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ($python.Count -lt 3 -or $python[2] -ne "3.11") {
  throw "Python 3.11 is required. Detected: $($python[2])"
}
if ($python[1] -ne "64bit") {
  throw "Python 3.11 must be the x64 build. Detected: $($python[1])"
}
$env:FILEBACKUP_PYTHON311 = $python[0]

$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) {
  throw "Node.js x64 with npm is required. Install an active Node.js LTS release."
}

Write-Host "Python: $($python[0])"
Write-Host "Node: $(node --version)"
Write-Host "npm: $(npm --version)"

Write-Host "Building Client with embedded Agent mode..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "build-client-exe-min.ps1")

Write-Host "Building Server Manager and Server runtime..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "build-server-windows.ps1")

$artifacts = @(
  (Join-Path $root "dist\FileBackupClient.exe"),
  (Join-Path $root "dist\FileBackupServer.exe")
)

foreach ($artifact in $artifacts) {
  if (-not (Test-Path $artifact)) {
    throw "Expected build artifact was not created: $artifact"
  }
}

$hashLines = foreach ($artifact in $artifacts) {
  $hash = (Get-FileHash -Algorithm SHA256 $artifact).Hash.ToLowerInvariant()
  "$hash  $([System.IO.Path]::GetFileName($artifact))"
}
$hashPath = Join-Path $root "dist\SHA256SUMS.txt"
$hashLines | Set-Content -Path $hashPath -Encoding ascii

Write-Host ""
Write-Host "Windows build completed successfully." -ForegroundColor Green
foreach ($artifact in $artifacts) {
  $item = Get-Item $artifact
  Write-Host ("  {0}  {1:N1} MB" -f $item.FullName, ($item.Length / 1MB))
}
Write-Host "  $hashPath"
