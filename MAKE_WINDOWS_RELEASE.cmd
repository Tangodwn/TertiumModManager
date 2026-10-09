@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0scripts\build_windows_release.ps1" (
  echo.
  echo ERROR: Tertium's build files are incomplete at:
  echo   %~dp0
  echo.
  echo This usually happens when MAKE_WINDOWS_RELEASE.cmd is run from inside the ZIP preview.
  echo Right-click the Tertium ZIP, choose Extract All, then run this file from the extracted folder.
  echo.
  pause
  exit /b 2
)

if not exist "%~dp0requirements-dev.txt" (
  echo ERROR: requirements-dev.txt is missing. Re-extract the complete Tertium package.
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows_release.ps1"
set "BUILD_RC=%ERRORLEVEL%"
if not "%BUILD_RC%"=="0" (
  echo.
  echo Tertium Windows release build failed with exit code %BUILD_RC%.
  echo See the messages above. No release should be treated as complete.
  pause
  exit /b %BUILD_RC%
)

echo.
echo Build verified successfully.
echo Opening the release folder containing the installer and portable ZIP.
start "" "%~dp0release"
pause
