@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "SETUP=%ROOT%\scripts\setup_opencomp.py"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
set "BOOTSTRAP_PY="

if not exist "%SETUP%" (
  echo OpenComp setup script was not found: "%SETUP%"
  exit /b 1
)

if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 set "BOOTSTRAP_PY="%VENV_PY%""
)

if not defined BOOTSTRAP_PY if defined OPENCOMP_PYTHON call :try_python "%OPENCOMP_PYTHON%"
if not defined BOOTSTRAP_PY call :try_python py -3.12
if not defined BOOTSTRAP_PY call :try_python py -3.11
if not defined BOOTSTRAP_PY call :try_python py -3
if not defined BOOTSTRAP_PY call :try_python python
if not defined BOOTSTRAP_PY call :try_python python3

if not defined BOOTSTRAP_PY (
  echo Python 3.11+ was not found.
  echo Install Python 3.11+ or set OPENCOMP_PYTHON to a Python executable.
  exit /b 1
)

echo OpenComp Studio install/check
echo Root: "%ROOT%"
echo Python: %BOOTSTRAP_PY%

call %BOOTSTRAP_PY% "%SETUP%" --ensure %*
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo OpenComp Studio dependencies are ready.
exit /b 0

:try_python
%* -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "BOOTSTRAP_PY=%*"
  exit /b 0
)
exit /b 1
