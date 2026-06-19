Set-Location 'E:\PROJECTS\opencomp_studio\backend'
$env:PYTHONPATH = 'E:\PROJECTS\opencomp_studio\backend'
$env:VIRTUAL_ENV = 'E:\PROJECTS\opencomp_studio\.venv'
$env:Path = 'E:\PROJECTS\opencomp_studio\.venv\Scripts;C:\Users\Michal\.cache\codex-runtimes\codex-primary-runtime\dependencies\python;C:\Windows\System32;C:\Windows;C:\Windows\System32\Wbem'
& 'E:\PROJECTS\opencomp_studio\.venv\Scripts\python.exe' -m uvicorn opencomp.app:app --host 127.0.0.1 --port 8000 *>&1 |
  Tee-Object -FilePath 'E:\PROJECTS\opencomp_studio\backend\.logs\uvicorn.launch.log'
"exit $LASTEXITCODE" | Out-File 'E:\PROJECTS\opencomp_studio\backend\.logs\uvicorn.launch.exit.log'
