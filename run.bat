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

call "%ROOT%\install.bat" --backend-port %OPENCOMP_BACKEND_PORT% --frontend-port %OPENCOMP_FRONTEND_PORT% %*
if errorlevel 1 (
  echo.
  echo Install/check failed. OpenComp Studio was not started.
  exit /b %ERRORLEVEL%
)

set "BACKEND_RUNNER=%ROOT%\scripts\run_backend.bat"
set "FRONTEND_RUNNER=%ROOT%\scripts\run_frontend.bat"

if not exist "%BACKEND_RUNNER%" (
  echo Backend runner was not generated: "%BACKEND_RUNNER%"
  exit /b 1
)

if not exist "%FRONTEND_RUNNER%" (
  echo Frontend runner was not generated: "%FRONTEND_RUNNER%"
  exit /b 1
)

echo.
echo Starting backend and frontend...
start "OpenComp Backend" cmd /k ""%BACKEND_RUNNER%""
start "OpenComp Frontend" cmd /k ""%FRONTEND_RUNNER%""

echo.
echo OpenComp Studio is starting.
echo Frontend: http://127.0.0.1:%OPENCOMP_FRONTEND_PORT%
echo Backend health: http://127.0.0.1:%OPENCOMP_BACKEND_PORT%/api/health
echo.
echo Close the backend/frontend command windows to stop the app.

exit /b 0
