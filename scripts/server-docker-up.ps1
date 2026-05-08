$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".env")) {
  Copy-Item ".env.docker.example" ".env"
  Write-Host "Created .env from .env.docker.example. Edit tokens in .env before production use."
}

docker compose up --build -d file-backup-server
docker compose ps
Write-Host "Server: http://127.0.0.1:8000"
Write-Host "Health: http://127.0.0.1:8000/health"
