# OpenComp Studio Architecture

The backend owns the renderable graph contract. The frontend canvas is a client editor for that contract, not the source of truth for evaluation.

## Backend

- FastAPI app: `backend/opencomp/app.py`
- API routes: `backend/opencomp/api/routes.py`
- Project and graph models: `backend/opencomp/core/models.py`
- Demand-driven evaluator: `backend/opencomp/core/evaluator.py`
- Color engine: `backend/opencomp/color/ocio_engine.py`
- Image adapters: `backend/opencomp/io/`
- Node operations: `backend/opencomp/nodes/`

Internal image data is NumPy `float32`, shape `H x W x 4`, RGBA. Viewer display transforms operate on a copy and encode display-ready PNG bytes.

## Frontend

- React/Vite entry: `frontend/src/main.tsx`
- App shell: `frontend/src/App.tsx`
- API client: `frontend/src/api/client.ts`
- Canvas graph editor: `frontend/src/nodegraph/CanvasNodeGraph.tsx`
- Viewer: `frontend/src/viewer/ViewerPanel.tsx`
- Inspector: `frontend/src/inspector/Inspector.tsx`
- Zustand store: `frontend/src/store/appStore.ts`

The MVP graph flows visually from top to bottom. Plugin/menu customization is represented in project data and the top script menu; actual Python script execution should be added behind a backend permission and sandbox policy.
