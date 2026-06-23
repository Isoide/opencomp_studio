from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_LOG_LOCK = threading.Lock()


def perf_log_path() -> Path | None:
    raw = os.getenv("OPENCOMP_PERF_LOG_PATH", "").strip()
    if not raw:
        return None
    return Path(raw)


def perf_phase_logging_enabled() -> bool:
    return os.getenv("OPENCOMP_PERF_LOG_PHASES", "").strip().lower() in {"1", "true", "yes", "on"}


def perf_preview_logging_enabled() -> bool:
    return os.getenv("OPENCOMP_PERF_LOG_PREVIEWS", "").strip().lower() in {"1", "true", "yes", "on"}


def emit_perf_event(event_type: str, payload: dict[str, Any]) -> None:
    path = perf_log_path()
    if path is None:
        return
    record = {"event": event_type, "timestamp": time.time(), **payload}
    line = json.dumps(record, default=str)
    with _LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
