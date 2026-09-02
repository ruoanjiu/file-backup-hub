@echo off
setlocal
cd /d "%~dp0"
echo Building File Backup for Windows x64...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build-windows-all.ps1"
if errorlevel 1 (
  echo.
  echo BUILD FAILED. Review the error above.
  pause
  exit /b 1
)
echo.
echo BUILD SUCCEEDED. Outputs are in the dist folder.
pause
