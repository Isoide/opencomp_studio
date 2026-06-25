# OpenComp Agent Notes

## App Startup

- Use `python scripts/setup_opencomp.py --run` for the normal launcher path.
- The launcher is expected to find free ports automatically when the defaults are busy.
- For isolated debugging, prefer a dedicated backend/frontend pair such as `8001/5174` with logs redirected into `.codex_debug/`.

## Frontend API Hygiene

- Prefer frontend-native helpers for viewer settings, frame transport metadata, and cache/runtime status instead of repeating raw nested payload chains.
- Prefer viewer-specific helper modules such as `viewerFrame.ts` and `viewerMetadata.ts` for routine display geometry, bbox, pixel-aspect, and proxy-detection logic instead of recomputing those rules inside components.
- On the backend API side, keep route modules thin. Request shaping, warm/preload planning, and other non-transport viewer orchestration should live in helper modules such as `viewer_context.py`, `viewer_transport.py`, or `viewer_requests.py` instead of accumulating in `routes.py`.
- Treat float viewer headers, tile-source selection, and request timing payload formatting as a separate concern from request planning. Those pieces should live in a dedicated helper surface such as `viewer_float.py`, not inline inside websocket/HTTP route handlers.
- Treat websocket float-stream send loops as their own concern as well. Header/data tile emission, cancellation checkpoints, and byte/timing accumulation should live in a dedicated async helper such as `viewer_float_stream.py`, leaving route handlers responsible only for request orchestration and lifecycle.
- Treat `project.settings.*`, `status.gpu_runtime.*`, and `gpuFrame.header.*` as transport/internal structures. When the same nested read appears in multiple places, promote it into a helper or selector.
- Prefer direct helpers such as `projectPath(...)`, `suggestedProjectPath(...)`, `suggestedNukePath(...)`, `nodeMetadataResolutionLabel(...)`, and `nodeMetadataDisplayViewLabel(...)` over repeating nested reads for routine save-path or viewer metadata UI.
- Keep project save/export path rules in dedicated helpers such as `projectFiles.ts` instead of embedding filename extension or backend-path detection logic inline in `App.tsx`.
- Keep recurring project preference defaults in selectors such as `projectPreferences.ts` instead of repeating `project.preferences.* ?? default` across render and warm paths.
- Keep viewer runtime defaults such as frame bounds, tile sizing, backend warm limits, and playback warm limits in a dedicated helper surface such as `projectRuntime.ts` instead of scattering fallback math across `App.tsx`.
- Keep compare-mode request planning, compare-input selection, and viewer request timing shaping in a dedicated helper surface such as `viewerCompare.ts` instead of repeating branchy compare logic across warm, fallback, and telemetry paths.
- Keep viewer result bookkeeping such as timing-history trimming, request-timing payload shaping, and result-mode selection in a dedicated helper surface such as `viewerResult.ts` instead of rebuilding those rules inline in `App.tsx`.
- For frontend viewer work, keep React components focused on state, effects, and event orchestration. Move pure geometry math, pixel decoding, and canvas drawing helpers into dedicated `viewer*` utility modules instead of letting `ViewerPanel.tsx` absorb them.
- Prefer frontend unit tests for pure helpers and selectors (`project*`, `viewer*`, formatting, path helpers). Reserve app startup, route wiring, and render/cache behavior for integration tests so failures point to the right layer.
- For backend API work, keep `routes.py` focused on viewer/render transport concerns. Move node introspection, project CRUD, or other stable subdomains into dedicated route modules once a route cluster starts carrying its own validation/evaluation helpers.
- For startup and installer work, keep `setup_opencomp.py` as a thin orchestration entrypoint. Put pure runner generation, port resolution, quoting, and environment shaping in a dedicated helper module so those rules stay unit-testable without booting the app.
- As a rule of thumb, avoid new callsites that need more than `2-3` property hops for routine viewer information such as proxy size, display/view selection, frame dimensions, or runtime activity.
- If basic viewer information feels buried, expose a direct accessor first before adding more nested reads in components.
- Do not seed runtime UI defaults with machine-specific filesystem paths. Example/demo paths are acceptable in docs, fixtures, or opt-in local tests, but new runtime nodes should default to portable values such as `builtin://gradient` or explicit user input.

## Frontend Smoke Checklist

Run these checks after every 4-5 meaningful app changes that touch startup, viewer transport, caching, render scheduling, error handling, OCIO, or GPU execution.

### Error comp

Reference file: `G:\PIPELINE_DEVELOPMENTS\GIT\opencomp_studio\LAL_105_523_0010_slapcomp_test.opencomp`

- Load the comp and wait 5-10 seconds.
- Confirm the viewer shows the node error overlay and frame strip error label.
- Confirm the project does not reset back to a default comp.
- Confirm the app settles after the initial error.
- Confirm there are no repeated `POST /api/reads/warm` requests after the error is known.
- Confirm there are no repeated `POST /api/viewer/warm` requests after the error is known.
- Confirm viewer-frame retries stop after the initial failure path settles.
- Confirm cache status stops changing once the error state is stable.
- Confirm node errors remain visible and do not flicker away.
- Confirm node graph error highlighting still points to the failing branch.

### Good local comp

Reference file: `G:\PIPELINE_DEVELOPMENTS\GIT\opencomp_studio\test_projects\local_read_viewer_check.opencomp`

- Load the comp and confirm frame `1001` renders.
- Confirm the viewer image appears without an error overlay.
- Confirm frame `1002` renders when scrubbing or typing the frame number.
- Return to frame `1001` and confirm the frame is reused from cache when expected.
- Confirm backend warm requests occur only after a successful frame render.
- Confirm cache HUD or cache status grows in a plausible way instead of looping.
- Confirm the viewer does not emit repeated abort or reconnect behavior while idle.
- Confirm the script tab and graph state remain stable after frame changes.

## Browser Benchmark Procedure

- Keep one in-app browser tab active during measurement.
- Navigate the tab to `about:blank` before resetting isolated logs to avoid background traffic.
- Record backend log line counts before each run.
- Open the target app URL and wait a fixed 5 seconds.
- Count:
  - `GET /api/cache/status`
  - `GET /api/nodes/Viewer1/metadata`
  - `POST /api/reads/warm`
  - `POST /api/viewer/warm`
  - accepted `/ws/viewer/float`
  - accepted `/ws/viewer/frame`
- Wait another 5-10 seconds and confirm the counts stop increasing.

## Skill Ideas

- `opencomp-browser-smoke`: load reference comps, run the frontend checklist, and summarize request counts from isolated logs.
- `opencomp-backend-benchmark`: run backend-only frame benchmarks for read, OCIO, node chains, transport packing, and cache reuse.
- `opencomp-launcher-debug`: start isolated backend/frontend pairs with smart ports, hidden windows, and captured logs.
- `opencomp-ocio-benchmark`: compare OCIO CPU paths, proxy paths, OIIO colorconvert paths, and viewer transport overhead.
- `opencomp-error-regression`: load known broken comps and verify that the app settles instead of looping.
- `opencomp-project-fixtures`: generate or refresh small `.opencomp` regression fixtures for browser and backend testing.
