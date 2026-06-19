from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from opencomp.api.routes import router
from opencomp.core.defaults import create_default_project
from opencomp.core.evaluator import GraphEvaluator


def create_app() -> FastAPI:
    app = FastAPI(title="OpenComp Studio", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    project = create_default_project()
    app.state.project = project
    app.state.evaluator = GraphEvaluator(settings=project.settings)
    app.state.evaluator_settings_key = project.settings.model_dump_json()
    app.state.graph_revision = 0
    app.include_router(router)
    return app


app = create_app()
