@echo off
REM Stop the watchdog chain before maintenance (upgrades, manual restarts).
REM The three services keep running by design; only auto-restart is turned off.
REM Pure ASCII + CRLF on purpose - cmd.exe reads .cmd as ANSI(936) here.
setlocal enabledelayedexpansion
set "ROOT=%~dp0.."
set "LOGS=%ROOT%\logs"

echo === Stopping Contract Review watchdog ===

REM Kill the supervisor FIRST. Its whole job is to respawn watchdog.mjs,
REM so killing watchdog alone would just bring it back five seconds later.
if exist "%LOGS%\supervisor.lock" (
  set /p SPID=<"%LOGS%\supervisor.lock"
  echo supervisor pid=!SPID! -^> taskkill
  taskkill /PID !SPID! /F >nul 2>&1
  del "%LOGS%\supervisor.lock" >nul 2>&1
) else (
  echo supervisor: no lock found
)

if exist "%LOGS%\watchdog.lock" (
  set /p WPID=<"%LOGS%\watchdog.lock"
  echo watchdog   pid=!WPID! -^> taskkill
  taskkill /PID !WPID! /F >nul 2>&1
  del "%LOGS%\watchdog.lock" >nul 2>&1
) else (
  echo watchdog:   no lock found
)

echo.
echo Auto-restart is now OFF. Services already running stay up.
echo To start the chain again, run contract-watchdog.cmd.
endlocal
