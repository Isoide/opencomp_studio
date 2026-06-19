from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and optionally run OpenComp Studio.")
    parser.add_argument("--python", default=sys.executable, help="Python executable to use for the backend venv.")
    parser.add_argument("--venv", default=str(VENV), help="Virtual environment path.")
    parser.add_argument("--backend-only", action="store_true", help="Only set up backend dependencies.")
    parser.add_argument("--frontend-only", action="store_true", help="Only set up frontend dependencies.")
    parser.add_argument("--minimal", action="store_true", help="Install backend without dev/OCIO/EXR extras.")
    parser.add_argument("--ensure", action="store_true", help="Only install dependencies when missing or stale.")
    parser.add_argument("--force-install", action="store_true", help="Install dependencies even when --ensure thinks they are current.")
    parser.add_argument("--skip-install", action="store_true", help="Skip dependency installation.")
    parser.add_argument("--run", action="store_true", help="Run backend and frontend after setup.")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=5173)
    args = parser.parse_args()

    system = platform.system().lower()
    print(f"OpenComp Studio setup")
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Root: {ROOT}")

    if args.backend_only and args.frontend_only:
        parser.error("Use only one of --backend-only or --frontend-only.")

    setup_backend = not args.frontend_only
    setup_frontend = not args.backend_only
    venv = Path(args.venv).resolve()

    if setup_backend:
        ensure_python(args.python)
        if not args.skip_install:
            create_venv(args.python, venv)
            if args.force_install or not args.ensure or backend_needs_install(venv, minimal=args.minimal):
                install_backend(venv, minimal=args.minimal)
                write_backend_stamp(venv, minimal=args.minimal)
            else:
                print("Backend dependencies are already installed.")
        write_backend_runner(venv, args.backend_port, system)

    if setup_frontend:
        ensure_npm()
        if not args.skip_install:
            if args.force_install or not args.ensure or frontend_needs_install():
                install_frontend()
                write_frontend_stamp()
            else:
                print("Frontend dependencies are already installed.")
        write_frontend_runner(args.frontend_port, system)

    print()
    print("Setup complete.")
    print_run_instructions(args.backend_port, args.frontend_port, system)

    if args.run:
        return run_app(venv, args.backend_port, args.frontend_port)
    return 0


def ensure_python(python: str) -> None:
    result = subprocess.run(
        [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Python executable is not usable: {python}")
    major_text, minor_text = result.stdout.strip().split(".", 1)
    major, minor = int(major_text), int(minor_text)
    if (major, minor) < (3, 11):
        print("Warning: the current Python is older than 3.11. Use --python to point at Python 3.11+.")


def ensure_npm() -> None:
    npm = npm_command()
    if shutil.which(npm) is None:
        raise SystemExit("npm was not found on PATH. Install Node.js 20+ or add npm to PATH.")


def create_venv(python: str, venv: Path) -> None:
    if venv.exists():
        print(f"Using existing venv: {venv}")
        return
    print(f"Creating venv: {venv}")
    run([python, "-m", "venv", str(venv)])


def install_backend(venv: Path, minimal: bool) -> None:
    python = venv_python(venv)
    extras = "" if minimal else "[dev,ocio,exr]"
    print("Installing backend dependencies...")
    run([str(python), "-m", "pip", "install", "-e", f".{extras}"], cwd=BACKEND, venv=venv)


def install_frontend() -> None:
    print("Installing frontend dependencies...")
    run([npm_command(), "install"], cwd=FRONTEND)


def backend_needs_install(venv: Path, minimal: bool) -> bool:
    python = venv_python(venv)
    if not python.exists():
        return True
    return not stamp_is_fresh(
        backend_stamp(venv),
        sources=[BACKEND / "pyproject.toml", ROOT / "scripts" / "setup_opencomp.py"],
        expected=backend_stamp_text(minimal),
    )


def frontend_needs_install() -> bool:
    if not (FRONTEND / "node_modules").exists():
        return True
    sources = [FRONTEND / "package.json", FRONTEND / "package-lock.json"]
    return not stamp_is_fresh(frontend_stamp(), sources=sources, expected=frontend_stamp_text())


def stamp_is_fresh(path: Path, sources: list[Path], expected: str) -> bool:
    if not path.exists():
        return False
    try:
        if path.read_text(encoding="utf-8") != expected:
            return False
        stamp_mtime = path.stat().st_mtime
        return all(not source.exists() or source.stat().st_mtime <= stamp_mtime for source in sources)
    except OSError:
        return False


def write_backend_stamp(venv: Path, minimal: bool) -> None:
    path = backend_stamp(venv)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(backend_stamp_text(minimal), encoding="utf-8")


def write_frontend_stamp() -> None:
    path = frontend_stamp()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontend_stamp_text(), encoding="utf-8")


def backend_stamp(venv: Path) -> Path:
    return venv / ".opencomp_backend_installed"


def frontend_stamp() -> Path:
    return FRONTEND / "node_modules" / ".opencomp_frontend_installed"


def backend_stamp_text(minimal: bool) -> str:
    return f"backend=minimal:{int(minimal)}\n"


def frontend_stamp_text() -> str:
    return "frontend=npm-install\n"


def run_app(venv: Path, backend_port: int, frontend_port: int) -> int:
    print()
    print("Starting OpenComp Studio. Press Ctrl+C to stop both services.")
    backend = subprocess.Popen(
        [
            str(venv_python(venv)),
            "-m",
            "uvicorn",
            "opencomp.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
        ],
        cwd=BACKEND,
        env=clean_env(venv),
    )
    frontend = subprocess.Popen(
        [npm_command(), "run", "dev", "--", "--port", str(frontend_port)],
        cwd=FRONTEND,
        env=clean_env(venv),
    )
    try:
        print(f"Frontend: http://127.0.0.1:{frontend_port}")
        while True:
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                return backend_code
            if frontend_code is not None:
                return frontend_code
            try:
                backend.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        terminate(frontend)
        terminate(backend)
        return 0


def write_backend_runner(venv: Path, port: int, system: str) -> None:
    if system == "windows":
        path = ROOT / "scripts" / "run_backend.bat"
        path.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    f"cd /d {quote_win(BACKEND)}",
                    f"set PYTHONPATH={BACKEND}",
                    f"{quote_win(venv_python(venv))} -m uvicorn opencomp.app:app --host 127.0.0.1 --port {port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        path = ROOT / "scripts" / "run_backend.sh"
        path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env sh",
                    "set -eu",
                    f"cd {sh_quote(BACKEND)}",
                    f"PYTHONPATH={sh_quote(BACKEND)} {sh_quote(venv_python(venv))} -m uvicorn opencomp.app:app --host 127.0.0.1 --port {port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)
    print(f"Wrote backend runner: {path}")


def write_frontend_runner(port: int, system: str) -> None:
    if system == "windows":
        path = ROOT / "scripts" / "run_frontend.bat"
        path.write_text(
            "\r\n".join(
                [
                    "@echo off",
                    f"cd /d {quote_win(FRONTEND)}",
                    f"{npm_command()} run dev -- --port {port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    else:
        path = ROOT / "scripts" / "run_frontend.sh"
        path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env sh",
                    "set -eu",
                    f"cd {sh_quote(FRONTEND)}",
                    f"{npm_command()} run dev -- --port {port}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)
    print(f"Wrote frontend runner: {path}")


def print_run_instructions(backend_port: int, frontend_port: int, system: str) -> None:
    print()
    print("Run backend:")
    print("  scripts\\run_backend.bat" if system == "windows" else "  ./scripts/run_backend.sh")
    print("Run frontend:")
    print("  scripts\\run_frontend.bat" if system == "windows" else "  ./scripts/run_frontend.sh")
    print(f"Open: http://127.0.0.1:{frontend_port}")
    print(f"Backend health: http://127.0.0.1:{backend_port}/api/health")


def venv_python(venv: Path) -> Path:
    if platform.system().lower() == "windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def npm_command() -> str:
    return "npm.cmd" if platform.system().lower() == "windows" else "npm"


def clean_env(venv: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.lower() == "path" and "PATH" in env:
            continue
        env[key] = value
    path_key = "Path" if platform.system().lower() == "windows" else "PATH"
    old_path = env.get(path_key) or env.get("PATH") or env.get("Path") or ""
    venv_bin = venv / ("Scripts" if platform.system().lower() == "windows" else "bin")
    env[path_key] = str(venv_bin) + os.pathsep + old_path
    env["VIRTUAL_ENV"] = str(venv)
    env["PYTHONPATH"] = str(BACKEND)
    return env


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def run(
    command: list[str],
    cwd: Path | None = None,
    check: bool = True,
    venv: Path = VENV,
) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(part) for part in command))
    result = subprocess.run(command, cwd=cwd, env=clean_env(venv))
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def quote_win(path: Path) -> str:
    return f'"{path}"'


def sh_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
