@echo off
REM Contract Review App backend watchdog launcher.
REM Placed in the user's Startup folder; runs at every logon.
REM It only starts the supervisor in a minimized window. The supervisor
REM restarts watchdog.mjs 5s after any exit, and watchdog.mjs keeps
REM PostgreSQL (5434), FastAPI (8790) and the analysis worker alive.
REM
REM NOTE: keep this file pure ASCII + CRLF. cmd.exe reads .cmd as ANSI(936)
REM on this machine and an LF-only file gets split mid-line.
REM NOTE: when this file is copied to the Startup folder, %~dp0 points there,
REM not at the project. Set CONTRACT_ROOT to the handoff directory in that case.
setlocal
if defined CONTRACT_ROOT (set "ROOT=%CONTRACT_ROOT%") else (set "ROOT=%~dp0..")
set "NODE=%CONTRACT_NODE%"
if not defined NODE set "NODE=node.exe"
set "ERRLOG=%ROOT%\logs\startup-error.log"

if not exist "%NODE%" (
  echo %DATE% %TIME% Missing node: %NODE%>> "%ERRLOG%"
  exit /b 1
)
if not exist "%ROOT%\watchdog-supervisor.mjs" (
  echo %DATE% %TIME% Missing supervisor script.>> "%ERRLOG%"
  exit /b 1
)
if not exist "%ROOT%\contract-watchdog-launch.vbs" (
  echo %DATE% %TIME% Missing VBScript launcher.>> "%ERRLOG%"
  exit /b 1
)
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

REM VBScript with window style 0 = fully hidden. `start /min` used to leave a
REM permanent console box on the taskbar (and flash it on each watchdog exit).
wscript.exe //Nologo "%ROOT%\contract-watchdog-launch.vbs"
endlocal
