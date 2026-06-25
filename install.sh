#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SETUP="$ROOT/scripts/setup_opencomp.py"
VENV_PY="$ROOT/.venv/bin/python"
BOOTSTRAP_PY=""

if [ ! -f "$SETUP" ]; then
  echo "OpenComp setup script was not found: $SETUP"
  exit 1
fi

try_python() {
  candidate=$1
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      BOOTSTRAP_PY=$candidate
      return 0
    fi
  fi
  return 1
}

if [ -x "$VENV_PY" ]; then
  if "$VENV_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    BOOTSTRAP_PY=$VENV_PY
  fi
fi

if [ -z "$BOOTSTRAP_PY" ] && [ "${OPENCOMP_PYTHON:-}" ]; then
  if "${OPENCOMP_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    BOOTSTRAP_PY=${OPENCOMP_PYTHON}
  fi
fi

[ -n "$BOOTSTRAP_PY" ] || try_python python3.12 || true
[ -n "$BOOTSTRAP_PY" ] || try_python python3.11 || true
[ -n "$BOOTSTRAP_PY" ] || try_python python3 || true
[ -n "$BOOTSTRAP_PY" ] || try_python python || true

if [ -z "$BOOTSTRAP_PY" ]; then
  echo "Python 3.11+ was not found."
  echo "Install Python 3.11+ or set OPENCOMP_PYTHON to a Python executable."
  exit 1
fi

echo "OpenComp Studio install/check"
echo "Root: $ROOT"
echo "Python: $BOOTSTRAP_PY"

"$BOOTSTRAP_PY" "$SETUP" --ensure "$@"

echo
echo "OpenComp Studio dependencies are ready."
