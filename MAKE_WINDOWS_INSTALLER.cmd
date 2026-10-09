@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0scripts\build_windows_release.ps1" (
  echo.
  echo ERROR: Tertium's build files are incomplete at:
  echo   %~dp0
  echo.
  echo If this came from a ZIP, extract the entire folder first.
  echo.
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows_release.ps1" -InstallerOnly
set "BUILD_RC=%ERRORLEVEL%"
if not "%BUILD_RC%"=="0" (
  echo.
  echo Tertium installer build failed with exit code %BUILD_RC%.
  echo See the messages above. No installer should be treated as complete.
  pause
  exit /b %BUILD_RC%
)

echo.
echo Installer verified successfully.
echo Your file is:
echo   release\TertiumModManager-Setup-x64.exe
echo.
start "" "%~dp0release"
pause
