@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not defined OPENCOMP_BACKEND_PORT set "OPENCOMP_BACKEND_PORT=8000"
if not defined OPENCOMP_FRONTEND_PORT set "OPENCOMP_FRONTEND_PORT=5173"

echo OpenComp Studio launcher
echo Backend port: %OPENCOMP_BACKEND_PORT%
echo Frontend port: %OPENCOMP_FRONTEND_PORT%
echo.

title OpenComp Studio

call "%ROOT%\install.bat" --backend-port %OPENCOMP_BACKEND_PORT% --frontend-port %OPENCOMP_FRONTEND_PORT% %*
if errorlevel 1 (
  echo.
  echo Install/check failed. OpenComp Studio was not started.
  exit /b %ERRORLEVEL%
)

set "APP_RUNNER=%ROOT%\scripts\run_opencomp.bat"

if not exist "%APP_RUNNER%" (
  echo App runner was not generated: "%APP_RUNNER%"
  exit /b 1
)

echo.
echo Starting OpenComp Studio in a single console...
call "%APP_RUNNER%"

echo.
echo OpenComp Studio stopped.

exit /b 0
