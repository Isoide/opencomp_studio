@echo off
cd /d "E:\PROJECTS\opencomp_studio\backend"
set PYTHONPATH=E:\PROJECTS\opencomp_studio\backend
"E:\PROJECTS\opencomp_studio\.venv\Scripts\python.exe" -m uvicorn opencomp.app:app --host 127.0.0.1 --port 8000
