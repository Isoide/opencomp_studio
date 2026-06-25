@echo off
setlocal EnableExtensions
set "ROOT=%~dp0.."
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined OPENCOMP_FRONTEND_PORT set "OPENCOMP_FRONTEND_PORT=5194"
if not defined OPENCOMP_BACKEND_PORT set "OPENCOMP_BACKEND_PORT=8000"
cd /d "%ROOT%\frontend"
set "VITE_OPENCOMP_API=http://127.0.0.1:%OPENCOMP_BACKEND_PORT%"
npm.cmd run dev -- --port %OPENCOMP_FRONTEND_PORT%
