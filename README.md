# OpenComp Studio

Local browser-based compositor prototype with a Python/FastAPI backend and React/Vite frontend.

## Run

Windows one-step launcher:

```bat
run.bat
```

`run.bat` calls `install.bat` first. The install step creates or reuses `.venv`, installs backend/frontend dependencies only when missing or stale, writes `scripts\run_backend.bat` and `scripts\run_frontend.bat`, then launches both services.

To install/check without starting the app:

```bat
install.bat
```

Optional Windows port overrides:

```bat
set OPENCOMP_BACKEND_PORT=8010
set OPENCOMP_FRONTEND_PORT=5174
run.bat
```

Cross-platform setup helper:

```bash
python scripts/setup_opencomp.py
```

This creates `.venv`, installs backend dependencies, installs frontend dependencies, and writes OS-specific runner scripts in `scripts/`.

To set up and run immediately:

```bash
python scripts/setup_opencomp.py --run
```

Backend:

```bash
cd backend
python -m pip install -e ".[dev,ocio,exr]"
python -m uvicorn opencomp.app:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev -- --port 5173
```

Open `http://127.0.0.1:5173`.

## Current MVP

- Versioned project and node graph models.
- Float32 RGBA `ImageFrame` core.
- OCIO engine using PyOpenColorIO/opencolorio with processor caching.
- PNG/JPG and basic EXR reading.
- Preview PNG encoding.
- Read, Grade, Colorspace, Reformat, Scale, Transform, Merge, Viewer, and Write nodes.
- Demand-driven graph evaluator with cycle detection.
- Viewer API returning `image/png`.
- Canvas-based browser node graph, viewer, inspector, frame controls, and configurable script-menu placeholder.
