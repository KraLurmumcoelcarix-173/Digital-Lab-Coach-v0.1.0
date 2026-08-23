@echo off
title Digital Lab Coach
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing the uv package manager - one-time setup...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo Could not install uv automatically. Please install it from
  echo https://docs.astral.sh/uv/ and run START_HERE.bat again.
  pause
  exit /b 1
)

echo Preparing packages - the first run can take a few minutes...
uv sync
if errorlevel 1 (
  echo.
  echo Package setup failed - check your internet connection and retry.
  pause
  exit /b 1
)

set DLC_ENFORCE_LIMITS=1
echo Starting Digital Lab Coach at http://127.0.0.1:8765 ...
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8765"
uv run python -m dlc.web.server
pause
