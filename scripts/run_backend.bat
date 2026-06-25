@echo off
setlocal EnableExtensions
set "ROOT=%~dp0.."
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined OPENCOMP_BACKEND_PORT set "OPENCOMP_BACKEND_PORT=8036"
cd /d "%ROOT%\backend"
set PYTHONPATH="%ROOT%\backend"
"%ROOT%\.venv\Scripts\python.exe" -m uvicorn opencomp.app:app --host 127.0.0.1 --port %OPENCOMP_BACKEND_PORT%
