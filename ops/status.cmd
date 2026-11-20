@echo off
REM Quick status check for the Contract Review local backend.
REM Pure ASCII + CRLF on purpose - cmd.exe reads .cmd as ANSI(936) here.
setlocal enabledelayedexpansion
set "ROOT=%~dp0.."
set "PGPORT=%PGPORT%"
if not defined PGPORT set "PGPORT=5434"
set "APP_PORT=%APP_PORT%"
if not defined APP_PORT set "APP_PORT=8790"
set "LOGS=%ROOT%\logs"

echo === Contract Review local backend ===
netstat -ano | findstr /C:":%PGPORT%" /C:":%APP_PORT%" | findstr LISTENING
echo.

if exist "%LOGS%\supervisor.lock" (
  set /p SPID=<"%LOGS%\supervisor.lock"
  echo supervisor: running, pid=!SPID!
) else (
  echo supervisor: NOT running
)
if exist "%LOGS%\watchdog.lock" (
  set /p WPID=<"%LOGS%\watchdog.lock"
  echo watchdog:    running, pid=!WPID!
) else (
  echo watchdog:    NOT running
)

echo.
echo --- /api/health ---
curl.exe -s --max-time 5 http://127.0.0.1:%APP_PORT%/api/health
echo.
echo.
echo --- public URL (what the phone reaches) ---
if defined PUBLIC_BASE_URL (echo   %PUBLIC_BASE_URL%) else (echo   not configured - set PUBLIC_BASE_URL in .env)
echo.
echo --- LAN IPv4 (may change; set a DHCP reservation) ---
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4"') do echo   now:%%A

echo.
echo logs: %LOGS%\watchdog.log , %LOGS%\supervisor.log
endlocal
