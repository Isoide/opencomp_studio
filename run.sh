#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

: "${OPENCOMP_BACKEND_PORT:=8000}"
: "${OPENCOMP_FRONTEND_PORT:=5173}"

echo "OpenComp Studio launcher"
echo "Backend port: $OPENCOMP_BACKEND_PORT"
echo "Frontend port: $OPENCOMP_FRONTEND_PORT"
echo

"$ROOT/install.sh" --backend-port "$OPENCOMP_BACKEND_PORT" --frontend-port "$OPENCOMP_FRONTEND_PORT" "$@"

APP_RUNNER="$ROOT/scripts/run_opencomp.sh"
if [ ! -f "$APP_RUNNER" ]; then
  echo "App runner was not generated: $APP_RUNNER"
  exit 1
fi

echo
echo "Starting OpenComp Studio..."
"$APP_RUNNER"

echo
echo "OpenComp Studio stopped."
