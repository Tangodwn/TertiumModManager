@echo off
cd /d "%~dp0"
python -m pytest -q
if errorlevel 1 (
  echo.
  echo Tests failed.
  pause
  exit /b 1
)
echo.
echo All tests passed.
pause
