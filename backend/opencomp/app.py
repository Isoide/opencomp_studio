"""FastAPI application assembly for the OpenComp backend.

This module wires together the initial project state, evaluator, scheduler, and
HTTP routes into one backend application instance. It keeps startup behavior
small and explicit so launcher and test code can reuse the same app factory.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from opencomp.api.app_state import RUNTIME_STATE_KEY, build_runtime_state
from opencomp.api.routes import router


def initialize_app_state(app: FastAPI) -> None:
    """Populate FastAPI state with the backend services required by routes."""

    setattr(app.state, RUNTIME_STATE_KEY, build_runtime_state())


def create_app() -> FastAPI:
    app = FastAPI(title="OpenComp Studio", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    initialize_app_state(app)
    app.include_router(router)
    return app


app = create_app()
