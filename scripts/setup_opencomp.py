"""OpenComp Studio setup and launcher helper.

This script owns the local developer install flow for the backend venv, frontend
dependencies, and OS-specific runner scripts. It keeps the launch behavior
cross-platform, resolves free runtime ports when preferred ports are busy, and
starts backend/frontend child processes with matching environment wiring.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from setup_opencomp_support import (
    BACKEND,
    DEFAULT_BACKEND_PORT,
    DEFAULT_FRONTEND_PORT,
    DEFAULT_PORT_SEARCH_LIMIT,
    FRONTEND,
    ROOT,
    VENV,
    app_runner_lines,
    backend_runner_lines,
    choose_runtime_port,
    clean_env,
    ephemeral_free_port,
    frontend_runner_lines,
    npm_command,
    platform_id,
    print_run_instructions,
    quote_win,
    resolve_runtime_ports,
    run_app,
    runner_path_reference,
    sh_quote,
    venv_python,
    write_app_runner,
    write_backend_runner,
    write_frontend_runner,
)


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    system = platform_id()
    print("OpenComp Studio setup")
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
        ensure_npm(system)
        if not args.skip_install:
            if args.force_install or not args.ensure or frontend_needs_install():
                install_frontend(system)
                write_frontend_stamp()
            else:
                print("Frontend dependencies are already installed.")
        write_frontend_runner(args.frontend_port, system)

    write_app_runner(venv, args.backend_port, args.frontend_port, system)

    print()
    print("Setup complete.")
    print_run_instructions(args.backend_port, args.frontend_port, system)

    if args.run:
        return run_app(venv, args.backend_port, args.frontend_port, args.port_search_limit, system)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI parser used by setup and local app launch."""

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
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)
    parser.add_argument(
        "--port-search-limit",
        type=int,
        default=DEFAULT_PORT_SEARCH_LIMIT,
        help="How many consecutive ports to probe before falling back to an ephemeral free port.",
    )
    return parser

def ensure_python(python: str) -> None:
    """Verify the requested Python executable is usable and warn about old versions."""

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


def ensure_npm(system: str | None = None) -> None:
    """Verify npm is available before attempting frontend installation or launch."""

    npm = npm_command(system)
    if shutil.which(npm) is None:
        raise SystemExit("npm was not found on PATH. Install Node.js 20+ or add npm to PATH.")


def create_venv(python: str, venv: Path) -> None:
    """Create the backend virtual environment when it does not already exist."""

    if venv.exists():
        print(f"Using existing venv: {venv}")
        return
    print(f"Creating venv: {venv}")
    run([python, "-m", "venv", str(venv)])


def install_backend(venv: Path, minimal: bool) -> None:
    """Install editable backend dependencies into the selected virtual environment."""

    python = venv_python(venv)
    extras = "" if minimal else "[dev,ocio,exr,oiio,vulkan]"
    print("Installing backend dependencies...")
    run([str(python), "-m", "pip", "install", "-e", f".{extras}"], cwd=BACKEND, venv=venv)


def install_frontend(system: str | None = None) -> None:
    """Install frontend dependencies with the system-appropriate npm entrypoint."""

    print("Installing frontend dependencies...")
    run([npm_command(system), "install"], cwd=FRONTEND)


def backend_needs_install(venv: Path, minimal: bool) -> bool:
    """Return True when backend install state is missing or stale."""

    python = venv_python(venv)
    if not python.exists():
        return True
    return not stamp_is_fresh(
        backend_stamp(venv),
        sources=[BACKEND / "pyproject.toml", ROOT / "scripts" / "setup_opencomp.py"],
        expected=backend_stamp_text(minimal),
    )


def frontend_needs_install() -> bool:
    """Return True when node_modules is missing or the install stamp is stale."""

    if not (FRONTEND / "node_modules").exists():
        return True
    sources = [FRONTEND / "package.json", FRONTEND / "package-lock.json"]
    return not stamp_is_fresh(frontend_stamp(), sources=sources, expected=frontend_stamp_text())


def stamp_is_fresh(path: Path, sources: list[Path], expected: str) -> bool:
    """Compare an install stamp against the expected marker text and source mtimes."""

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
    """Persist the backend install marker so --ensure can skip redundant installs."""

    path = backend_stamp(venv)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(backend_stamp_text(minimal), encoding="utf-8")


def write_frontend_stamp() -> None:
    """Persist the frontend install marker so --ensure can skip redundant installs."""

    path = frontend_stamp()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontend_stamp_text(), encoding="utf-8")


def backend_stamp(venv: Path) -> Path:
    """Return the backend install stamp path for the selected virtual environment."""

    return venv / ".opencomp_backend_installed"


def frontend_stamp() -> Path:
    """Return the frontend install stamp path inside node_modules."""

    return FRONTEND / "node_modules" / ".opencomp_frontend_installed"


def backend_stamp_text(minimal: bool) -> str:
    """Encode the backend install mode so stamp changes invalidate stale installs."""

    return f"backend=minimal:{int(minimal)}\n"


def frontend_stamp_text() -> str:
    """Encode the frontend install marker used by --ensure."""

    return "frontend=npm-install\n"


def run(
    command: list[str],
    cwd: Path | None = None,
    check: bool = True,
    venv: Path = VENV,
    system: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess with the same sanitized environment used by the launcher."""

    print("+ " + " ".join(str(part) for part in command))
    result = subprocess.run(command, cwd=cwd, env=clean_env(venv, system))
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def quote_win(path: Path) -> str:
    """Quote a filesystem path for Windows batch scripts."""

    return f'"{path}"'


def sh_quote(path: Path) -> str:
    """Quote a filesystem path for POSIX shell scripts."""

    return "'" + str(path).replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
