#!/bin/zsh
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
build_env="$project_root/.venv-client-build-macos"
build_python="${PYTHON_BIN:-}"
if [[ -z "$build_python" ]] && command -v python3.11 >/dev/null 2>&1; then
  build_python="$(command -v python3.11)"
elif [[ -z "$build_python" ]] && [[ -x /opt/anaconda3/bin/python3 ]]; then
  build_python="/opt/anaconda3/bin/python3"
elif [[ -z "$build_python" ]]; then
  build_python="$(command -v python3)"
fi
"$build_python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'

cd "$project_root/frontend"
npm ci
npm run build

rm -rf "$build_env"
"$build_python" -m venv "$build_env"
"$build_env/bin/python" -m pip install --upgrade pip setuptools wheel
"$build_env/bin/python" -m pip install -r "$project_root/client/requirements-gui.txt" pyinstaller

cd "$project_root"
"$build_env/bin/python" -m PyInstaller \
  --clean \
  --noconfirm \
  --windowed \
  --name FileBackupClient \
  --icon "$project_root/assets/app-icon.icns" \
  --add-data "frontend/dist:frontend/dist" \
  --collect-all webview \
  --collect-submodules tzdata \
  --exclude-module fastapi \
  --exclude-module uvicorn \
  --exclude-module sqlalchemy \
  --exclude-module pytest \
  --exclude-module server \
  run_client_gui.py

"$build_env/bin/python" -m PyInstaller \
  --clean \
  --noconfirm \
  --name FileBackupClientAgent \
  --icon "$project_root/assets/app-icon.icns" \
  --collect-submodules tzdata \
  --exclude-module fastapi \
  --exclude-module uvicorn \
  --exclude-module sqlalchemy \
  --exclude-module pytest \
  --exclude-module server \
  run_client_agent.py

rm -rf "$project_root/build/FileBackupClient" "$project_root/build/FileBackupClientAgent"
rm -f "$project_root/FileBackupClient.spec" "$project_root/FileBackupClientAgent.spec"
print "Built macOS app: $project_root/dist/FileBackupClient.app"
print "Built macOS agent: $project_root/dist/FileBackupClientAgent/FileBackupClientAgent"
