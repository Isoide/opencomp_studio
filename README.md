# OpenComp Studio

Local browser-based compositor prototype with a Python/FastAPI backend and React/Vite frontend.

## Run

Windows one-step launcher:

```bat
run.bat
```

`run.bat` calls `install.bat` first. The install step creates or reuses `.venv`, installs backend/frontend dependencies only when missing or stale, writes `scripts\run_opencomp.bat` plus the per-service runners, then launches OpenComp Studio in a single visible console.

To install/check without starting the app:

```bat
install.bat
```

Linux one-step launchers:

```bash
./install.sh
./run.sh
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

This creates `.venv`, installs backend dependencies, installs frontend dependencies, and writes OS-specific runner scripts in `scripts/`, including a combined OpenComp launcher.

To set up and run immediately:

```bash
python scripts/setup_opencomp.py --run
```

Preferred ports are `8000` for the backend and `5173` for the frontend. If either port is already in use, the launcher scans forward for the next free port automatically and prints the resolved URLs.

Backend:

```bash
cd backend
python -m pip install -e ".[dev,ocio,exr,oiio,vulkan]"
python -m uvicorn opencomp.app:app --host 127.0.0.1 --port 8000
```

EXR reader and writer backends:

- `OpenImageIO` is the preferred EXR reader and writer when installed.
- legacy `OpenEXR` remains available as the fallback backend.
- exact single-channel EXR reads still keep the legacy fast path.
- runtime setting: `ProjectSettings.image_io_backend = "auto" | "oiio" | "openexr"`

OCIO execution:

- `OpenImageIO` is the preferred backend execution path for colorspace conversion and display/view transforms when it is available.
- PyOpenColorIO CPU processors remain the compatibility fallback.

Useful backend benchmarks:

```bash
python backend/scripts/benchmark_viewer_pipeline.py --help
python backend/scripts/benchmark_backend_matrix.py --help
python backend/scripts/benchmark_oiio_ocio_conversion.py --help
```

Optional Vulkan shader compilation for Windows and Linux:

```bash
python backend/scripts/build_vulkan_shaders.py --check
python backend/scripts/build_vulkan_shaders.py
```

The runtime discovers `glslangValidator` or `glslc` from `VULKAN_SDK`, `PATH`, or from `OPENCOMP_VULKAN_SHADER_COMPILER` if you need to point at a non-standard install. If a studio needs host-specific fallback locations, `OPENCOMP_VULKAN_SHADER_SEARCH_ROOTS` can provide one or more search roots using the platform path separator. The intended production flow is to build SPIR-V once, ship the compiled artifacts, and let end-user machines run without the shader compiler installed. Native Vulkan device init is cross-platform for Windows and Linux; iOS/macOS support is deferred to a future MoltenVK roadmap item.

If users do not have the Vulkan SDK compiler on the machine, OpenComp can fall back to a Houdini-bundled `glslang`/`glslangValidator` install. That path should be treated as a warning-level fallback, not the preferred primary compiler source.

Full roadmap and current status:

- [docs/vulkan_backend.md](/G:/PIPELINE_DEVELOPMENTS/GIT/opencomp_studio/docs/vulkan_backend.md)

That roadmap also carries the post-Vulkan OpenGL+OCIO spike, using OCIO's `pyociodisplay.py` as a reference for shader extraction and GPU display-path patterns rather than direct code ingestion.

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
