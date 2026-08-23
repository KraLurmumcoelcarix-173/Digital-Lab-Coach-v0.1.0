@echo off
title Digital Lab Coach - uninstall
echo This removes Digital Lab Coach's local data folder:
echo   %USERPROFILE%\.dlc  (settings, machine-id cache, telemetry spool)
echo.
echo NOTE: your course usage limits live on the course server and are
echo keyed to this machine - uninstalling or re-downloading never
echo resets them.
echo.
set /p CONFIRM="Type YES to continue: "
if /i not "%CONFIRM%"=="YES" (echo Cancelled. & pause & exit /b 0)
rmdir /s /q "%USERPROFILE%\.dlc" 2>nul
echo Done. To finish, delete this whole folder in File Explorer.
pause
