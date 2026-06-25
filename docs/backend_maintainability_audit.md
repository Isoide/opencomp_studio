# Backend Maintainability Audit

This audit is incremental. It records the current readability and structure
state of backend Python modules as the cleanup passes progress, starting with
startup and initialization code.

## Scoring

Scores are on a `1-10` scale.

- `discoverability`: how easy it is to find the right module/function.
- `readability`: how easy it is to understand local logic without cross-jumping.
- `structure`: how well responsibilities are separated and reusable.

## Pass 1: Startup / Initialization

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/app.py` | 9 | 9 | 9 | Clear app factory. State initialization is now isolated in `initialize_app_state()`. |
| `backend/opencomp/core/defaults.py` | 8 | 8 | 8 | Default project wiring is easy to follow. Remaining downside is the large inline graph fixture, which may later deserve smaller builders. |
| `backend/opencomp/io/path_utils.py` | 9 | 9 | 9 | Good single-purpose utility module. Token expansion rules are centralized and covered by tests. |
| `backend/opencomp/cli.py` | 7 | 7 | 7 | Functional and mostly clean, but still long. It would benefit from extracting render/report actions into smaller helpers or subcommands. |
| `backend/opencomp/core/models.py` | 8 | 8 | 8 | Schemas are centralized and coherent. The file is doing the right job, but it is becoming large as more runtime settings accumulate. |
| `scripts/setup_opencomp.py` | 7 | 7 | 8 | Better than before: runner generation, port selection, and environment handling are now testable and more portable. Still a large multipurpose script and the main candidate for future splitting. |

## What Improved In This Pass

- Startup defaults can now use `OPENCOMP_REFERENCE_SEQUENCE` instead of relying only on one hard-coded reference path.
- Generated runner scripts now prefer runtime `ROOT`-relative paths for in-repo resources, reducing machine-specific absolute path embedding.
- App state setup is isolated from FastAPI assembly, which makes the entrypoint easier to scan and easier to reuse in tests.
- Missing top-level module descriptions were added to key backend files.

## Main Remaining Problems

- `scripts/setup_opencomp.py` still owns install flow, launch flow, script generation, environment preparation, and port resolution in one file.
- `backend/opencomp/cli.py` is still a large command handler rather than a thin router over smaller command modules.
- `backend/opencomp/core/defaults.py` still contains a full inline starter graph definition; that is acceptable for now, but it will become cumbersome as defaults grow.

## Recommended Next Cleanup Targets

1. Split launcher concerns out of `scripts/setup_opencomp.py` into pure helpers such as `launch_env`, `runner_templates`, and `port_selection`.
2. Break `backend/opencomp/cli.py` into smaller action-oriented helpers or subcommand modules.
3. Audit evaluator/render modules for repeated request-timing and cache-status shaping logic.
4. Continue adding concise module docstrings only where file purpose is not immediately obvious.

## Pass 2: Image IO / OCIO Runtime

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/io/image_reader.py` | 7 | 7 | 7 | Still large, but backend selection is now less repetitive and RGBA assembly is less duplicated. Best next step would be splitting EXR-specific helpers into a dedicated module. |
| `backend/opencomp/io/image_writer.py` | 8 | 8 | 8 | Clearer after sharing backend-selection helpers. Raster and EXR writing paths are reasonably easy to follow. |
| `backend/opencomp/io/backend_support.py` | 9 | 9 | 9 | Good single-purpose module. It removes repeated optional-import and backend-normalization logic cleanly. |
| `backend/opencomp/color/ocio_engine.py` | 7 | 7 | 8 | Diagnostics and OIIO fallback paths are cleaner now, but the class is still dense and contains several responsibilities: config loading, CPU transforms, OIIO transforms, and GPU shader export. |

## What Improved In This Pass

- Shared EXR backend normalization and optional import logic moved into `backend/opencomp/io/backend_support.py`.
- Reader and writer no longer each own their own backend-name parsing and optional-import wrappers.
- EXR RGBA assembly in the reader is less repetitive.
- OCIO engine now centralizes diagnostics warning generation and the optional OIIO-to-CPU fallback pattern.

## Main Remaining Problems

- `backend/opencomp/io/image_reader.py` is still too large for comfortable human scanning.
- `backend/opencomp/color/ocio_engine.py` still combines multiple concerns in one class.
- IO/color tests are present and valuable, but the wider test suite still needs clearer unit vs integration organization.

## Recommended Next Cleanup Targets

1. Split EXR-specific read helpers out of `backend/opencomp/io/image_reader.py`.
2. Separate OCIO config/materialization concerns from transform execution concerns in `backend/opencomp/color/ocio_engine.py`.
3. Start formalizing test boundaries with explicit unit/integration grouping metadata.

## Pass 3: Reader Split / Path Hygiene / Viewer Context

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/io/image_reader.py` | 8 | 8 | 8 | Now a clearer high-level dispatch module instead of a mixed dispatch-plus-EXR implementation file. |
| `backend/opencomp/io/image_reader_exr.py` | 8 | 7 | 8 | Large, but appropriately focused. EXR-specific logic is at least isolated behind one entrypoint now. |
| `backend/opencomp/io/image_reader_support.py` | 9 | 9 | 9 | Small metadata helpers with a clear purpose. |
| `backend/opencomp/api/viewer_context.py` | 9 | 9 | 9 | Good compact API-facing helper. It exposes routine viewer information without repeated nested settings reads. |
| `backend/opencomp/core/defaults.py` | 9 | 9 | 9 | Improved further by removing the baked-in studio UNC startup plate. Host-specific startup media is now opt-in through environment configuration. |
| `backend/tests/conftest.py` | 8 | 8 | 8 | Central unit/integration grouping is better than scattered per-file markers. |

## What Improved In This Pass

- EXR-heavy reader logic moved out of `backend/opencomp/io/image_reader.py` into a dedicated module.
- Startup defaults no longer embed a hard-coded `\\skynet\...` reference sequence path.
- Node metadata now includes a compact viewer-context payload so common viewer facts do not require deep traversal through project settings.
- Test collection now applies explicit unit/integration markers centrally.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` still performs many direct `request.app.state.*` reads and writes; it would benefit from a typed app-state helper layer.
- `backend/opencomp/io/image_reader_exr.py` is still dense and may later want a smaller split between OIIO and OpenEXR implementations.
- Vulkan shader compiler discovery still contains explicit platform search roots for Windows fallback installs. That is acceptable operationally, but the policy should keep living behind one helper rather than spreading.

## Recommended Next Cleanup Targets

1. Add a typed backend app-state accessor layer so routes stop mutating raw `app.state` attributes directly.
2. Continue flattening frontend/backend viewer information behind compact accessors instead of repeated `project.settings.*` chains.
3. Keep platform-specific tool discovery centralized and configurable through environment variables before adding more fallback locations.

## Pass 4: Runtime State / Viewer Settings Hydration

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/app_state.py` | 9 | 9 | 9 | New single-purpose runtime-state helper module. It turns implicit `app.state` conventions into a typed, explicit API. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved by moving repeated runtime lookups and graph-revision mutation behind helpers. Still a large transport module, but less stateful and easier to scan. |
| `backend/opencomp/app.py` | 9 | 9 | 9 | Startup state initialization is now one structured install instead of several ad hoc attributes. |
| `frontend/src/projectSettings.ts` | 9 | 9 | 9 | Better viewer-settings surface. Default display/view hydration now lives beside other viewer-setting selectors. |
| `frontend/src/App.tsx` | 7 | 8 | 7 | Slightly cleaner after centralizing viewer default hydration. It remains the largest frontend orchestration file and still deserves future splitting. |

## What Improved In This Pass

- Backend runtime services now live behind `backend/opencomp/api/app_state.py` instead of being spread across raw `app.state` attributes.
- Route handlers no longer manually rebuild evaluator cache-budget logic or graph-revision mutation in as many places.
- Frontend viewer default hydration now lives in one helper instead of being repeated in boot, load, and new-project flows.
- Additional unit coverage now exists for typed runtime-state behavior.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` still mixes a very broad set of concerns: project CRUD, viewer transport, render jobs, scripting, and diagnostics.
- `frontend/src/App.tsx` still owns too much orchestration and is the next major readability hotspot on the frontend side.
- A few path/config constants remain intentionally centralized for dev/runtime discovery, especially localhost launcher URLs and Vulkan compiler search roots.

## Recommended Next Cleanup Targets

1. Split `backend/opencomp/api/routes.py` by concern, likely into project, viewer, render, and scripting route modules.
2. Continue carving selectors/controller helpers out of `frontend/src/App.tsx`, especially around viewer transport orchestration.
3. Review centralized path/tool discovery helpers and document which hard-coded roots are deliberate operational fallbacks versus candidates for removal.

## Pass 5: Vulkan Toolchain Discovery / Path Policy

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/gpu/toolchain.py` | 9 | 9 | 9 | New focused helper module for compiler discovery, fallback search roots, and warning policy. This is the right home for path-sensitive GPU toolchain behavior. |
| `backend/opencomp/gpu/runtime.py` | 8 | 8 | 8 | Improved by deleting duplicated compiler search logic. It still remains a very large module, but one less cross-cutting concern lives there now. |
| `backend/scripts/build_vulkan_shaders.py` | 8 | 8 | 8 | Cleaner after reusing runtime discovery policy instead of owning a second implementation. |

## What Improved In This Pass

- Vulkan shader compiler discovery now lives in one shared helper instead of being duplicated between runtime and build tooling.
- Host-specific fallback roots are now centralized and can be overridden through `OPENCOMP_VULKAN_SHADER_SEARCH_ROOTS`.
- Runtime and build-script warning behavior now stay aligned for Houdini-bundled fallback toolchains.
- Developer docs now expose the configurable fallback-root mechanism explicitly.

## Main Remaining Problems

- `backend/opencomp/gpu/runtime.py` is still large and mixes device setup, diagnostics, cache policy, native dispatch, and shader-module management.
- Native shader-manifest validation still lives inside the runtime class and could later move into its own validation helper.
- Vulkan benchmark coverage is still mostly targeted/manual rather than a dedicated perf test layer.

## Recommended Next Cleanup Targets

1. Split `backend/opencomp/gpu/runtime.py` into smaller modules such as toolchain, native device/context, and cache/dispatch helpers.
2. Keep broad backend route splitting as the next non-Vulkan structural priority.
3. Add a dedicated fast Vulkan/toolchain unit suite separate from broader native compute integration checks.

## Pass 6: API Route Decomposition

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/context.py` | 9 | 9 | 9 | Good shared service-access layer. It makes route modules less stateful and reduces repeated backend wiring. |
| `backend/opencomp/api/project_routes.py` | 9 | 8 | 8 | Clear focused module for project, graph, script, and scripting concerns. This was the highest-value first split from the API monolith. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved meaningfully. It is still large because viewer/render/websocket transport remains dense, but the project/session CRUD layer is no longer mixed into it. |

## What Improved In This Pass

- Project/session/script routes now live in their own dedicated module.
- Shared API service access moved into a reusable context helper instead of being embedded in the main route file.
- The main `routes.py` file is now more accurately focused on viewer/render/runtime transport concerns.
- Existing backend tests for project/session routes and viewer endpoints still pass after the split.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` is still the single largest backend readability hotspot because preview transport, tile streaming, render jobs, and websocket behavior all still live together.
- Viewer helper functions inside `routes.py` remain numerous and would benefit from a second extraction pass, likely into preview/streaming helper modules.
- Some integration-style backend tests are still relatively slow even when targeted, which weakens the unit/integration distinction.

## Recommended Next Cleanup Targets

1. Extract viewer/preview helper logic from `backend/opencomp/api/routes.py` into smaller transport helper modules.
2. Continue tightening the test split by keeping fast route/helper tests isolated from heavier viewer integration coverage.
3. Keep frontend `App.tsx` as the next frontend orchestration cleanup target after the backend viewer transport layer is less monolithic.

## Pass 7: Path Hygiene / Native Accessors

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/context.py` | 9 | 9 | 9 | Better after adding a direct active-graph accessor. It removes one of the most repeated project-to-script traversal patterns from route code. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Slightly clearer now that graph access goes through a named helper instead of repeated `get_active_script(project).graph` chains. |
| `frontend/src/projectSettings.ts` | 9 | 9 | 9 | Now acts as a broader project/viewer selector layer, not just viewer-settings hydration. |
| `frontend/src/App.tsx` | 8 | 8 | 7 | Improved by reusing active-script selectors instead of open-coded `script_tabs.find(...)` patterns. It is still large, but the local intent is easier to read. |
| `frontend/src/store/appStore.ts` | 8 | 8 | 8 | Runtime path hygiene improved by removing the machine-specific default `Read` path. |

## What Improved In This Pass

- Runtime code no longer seeds new frontend `Read` nodes with a hard-coded local filesystem path.
- Repeated active-script graph traversal in the backend API now goes through a direct helper.
- Frontend active-script lookups now use small selectors instead of repeating array searches and fallback rules.
- Agent notes now explicitly distinguish acceptable fixture/example paths from unacceptable runtime defaults.

## Main Remaining Problems

- Some tracked example/session artifacts in the repository still contain host-specific paths; those are not part of runtime defaults, but they should stay clearly labeled as local fixtures or be normalized later.
- `frontend/src/App.tsx` still combines too many concerns despite selector cleanup.
- `backend/opencomp/api/routes.py` still has a dense viewer/render transport surface even after helper extraction.

## Recommended Next Cleanup Targets

1. Decide which tracked sample/session files should be converted to portable built-in sources versus preserved as explicit local test fixtures.
2. Keep moving repeated frontend project/session state logic out of `App.tsx` into selectors or controller helpers.
3. Continue extracting route-local viewer/render orchestration helpers until `routes.py` is mostly endpoint glue.

## Pass 8: Viewer Accessor Surface

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/viewer_context.py` | 9 | 9 | 9 | Now covers both API payload shaping and small viewer-setting resolution helpers. Route code no longer needs to hand-roll the same proxy/display/view logic repeatedly. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Slightly flatter after delegating more viewer-setting fallback rules to `viewer_context.py`. It remains large, but less repetitive. |
| `frontend/src/viewer/viewerMetadata.ts` | 9 | 9 | 9 | New focused helper module for bbox, scale, pixel-aspect, and proxy-detection logic derived from node metadata. |
| `frontend/src/viewer/ViewerPanel.tsx` | 8 | 8 | 8 | Improved by deleting repeated raw metadata math and replacing it with named helpers. Still a large UI file, but more of it now reads as orchestration rather than geometry bookkeeping. |
| `frontend/src/inspector/Inspector.tsx` | 8 | 8 | 8 | Minor improvement through shared metadata summary formatting rather than inlined field assembly. |

## What Improved In This Pass

- Backend route handlers now use shared viewer-setting helpers for proxy limits and display/view resolution.
- Frontend viewer bbox/proxy logic moved into a dedicated helper module instead of staying embedded in `ViewerPanel.tsx`.
- Metadata summaries now come from shared helpers, which reduces repeated direct field assembly.
- Verification stayed green across unit tests, integration tests, app smoke, and benchmark regression checks.

## Main Remaining Problems

- `frontend/src/viewer/ViewerPanel.tsx` is still long and remains a candidate for further separation into overlay/render-control subcomponents or helper modules.
- `backend/opencomp/api/routes.py` still contains too much mixed transport logic even after repeated helper extraction.
- The repository still contains a few tracked sample/session artifacts with local absolute paths; those are no longer runtime defaults, but they still deserve an explicit fixture-or-portable cleanup decision.

## Recommended Next Cleanup Targets

1. Split viewer overlays and interaction math out of `ViewerPanel.tsx`.
2. Keep pulling websocket/render helper logic out of `backend/opencomp/api/routes.py`.
3. Normalize or relocate tracked sample/session files that still embed machine-local paths so fixture content is clearly separated from portable app defaults.

## Pass 9: Viewer Request Orchestration Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/viewer_requests.py` | 9 | 9 | 9 | New focused home for request shaping, preview-request assembly, viewer input resolution, and background warm/preload orchestration. This is the right abstraction boundary between route transport and backend viewer behavior. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved again. One more dense cluster is gone, so the file trends closer to endpoint glue plus transport-specific formatting. |
| `backend/tests/test_viewer_requests.py` | 9 | 9 | 9 | Useful fast coverage for helper logic that previously could only be exercised indirectly through route integration tests. |

## What Improved In This Pass

- Preview-request building, scheduler scope shaping, and background warm/preload planning moved out of `routes.py`.
- The new helper surface now has direct unit coverage instead of relying only on endpoint tests.
- Viewer endpoint integration tests still pass after the split, which is a stronger signal that the extraction was behavior-neutral.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` still owns a large amount of websocket float-streaming and timing/header shaping logic.
- `frontend/src/ViewerPanel.tsx` remains one of the largest frontend modules even after recent helper extractions.
- Sample/session files with local paths are still in the repository and still deserve an explicit fixture portability decision.

## Recommended Next Cleanup Targets

1. Extract float-stream header/payload/timing helpers from `backend/opencomp/api/routes.py` into another dedicated module.
2. Continue splitting viewer UI overlays and interaction logic out of `frontend/src/viewer/ViewerPanel.tsx`.
3. Normalize or quarantine tracked local session/sample artifacts so runtime defaults and test fixtures remain clearly separated.

## Pass 10: Float Viewer Payload Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/viewer_float.py` | 9 | 9 | 9 | New focused module for float-frame headers, payload encoding, tile-source selection, native tile probing, and request timing shaping. This removes one of the densest transport-adjacent clusters from the main route file. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved again. The websocket/HTTP routes still do orchestration, but much less payload bookkeeping now lives inline. |
| `backend/tests/test_viewer_float.py` | 9 | 9 | 9 | Good fast unit coverage for float header shaping and ROI/tile metadata rules that previously lived only behind route integration. |
| `backend/scripts/benchmark_viewer_pipeline.py` | 8 | 8 | 8 | Better aligned with the new backend abstraction boundary instead of importing a route-local helper that no longer belongs in `routes.py`. |

## What Improved In This Pass

- Float viewer frame headers, encoded payload generation, tile-source selection, and normalized request timing shaping moved into a dedicated helper module.
- Unit tests now cover part of the float metadata surface directly.
- Route integration, app smoke, and benchmark checks all still pass after the split.
- One stale benchmark-script import was corrected so benchmarks now consume the new helper surface instead of route-local internals.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` still contains a large websocket float-stream control flow and tile-send loop.
- `frontend/src/viewer/ViewerPanel.tsx` remains a large file even after previous helper extraction passes.
- Local sample/session artifacts with host-specific paths are still present and should eventually be clearly categorized as fixtures or normalized.

## Recommended Next Cleanup Targets

1. Extract websocket float-stream send-loop helpers from `backend/opencomp/api/routes.py`, leaving only endpoint orchestration in the route file.
2. Continue separating overlay/interaction subdomains out of `frontend/src/viewer/ViewerPanel.tsx`.
3. Normalize or isolate tracked local sample/session files so runtime defaults, docs/examples, and local fixtures are clearly separated.

## Pass 11: Float Websocket Send-Loop Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/viewer_float_stream.py` | 9 | 9 | 9 | New focused async helper module for websocket float response emission, tile streaming, cancellation checks, and send-stat accumulation. This is the right transport-side companion to `viewer_float.py`. |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved again. The float websocket route now reads more like request orchestration and less like a hand-written stream implementation. |
| `backend/tests/test_viewer_float_stream.py` | 9 | 9 | 9 | Good fast coverage for non-tiled send, tiled fallback send, and cancellation behavior without depending only on full websocket integration tests. |

## What Improved In This Pass

- The float websocket send loop now lives in its own helper module instead of remaining inline in `routes.py`.
- Tile-send and cancellation behavior now have direct unit tests in addition to full websocket integration coverage.
- App smoke and benchmark checks still pass after the split.

## Main Remaining Problems

- `backend/opencomp/api/routes.py` still contains a fairly large websocket control-flow section even though the heavy helper logic has been extracted.
- `frontend/src/viewer/ViewerPanel.tsx` remains one of the biggest frontend readability hotspots.
- Host-specific tracked sample/session artifacts are still in the repo and should eventually be normalized or clearly separated as fixtures.

## Recommended Next Cleanup Targets

1. Either continue shrinking websocket/control-flow helpers in `backend/opencomp/api/routes.py` or switch to the frontend and split `ViewerPanel.tsx`.
2. Keep tightening the distinction between fast unit tests and slower integration/websocket coverage.
3. Normalize or isolate tracked local sample/session files so runtime defaults, docs/examples, and local fixtures are clearly separated.

## Pass 12: Accessor Surface And Path Portability Cleanup

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/projectSettings.ts` | 9 | 9 | 9 | Better home for direct project-path and export-path helpers. Save/export flows no longer need to reach through `project.settings.project_path` repeatedly. |
| `frontend/src/viewer/viewerMetadata.ts` | 9 | 9 | 9 | Now exposes direct viewer-context labels for routine UI reads. This keeps node metadata presentation aligned with the “no deep transport reads for basic viewer info” rule. |
| `backend/opencomp/api/context.py` | 8 | 9 | 8 | Small but useful `resolved_frame_number(...)` helper removes another repeated nested route pattern. |
| `scripts/run_backend.ps1` | 8 | 8 | 8 | Fixed a real portability bug: it no longer hard-codes one developer machine path and now resolves repo-relative backend/venv/log locations. |

## What Improved In This Pass

- Common project save/export path logic now lives behind direct helpers instead of repeated nested `project.settings.project_path` reads.
- Routine node viewer metadata labels now have direct accessors instead of components reaching into `metadata.viewer_context.*` for basic display text.
- Backend route code lost another repeated frame-default pattern through `resolved_frame_number(...)`.
- The stale PowerShell backend launcher is now workspace-relative and safe to use on other Windows machines.

## Hard-Coded Path Audit

- Fixed runtime debt:
  - `scripts/run_backend.ps1` had machine-specific `E:\\...` paths and is now portable.
- Acceptable for now:
  - Vulkan compiler fallback roots in `backend/opencomp/gpu/toolchain.py` are intentional OS/toolchain heuristics, not app-state defaults.
  - Documentation examples and local fixture references still contain host-style example paths where they are illustrating usage or opt-in local tests.
- Still worth a future cleanup:
  - Tracked local sample/session artifacts and compiled shader manifests still expose host-specific absolute paths. They are not the active runtime path, but they remain portability noise in the repo.

## Verification

- `python -m py_compile backend/opencomp/api/context.py backend/opencomp/api/routes.py`
- `npm run build`
- `python -m pytest backend/tests/test_api_context.py backend/tests/test_viewer_context.py backend/tests/test_setup_opencomp.py -q`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass12.json`
- Isolated browser smoke on `http://127.0.0.1:5191/` after loading `test_projects/local_read_viewer_check.opencomp`:
  - frame stepped `1001 -> 1002`
  - viewer status updated `GPU | F1001 | 47% -> GPU | F1002 | 47%`
  - app remained `ready`
  - no browser console warnings/errors

## Recommended Next Cleanup Targets

1. Split `frontend/src/viewer/ViewerPanel.tsx`, which still carries a large amount of rendering and interaction code in one file.
2. Decide whether host-specific tracked sample/session artifacts should move under explicit fixtures/examples or be normalized before commit.
3. Keep promoting repeated transport-shaped reads into small selectors/helpers before they spread into more components.

## Pass 13: Viewer Utility Extraction

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/viewer/ViewerPanel.tsx` | 8 | 8 | 8 | Improved substantially. The file is still large, but it now reads more like viewer UI orchestration instead of one monolithic container for state, geometry math, pixel decoding, and immediate-mode drawing. |
| `frontend/src/viewer/viewerGeometry.ts` | 9 | 9 | 9 | Good focused home for transform math, ROI normalization/drag logic, wipe-line checks, and compact cache-label shaping. |
| `frontend/src/viewer/viewerCanvas.ts` | 9 | 9 | 9 | Good focused home for checkerboard, image draw, wipe draw, badge draw, and small overlay primitives. |
| `frontend/src/viewer/viewerPixels.ts` | 9 | 9 | 9 | Clear separation for float-frame sampling, transport decoding, proxy-to-source coordinate mapping, and readout formatting. |
| `frontend/src/viewer/viewerFrame.ts` | 8 | 9 | 8 | Small improvement: render-key logic now lives with the rest of the float-frame header helpers instead of staying local to `ViewerPanel.tsx`. |

## What Improved In This Pass

- Extracted pure viewer geometry helpers into `viewerGeometry.ts`.
- Extracted canvas-specific drawing helpers into `viewerCanvas.ts`.
- Extracted float-frame pixel sampling and readout formatting into `viewerPixels.ts`.
- Moved float render-key shaping into `viewerFrame.ts`.
- Removed stale `compareImageSize` state from `ViewerPanel.tsx`; redraw still happens through the existing `bitmapRevision` signal.
- Reduced `ViewerPanel.tsx` from roughly `1763` lines to `1340` lines without removing viewer features.

## Verification

- `npm run build`
- `python -m pytest backend/tests/test_viewer_context.py backend/tests/test_viewer_float.py backend/tests/test_viewer_transport.py -q`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass13.json`
- Isolated browser smoke on `http://127.0.0.1:5192/` after loading `test_projects/local_read_viewer_check.opencomp`:
  - viewer opened cleanly
  - frame stepped `1001 -> 1002`
  - status updated `GPU | F1001 | 100% -> GPU | F1002 | 100%`
  - app stayed `ready`
  - no browser console warnings/errors

## Benchmark Notes

- Pass 12 reference:
  - PNG cold `874.5 ms`
  - PNG warm `0.56 ms`
  - float cold `601.29 ms`
  - float warm `10.92 ms`
- Pass 13 after extraction:
  - PNG cold `879.48 ms`
  - PNG warm `0.62 ms`
  - float cold `656.69 ms`
  - float warm `14.03 ms`
- Interpretation:
  - This pass was a structural refactor, not a performance change.
  - The observed movement is within the kind of run-to-run variance already seen in this viewer benchmark path and does not point to a deterministic regression from the frontend utility split.

## Recommended Next Cleanup Targets

1. Continue splitting `ViewerPanel.tsx`, with the next natural boundary being ROI/overlay drawing or HUD/control sections.
2. Tackle the larger `App.tsx` orchestration file, which is still the biggest frontend readability hotspot by a wide margin.
3. Keep separating fast frontend-adjacent helper logic from slower runtime/browser integration checks so verification cost stays proportional to the change.

## Pass 14: Backend Node Route Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `backend/opencomp/api/routes.py` | 8 | 8 | 8 | Improved again. The file is now more clearly centered on viewer/render transport, warm/cancel behavior, and websocket endpoints. |
| `backend/opencomp/api/node_routes.py` | 9 | 9 | 9 | Good focused home for node catalog, metadata/bindings, and Cryptomatte endpoints plus their shared node-evaluation helpers. |
| `backend/tests/test_node_routes.py` | 9 | 9 | 9 | Useful direct coverage for the extracted router instead of relying only on viewer-side integration tests to prove route wiring. |

## What Improved In This Pass

- Extracted node catalog, node metadata/bindings, and Cryptomatte endpoints into `backend/opencomp/api/node_routes.py`.
- Added small shared helpers inside the new router for node-context resolution and common evaluator-to-HTTP error shaping.
- Removed now-stale imports from `routes.py` after the extraction.
- Reduced `backend/opencomp/api/routes.py` from roughly `654` lines to `501` lines.
- Added direct integration coverage in `backend/tests/test_node_routes.py`.

## Verification

- `python -m py_compile backend/opencomp/api/node_routes.py backend/opencomp/api/routes.py backend/tests/test_node_routes.py`
- `python -m pytest backend/tests/test_node_routes.py backend/tests/test_viewer_endpoint.py backend/tests/test_viewer_context.py backend/tests/test_viewer_requests.py -q`
  - result: `18 passed`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass14.json`
- Isolated browser smoke on `http://127.0.0.1:5193/` after loading `test_projects/local_read_viewer_check.opencomp`:
  - viewer opened cleanly
  - frame stepped `1001 -> 1002`
  - status updated `GPU | F1001 | 47% -> GPU | F1002 | 47%`
  - app stayed `ready`
  - no browser console warnings/errors

## Benchmark Notes

- Pass 13 reference:
  - PNG cold `879.48 ms`
  - PNG warm `0.62 ms`
  - float cold `656.69 ms`
  - float warm `14.03 ms`
- Pass 14 after backend route split:
  - PNG cold `934.1 ms`
  - PNG warm `0.55 ms`
  - float cold `606.28 ms`
  - float warm `10.68 ms`
- Interpretation:
  - This pass changed route structure, not render math.
  - The benchmark remains within the same operating band as prior runs and does not indicate a route-split regression.

## Recommended Next Cleanup Targets

1. Continue reducing `frontend/src/App.tsx`, which remains the single largest readability hotspot in the repo.
2. Consider splitting color-config endpoints next if `routes.py` keeps collecting configuration-oriented helpers that are unrelated to viewer transport.
3. Keep tightening the unit/integration boundary by adding direct tests when a route/helper extraction lands, instead of leaning only on broader viewer endpoint coverage.

## Pass 15: Setup/Launcher Support Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `scripts/setup_opencomp.py` | 8 | 9 | 8 | Much better as a thin entrypoint. It now reads more clearly as install/setup orchestration instead of one file mixing install flow with runtime launcher mechanics. |
| `scripts/setup_opencomp_support.py` | 9 | 9 | 9 | Good focused home for runner generation, quoting, environment shaping, port selection, and runtime child-process launch helpers. |
| `backend/tests/test_setup_opencomp.py` | 9 | 9 | 9 | Cleaner unit boundary now that the test targets the pure helper module instead of the orchestration script. |

## What Improved In This Pass

- Extracted cross-platform launcher/runtime helpers from `scripts/setup_opencomp.py` into `scripts/setup_opencomp_support.py`.
- Kept `setup_opencomp.py` focused on argument parsing, install decisions, and stamp management.
- Preserved the existing public helper surface used by tests by repointing the unit tests at the new pure helper module.
- Removed the duplicate local `platform_id` / `is_windows` definitions after the split.
- The launcher helper surface is now much easier to test without spawning the whole app.

## Verification

- `python -m py_compile scripts/setup_opencomp.py scripts/setup_opencomp_support.py`
- `python -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass15.json`
- Isolated browser smoke through the setup script path on `http://127.0.0.1:5194/` after loading `test_projects/local_read_viewer_check.opencomp`:
  - app booted via `scripts/setup_opencomp.py --skip-install --backend-port 8024 --frontend-port 5194 --run`
  - viewer opened cleanly
  - frame stepped `1001 -> 1002`
  - status updated `GPU | F1001 | 47% -> GPU | F1002 | 47%`
  - app stayed `ready`
  - no browser console warnings/errors

## Benchmark Notes

- Pass 14 reference:
  - PNG cold `934.1 ms`
  - PNG warm `0.55 ms`
  - float cold `606.28 ms`
  - float warm `10.68 ms`
- Pass 15 after setup/launcher split:
  - PNG cold `889.28 ms`
  - PNG warm `0.55 ms`
  - float cold `591.08 ms`
  - float warm `10.88 ms`
- Interpretation:
  - This pass touched startup/launcher structure, not frame-processing math.
  - The benchmark stayed comfortably within the same operating band and shows no startup-structure regression.

## Recommended Next Cleanup Targets

1. Continue attacking `frontend/src/App.tsx`, which is still the largest coordination hotspot in the repo.
2. Consider a similar split for remaining configuration-oriented backend routes if they continue to accumulate in `routes.py`.
3. Keep making pure helper surfaces explicit when they are natural unit-test targets, then keep browser/app smoke focused on end-to-end confidence rather than helper correctness.

## Pass 16: App Project Accessor / Path Helper Split

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/App.tsx` | 8 | 8 | 8 | Improved again. Repeated project path, export, and preference-default logic moved out, and the preferences UI no longer lives inline inside the main app coordinator. |
| `frontend/src/projectFiles.ts` | 9 | 9 | 9 | Good focused home for backend-path detection, OpenComp/Nuke filename normalization, and graph-attached project serialization helpers. |
| `frontend/src/projectPreferences.ts` | 9 | 9 | 9 | Good selector layer for routine preference defaults used by render, warm, and cache code. |
| `frontend/src/preferences/PreferencesDialog.tsx` | 9 | 8 | 9 | Clean extraction of the preferences surface out of `App.tsx` while preserving current behavior. |
| `backend/scripts/build_vulkan_shaders.py` | 8 | 8 | 8 | More portable output metadata now that generated manifests stop baking absolute workspace and SDK paths into the shipped artifact. |

## What Improved In This Pass

- Extracted project save/export path helpers into `frontend/src/projectFiles.ts`.
- Extracted recurring preference-default selectors into `frontend/src/projectPreferences.ts`.
- Moved the preferences modal UI into `frontend/src/preferences/PreferencesDialog.tsx`.
- Reduced `frontend/src/App.tsx` from `2798` lines to `2601` lines.
- Normalized the checked-in Vulkan shader manifest metadata so it no longer stores absolute machine-specific build paths.

## Hard-Coded / Deep-Access Findings

- Runtime path handling is improved, but there are still intentional machine-specific paths in local tests and sample fixtures. Those are acceptable only as fixtures and should not spread into runtime defaults.
- The Vulkan runtime diagnostics still report absolute current-runtime paths in live status payloads. That is acceptable for diagnostics, but generated manifest artifacts should stay portable, which this pass fixes.
- `frontend/src/api/client.ts` still carries the local fallback `http://127.0.0.1:8000`, which is acceptable as a local-dev default but should remain isolated there rather than duplicated elsewhere.
- `App.tsx` still has some direct `project.settings.*` reads in render orchestration; they are fewer now, but it remains the next cleanup target.

## Verification

- `npm run build`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_vulkan_backend.py -k relative_manifest -q`
  - result: `1 passed`
- `.venv\\Scripts\\python.exe -m py_compile backend/scripts/build_vulkan_shaders.py`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass16.json`
- Browser smoke status:
  - attempted minimal in-app reload/preferences-dialog verification on `http://127.0.0.1:5174/`
  - blocked by Browser Use URL policy before page interaction
  - this pass therefore has build/test/benchmark verification, but not a completed browser interaction check

## Benchmark Notes

- Pass 15 reference:
  - PNG cold `889.28 ms`
  - PNG warm `0.55 ms`
  - float cold `591.08 ms`
  - float warm `10.88 ms`
- Pass 16 after accessor/path split:
  - PNG cold `946.76 ms`
  - PNG warm `0.55 ms`
  - float cold `711.24 ms`
  - float warm `24.94 ms`
- Interpretation:
  - This pass did not touch render math.
  - The result points to run-to-run variance in the current benchmark path rather than a likely deterministic regression from the refactor, but the next pass should include another benchmark sample and a successful app smoke check before treating the slower float run as noise.

## Recommended Next Cleanup Targets

1. Continue reducing `frontend/src/App.tsx`, especially the remaining viewer/render orchestration that still reads `project.settings.*` directly in several places.
2. Consider introducing a small project runtime snapshot/helper for tile height, tile lanes, execution backend, and working colorspace so those values are not pulled ad hoc out of settings in render code.
3. Re-run a full browser smoke once local-page policy allows it again, because the preferences extraction itself was not interaction-verified in the browser during this pass.

## Pass 17: Viewer Runtime Helper Consolidation + Relative Shader Diagnostics

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/App.tsx` | 8 | 8 | 8 | Improved slightly in behavior density even though line count stayed roughly flat. Viewer/runtime fallback math is less scattered across preload, playback, and transport callbacks. |
| `frontend/src/projectRuntime.ts` | 9 | 9 | 9 | Good dedicated home for viewer tile defaults, frame bounds, execution-backend selection, and warm-window sizing. |
| `backend/opencomp/gpu/runtime.py` | 8 | 8 | 8 | Cleaner diagnostics surface now that shader toolchain status prefers backend-relative paths for repo-owned assets while preserving absolute paths for external override directories. |
| `backend/tests/test_vulkan_backend.py` | 9 | 9 | 9 | Better coverage for the new relative-diagnostics behavior without breaking the existing external-temp-path use case. |

## What Improved In This Pass

- Extracted recurring viewer runtime fallback math into `frontend/src/projectRuntime.ts`.
- Replaced inline frame-bound, tile-size, tile-lane, and warm-limit logic in `App.tsx` with direct helper calls.
- Kept isolated app startup verification on the real setup path instead of relying only on build/test signals.
- Normalized Vulkan shader diagnostics so repo-owned shader paths are reported relative to `backend/` instead of as long machine-specific absolute paths.
- Added targeted tests for both relative repo diagnostics and external override-path behavior.

## Hard-Coded / Deep-Access Findings

- The biggest remaining frontend coordinator smell is still `App.tsx` directly reading render-oriented `project.settings.*` fields in some viewer orchestration paths.
- Runtime diagnostics are now much cleaner for shader assets, but `compiler_path` is intentionally still absolute because it identifies the actual compiler used on the machine.
- Local fixture/sample projects still contain machine-specific paths by design; they remain acceptable only as fixtures, not as runtime defaults.

## Verification

- `npm run build`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- isolated startup smoke via `scripts/setup_opencomp.py --skip-install --backend-port 8031 --frontend-port 5196 --run`
  - result: backend health `ok`, frontend root `200 OK`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass17.json`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_vulkan_backend.py -k "relative_manifest or repo_assets" -q`
  - result: `2 passed`
- direct runtime status check:
  - `source_dir -> opencomp/gpu/shaders/src`
  - `compiled_dir -> opencomp/gpu/shaders/compiled`
  - `manifest_path -> opencomp/gpu/shaders/compiled/manifest.json`

## Benchmark Notes

- Pass 16 reference:
  - PNG cold `946.76 ms`
  - PNG warm `0.55 ms`
  - float cold `711.24 ms`
  - float warm `24.94 ms`
- Pass 17 after viewer runtime helper consolidation:
  - PNG cold `935.83 ms`
  - PNG warm `0.54 ms`
  - float cold `604.56 ms`
  - float warm `13.51 ms`
- Interpretation:
  - This pass was structural, not algorithmic.
  - The benchmark stayed healthy and returned closer to the earlier operating band, which supports treating the slower Pass 16 float sample as run-to-run variance rather than a deterministic refactor regression.

## Recommended Next Cleanup Targets

1. Continue reducing `App.tsx` by extracting the remaining render/request orchestration clusters, especially around viewer frame request planning and playback state.
2. Add a lightweight frontend test runner and start covering pure helper modules such as `projectSettings.ts`, `projectFiles.ts`, and `projectRuntime.ts` with real unit tests instead of relying only on build validation.
3. Keep auditing backend diagnostics and metadata payloads for other unnecessarily verbose absolute paths or repeated nested structures that could be summarized more cleanly.

## Pass 18: Frontend Unit-Test Layer For Extracted Helpers

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/package.json` | 8 | 9 | 8 | Better now that the frontend has an explicit unit-test command instead of treating build/typecheck as the only frontend validation. |
| `frontend/vitest.config.ts` | 9 | 9 | 9 | Minimal and focused. Keeps the unit-test surface constrained to pure TS helper specs. |
| `frontend/src/test/projectFixtures.ts` | 9 | 9 | 9 | Good shared fixture surface that keeps helper specs DRY and readable. |
| `frontend/src/*.test.ts` helper specs | 9 | 9 | 9 | Solid targeted unit coverage for the extracted helper modules; failures should point directly at logic regressions rather than broad app behavior. |
| `docs/test_strategy.md` | 9 | 9 | 9 | Useful repo guidance for keeping unit and integration responsibilities separate going forward. |

## What Improved In This Pass

- Added a frontend unit-test runner with `vitest`.
- Added targeted unit coverage for:
  - `projectSettings.ts`
  - `projectFiles.ts`
  - `projectPreferences.ts`
  - `projectRuntime.ts`
- Added shared frontend project fixtures so helper tests stay concise and consistent.
- Documented the unit-vs-integration split in `docs/test_strategy.md`.
- Updated agent guidance so future cleanup work keeps pure helper validation out of broad app-only checks.

## Test Structure Findings

- The repo now has a clear frontend unit-test entrypoint, which was previously missing.
- Backend integration coverage is already stronger than frontend coverage, but there is still room to add more backend unit tests around smaller utility modules instead of relying mostly on route/runtime suites.
- Remaining frontend behavior in `App.tsx` is still best validated by startup smoke and targeted benchmarks until more orchestration logic is extracted behind testable helpers.

## Verification

- `cd frontend && npm run test:unit`
  - result: `4 files passed`, `14 tests passed`
- `npm run build`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- isolated startup smoke via `scripts/setup_opencomp.py --skip-install --backend-port 8032 --frontend-port 5197 --run`
  - result: backend health `ok`, frontend root `200 OK`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass18.json`

## Benchmark Notes

- Pass 17 reference:
  - PNG cold `935.83 ms`
  - PNG warm `0.54 ms`
  - float cold `604.56 ms`
  - float warm `13.51 ms`
- Pass 18 after frontend test-layer addition:
  - PNG cold `1031.78 ms`
  - PNG warm `0.74 ms`
  - float cold `591.87 ms`
  - float warm `10.88 ms`
- Interpretation:
  - This pass changed test/tooling only.
  - Float timings stayed healthy; the slightly slower PNG run looks like ordinary benchmark variance rather than a code-path regression from the new test infrastructure.

## Recommended Next Cleanup Targets

1. Keep shrinking `App.tsx` by extracting the viewer frame request / playback orchestration cluster into a dedicated runtime helper or hook-like module.
2. Add more backend unit-test coverage around pure Python utility modules so the test pyramid is not overly biased toward integration tests.
3. Continue auditing package/runtime defaults and stale code paths now that helper modules have direct unit coverage and are safer to refactor.

## Pass 19: Viewer Compare / Preview Planning Extraction

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/App.tsx` | 8 | 8 | 8 | Improved in the request-planning area. Compare-mode branching is thinner and easier to scan because the orchestration code now delegates repeated planning logic. |
| `frontend/src/viewer/viewerCompare.ts` | 9 | 9 | 9 | Good focused helper module for compare-input lists, CPU preview planning, request timing fields, and display-preview transport labels. |
| `frontend/src/viewer/viewerCompare.test.ts` | 9 | 9 | 9 | Useful focused unit coverage, including the transport-label bug that previously would have slipped through typecheck/build. |

## What Improved In This Pass

- Extracted repeated compare-mode request logic from `App.tsx` into `frontend/src/viewer/viewerCompare.ts`.
- Replaced repeated compare-input list construction in interactive warm and playback warm paths with a shared helper.
- Replaced inline CPU preview fallback branching with a pure preview-plan helper.
- Replaced inline request timing compare-field shaping with a pure helper.
- Fixed a real bug where the CPU display-preview fallback transport label used the `playbackTransferMode` function object instead of the current playback mode value.

## Verification

- `cd frontend && npm run test:unit`
  - result: `5 files passed`, `18 tests passed`
- `npm run build`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- isolated startup smoke via `scripts/setup_opencomp.py --skip-install --backend-port 8033 --frontend-port 5198 --run`
  - result: backend health `ok`, frontend root `200 OK`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass19.json`

## Benchmark Notes

- Pass 18 reference:
  - PNG cold `1031.78 ms`
  - PNG warm `0.74 ms`
  - float cold `591.87 ms`
  - float warm `10.88 ms`
- Pass 19 after compare/request helper extraction:
  - PNG cold `1153.38 ms`
  - PNG warm `0.74 ms`
  - float cold `673.75 ms`
  - float warm `15.11 ms`
- Interpretation:
  - This pass was structural and fixed one string-label bug rather than touching core image math.
  - The cold-run slowdown looks like benchmark variance in the current environment, not a change expected from this helper extraction. Warm behavior remains in the same band.

## Recommended Next Cleanup Targets

1. Continue splitting `App.tsx` by extracting the remaining viewer frame transport/result application block, especially the blob/GPU-frame result application and frontend timing history updates.
2. Add backend unit tests around path normalization and sequence-pattern helpers if they are not already isolated enough.
3. Keep looking for function/object misreferences like the fixed `display-preview` label bug, because those are exactly the kind of mistakes that become visible once branch logic is moved behind testable pure helpers.

## Pass 20: Viewer Result Bookkeeping Extraction

| Module | Discoverability | Readability | Structure | Notes |
| --- | ---: | ---: | ---: | --- |
| `frontend/src/App.tsx` | 8 | 8 | 8 | Slightly cleaner in the post-render application block. Timing payload assembly and result-kind branching are no longer buried inline with React state updates. |
| `frontend/src/viewer/viewerResult.ts` | 9 | 9 | 9 | Good focused helper surface for timing-history trimming, request-timing shaping, result-mode classification, and frontend-cache reuse decisions. |
| `frontend/src/viewer/viewerResult.test.ts` | 9 | 9 | 9 | Useful direct unit coverage for the new bookkeeping helpers. |

## What Improved In This Pass

- Extracted frontend timing-history trimming into `viewerResult.ts`.
- Extracted frontend request-timing payload shaping into `viewerResult.ts`.
- Extracted viewer result-kind selection (`gpu` / `blob` / `none`) into a pure helper.
- Extracted the frontend-cache reuse decision into a helper instead of leaving it as inline boolean coupling.
- Added focused unit coverage for the new helper module.

## Verification

- `cd frontend && npm run test:unit`
  - result: `6 files passed`, `22 tests passed`
- `npm run build`
- `.venv\\Scripts\\python.exe -m pytest backend/tests/test_setup_opencomp.py -q`
  - result: `9 passed`
- isolated startup smoke via `scripts/setup_opencomp.py --skip-install --backend-port 8034 --frontend-port 5199 --run`
  - result: backend health `ok`, frontend root `200 OK`
- `backend/scripts/benchmark_viewer_pipeline.py ... --output .codex_debug/benchmark_viewer_pipeline_pass20.json`

## Benchmark Notes

- Pass 19 reference:
  - PNG cold `1153.38 ms`
  - PNG warm `0.74 ms`
  - float cold `673.75 ms`
  - float warm `15.11 ms`
- Pass 20 after viewer result helper extraction:
  - PNG cold `1098.53 ms`
  - PNG warm `0.80 ms`
  - float cold `655.14 ms`
  - float warm `11.88 ms`
- Interpretation:
  - This pass was structural and did not change render math.
  - The benchmark remains within the same operating band and does not indicate a regression from the extraction.

## Recommended Next Cleanup Targets

1. Continue reducing `App.tsx` by extracting the remaining side-effect-heavy viewer result application itself, especially URL lifecycle and GPU/blob state application.
2. Add backend unit coverage for pure path/sequence helpers to continue rebalancing the test pyramid.
3. Audit other frontend request/logging paths for similar inline payload-building that can move behind small tested helper surfaces.
