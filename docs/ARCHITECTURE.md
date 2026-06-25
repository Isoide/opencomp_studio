# OpenComp Studio Architecture

OpenComp Studio is currently a local, browser-based compositor prototype. The backend owns the project, node graph, image evaluation, file I/O, OCIO integration, metadata, and caches. The frontend owns the interactive UI, node graph canvas, viewer canvas, WebGL display path, playback controls, script editor, and browser-side viewer cache.

The current design is intentionally close to a Nuke-like model: a directed node graph evaluates from upstream inputs to downstream outputs, the Viewer is a node with numbered inputs, and the Write node renders files from the evaluated graph.

## Current Technology Stack

### Backend

- FastAPI and Uvicorn: local HTTP and WebSocket server.
- Pydantic: project, graph, settings, request, and API response models.
- NumPy: core in-memory image representation and most pixel math.
- Pillow: PNG/JPG read/write, PNG encode, and float-plane bilinear resize.
- OpenEXR Python bindings, optional dependency: EXR read/write fallback.
- OpenImageIO Python bindings, optional dependency: preferred EXR read/write backend and preferred OCIO execution path.
- PyOpenColorIO or opencolorio, optional dependency: OCIO config access, CPU fallback processors, and GPU shader generation.
- Python `asyncio.to_thread`: keeps FastAPI request handlers responsive while CPU work runs in worker threads.
- `ThreadPoolExecutor`: row/tile parallelism for selected node operations.

### Frontend

- React and Vite: application UI and development server.
- TypeScript: frontend type contracts.
- Zustand: app state store.
- WebGL2: GPU viewer path for float viewer frames.
- Browser WebSocket API: binary viewer streaming from backend to frontend.
- HTML canvas: node graph and viewer interaction surfaces.
- Lucide React: UI icons.

## Image Data Model

The main backend image unit is `ImageFrame` in `backend/opencomp/core/models.py`.

Each frame contains:

- `data`: contiguous NumPy `float32`, shape `height x width x 4`, RGBA.
- `channels`: list of available channel names for UI/channel menus.
- `channel_data`: optional extra planes or grouped AOV layers, also `float32`.
- `pixel_aspect`: pixel aspect ratio from EXR metadata or project defaults.
- `colorspace`: source/working color space name.
- `metadata`: input metadata, EXR header values, processing hints, write metadata.
- `format_bbox`: visible format box.
- `data_window`: active EXR/data window box.

All node processing is scene-linear float unless a node explicitly performs a color transform. The backend graph cache stores float32 frames. The browser delivery format can be PNG, float32, float16, packed RGB10A2, or 8-bit RGBA WebSocket data depending on viewer preferences.

## File I/O

### Reads

Read node evaluation is implemented by `backend/opencomp/nodes/read.py` and `backend/opencomp/io/image_reader.py`.

Supported sources:

- `builtin://gradient`: generated test source.
- PNG/JPG/JPEG: loaded through Pillow and normalized to float RGBA.
- EXR: loaded through OpenImageIO when available, with OpenEXR fallback retained.

Sequence expansion supports:

- `####`
- `%04d`
- `%d`

Example:

```text
E:\opencomp_tests\shot\PLATE\shot_####.exr
```

For EXR:

- OpenImageIO is the preferred reader backend when installed.
- OpenEXR v3 `OpenEXR.File` remains the fallback backend.
- Legacy `OpenEXR.InputFile` is still used for the exact single-channel fast path and as the final compatibility fallback.
- `displayWindow` and `dataWindow` are parsed.
- Pixel aspect ratio is read from EXR metadata.
- Sparse EXR data windows are expanded into the display window so compositing alignment works.
- Channel names are read from the EXR header and exposed to the UI.
- Reads can now materialize only selected channel groups, for example RGBA-only, while still exposing the full header channel list.

This matters for 3D renders: a 60-channel EXR can still advertise all AOVs, while a normal slapcomp can avoid loading every AOV into memory.

Read parameters:

- `path` or `file`
- `colorspace`
- `frame_start`, `frame_end`
- `before`, `after`
- `frame_mode`, `frame_offset`, `frame_expression`
- `read_all_channels`
- `read_channels`
- `missing_frames`

By default, EXR Reads now use smart channel demand: RGBA is loaded first, and extra AOVs are loaded only when the evaluated node tree references them or when the viewer requests a channel on demand. Set `read_all_channels=True` or `read_channels="all"` only when a script truly needs every layer in memory.

### Writes

Write node evaluation is implemented by `backend/opencomp/nodes/write.py` and `backend/opencomp/io/image_writer.py`.

Supported outputs:

- EXR: OpenImageIO preferred, OpenEXR fallback, float channels, ZIP compression by default.
- PNG: Pillow, 8-bit RGBA.
- JPG/JPEG: Pillow, 8-bit RGB.

Write supports:

- sequence paths with `####`, `%04d`, `%d`
- `channels`: `rgba`, `rgb`, `alpha`, `all`, or channel mask text
- metadata policy
- overwrite control
- directory creation
- limited frame range

Current limitation: writing all auxiliary channels depends on `ImageFrame.channel_data` being present. With smart Reads, extra AOV pixel data is not loaded unless the graph/viewer demands it, so all-layer writes must explicitly request those layers or enable full channel loading upstream.

## Color Pipeline and OCIO

OCIO is wrapped by `backend/opencomp/color/ocio_engine.py`.

Current behavior:

- Auto-loads a configured `.ocio`, a builtin OCIO config, or the current OCIO config.
- Prefers ACES 2.0 builtin configs when available.
- Exposes color spaces, displays, and views through `/api/color/config`.
- Prefers OpenImageIO for backend colorspace conversion and display/view transforms when it is available.
- Uses PyOpenColorIO CPU processors as the compatibility fallback.
- Generates OCIO GPU display shader text through `/api/color/gpu-shader`.
- The frontend WebGL viewer can apply viewer process and OCIO shader on the GPU.

Scene-linear viewer processing:

1. Backend evaluates the requested viewer input to `ImageFrame`.
2. Backend extracts the requested channel into float RGBA.
3. Backend proxy-resizes if proxy mode is enabled.
4. Backend stores that pre-display float buffer in the float preview cache.
5. Frontend receives float32, float16, RGB10A2, or 8-bit RGBA depending on the Viewer Precision preference.
6. Frontend WebGL applies viewer gain/saturation/f-stop and OCIO display shader.
7. Browser displays the final monitor image.

This preserves highlight/detail data because viewer gain, saturation, and f-stop happen before final display clamp.

## Backend Evaluation

The evaluator lives in `backend/opencomp/core/evaluator.py`.

Evaluation is demand-driven:

1. User requests a node/frame, usually a Viewer or Write.
2. Evaluator computes a recursive node signature for that node/frame.
3. Signature includes node type, params, upstream signatures, and Read source file fingerprint.
4. Evaluator checks the in-memory cache with `(node_id, frame, signature)`.
5. Cache hit returns the existing `ImageFrame`.
6. Cache miss evaluates upstream nodes first, then the current node.
7. Non-viewer nodes are cached.
8. Viewer nodes are not cached as graph nodes, but preview/float viewer caches are separate.

Viewer evaluation only evaluates the active viewer input. When a Viewer node input slot is selected, only nodes upstream of that slot should be traversed by the evaluator.

## Threading and Parallelism

The app has several levels of concurrency:

- FastAPI endpoints use `asyncio.to_thread` for heavy evaluation, rendering, script execution, and WebSocket frame construction.
- `backend/opencomp/core/tile_engine.py` provides row tile ranges and `ThreadPoolExecutor` workers.
- Merge and grade-like operations can process row tiles in parallel when `tile_rendering_enabled` is true.
- Viewer WebSocket float tile encoding runs tile by tile and can yield partial display updates in the frontend.
- Background viewer warming uses `asyncio.create_task` plus `asyncio.to_thread`.
- Frontend idle cache warming requests frames after inactivity.

Current tile rendering is row-based, full-width tiles. It is not yet true visible-region tile rendering.

Project settings:

- `tile_rendering_enabled`
- `tile_height`
- `tile_workers`

## Current Processing Algorithms

### Read

- Resolve frame path.
- Load image into float RGBA.
- Parse metadata, pixel aspect, channels, format/data windows.
- For EXR sparse data windows, place data into the display window.
- Optional RGBA-only or selected-channel loading.

### Reformat

- Bilinear resize using Pillow float planes.
- Tracks scaled `format_bbox` and `data_window`.
- Optimized for sparse data windows: crop active data, resize the crop, and paste into the target frame.

### Grade

- Scene-linear RGB math:
  - gain
  - multiply
  - offset
  - add
  - gamma
- Uses row tile mapping when enabled.

### Merge

Supported operations include:

- `over`
- `under`
- `atop`
- `in`
- `out`
- `mask`
- `stencil`
- `xor`
- `plus` / `add`
- `minus`
- `from`
- `difference` / `absminus`
- `multiply`
- `screen`
- `max`
- `min`
- `average`
- `divide`
- `copy`
- `matte`

Merge can use row tile parallelism. It tracks output data-window with union/intersection/A/B policies.

### Transform and Scale

- Transform-like nodes update data-window extents.
- Current v1 tracks bbox/data-window and works on full arrays.
- Off-format pixel preservation is limited.

### Channels

The channel module contains:

- Shuffle
- Copy
- ChannelMerge
- AddChannels
- Remove
- Premult
- Unpremult
- Invert
- Clamp
- Exposure
- Saturation
- Blur
- Metadata helper nodes

Auxiliary channel operations require `channel_data` to be present.

### Cryptomatte

Cryptomatte utilities live in `backend/opencomp/io/cryptomatte.py`.

Current capabilities:

- Parse cryptomatte layer metadata and manifests.
- Generate ID preview colors.
- Pick cryptomatte ID at a viewer pixel.
- Build mattes for selected IDs.

For cryptomatte work, Read nodes generally need all relevant cryptomatte channels loaded.

## Viewer Architecture

There are two viewer paths.

### GPU Float Viewer Path

Preferred path:

1. Frontend calls `/ws/viewer/float`.
2. Backend returns a JSON frame header.
3. Backend streams full-width row tiles as binary float32, float16, RGB10A2, or 8-bit RGBA.
4. Frontend assembles the float buffer.
5. Frontend stores it in the browser viewer cache.
6. WebGL2 uploads the float frame to a texture.
7. WebGL applies:
   - viewer gain
   - saturation
   - f-stop
   - optional wipe/difference compare
   - OCIO GPU display shader if available
8. Viewer canvas displays the result.

Default transport is float16 tiled streaming. Float32 preserves the most viewer precision, float16 is the normal scene-linear performance mode, and RGB10A2/8-bit are smaller preview modes that clamp and quantize the viewer stream.

### CPU PNG Fallback

Fallback path:

1. Frontend calls `/ws/viewer/frame` or `/api/viewer/frame`.
2. Backend evaluates graph.
3. Backend applies viewer process.
4. Backend applies OCIO display transform on CPU.
5. Backend encodes PNG.
6. Frontend displays PNG.

This path is useful as compatibility fallback but is slower for full resolution because CPU OCIO plus PNG encoding are expensive.

## Caching

### Backend Node Cache

Cache key:

```text
(node_id, frame, node_signature)
```

The signature includes:

- node id
- node type
- node params
- upstream input signatures
- Read source fingerprint: path, file size, mtime

Cached data:

- full `ImageFrame`
- byte estimate
- signature

Limits:

- controlled by `ProjectPreferences.cache_memory_limit_mb`
- LRU pruning through `OrderedDict`
- in-memory only
- reset on backend restart

### Backend PNG Preview Cache

Stores final display PNG bytes.

Key includes:

- viewer node
- frame
- output signature
- display/view
- channel
- proxy dimensions
- OCIO config
- viewer process settings

### Backend Float Preview Cache

Stores pre-display float RGBA after channel extraction and proxy resize.

Key includes:

- viewer node
- frame
- output signature
- channel
- proxy dimensions

Viewer gain/saturation/f-stop changes should reuse this cache because they are viewer-only display state, not graph state.

### Frontend Viewer Cache

The browser keeps float viewer frames in memory for very fast playback and frame switching.

Key includes:

- graph/render revision
- script tab
- viewer node
- frame
- viewer input
- channel
- proxy/full-res token
- OCIO config
- working color space

Default target is 10 GB, capped at 64 GB. It is still browser memory, so practical limits depend on the browser and machine.

### Cache Metrics

`/api/cache/status` returns:

- node cache entries/hits/misses
- preview cache entries/hits/misses
- float preview cache entries/hits/misses
- active nodes
- node timings
- phase timings
- request timings
- cached frames

The frontend displays a cache pill and Metrics tab.

## Backend and Frontend Interaction

Main API categories:

- Project: `/api/projects/new`, `/api/projects/save`, `/api/projects/load`, `/api/projects/import`, `/api/projects/export-nuke`, `/api/projects/settings`, `/api/projects/preferences`
- Graph: `/api/graph`
- Scripts: `/api/scripts`, `/api/scripts/active`, `/api/python/run`
- Nodes: `/api/nodes/catalog`, `/api/nodes/{node_id}/metadata`
- Color: `/api/color/config`, `/api/color/gpu-shader`
- Viewer: `/api/viewer/frame`, `/ws/viewer/frame`, `/ws/viewer/float`
- Cryptomatte: `/api/nodes/{node_id}/cryptomatte`, `/api/cryptomatte/pick`, `/api/cryptomatte/matte`
- Render: `/api/render`
- Cache: `/api/cache/status`, `/api/cache/clear`

The frontend synchronizes graph/settings before viewer renders. The backend returns the updated project after script execution so the frontend can adopt changed graph state.

Project scripts are JSON-backed `.opencomp` files. The backend API, browser UI, and headless CLI all use the same project model and project I/O helpers for save/load. Nuke `.nk` export currently writes a v1 structural script with native mappings for common nodes and NoOp fallbacks for unsupported nodes.

Browser project save has two modes: backend-path save for full filesystem paths, and browser file save/download for plain filenames. Browser project open imports selected `.opencomp` JSON through `/api/projects/import`.

The CLI entry points are:

```powershell
python -m opencomp.cli shot.opencomp --render Write1 --range 1001-1005
opencomp shot.opencomp --list-nodes
```

## Current Performance Profile

Recent slapcomp metrics on a 4096x3024 plate plus two 3D EXR reads:

- Main 3D EXR all-channel read: about 3.6 s.
- Main 3D EXR RGBA-only read: about 1.9 s.
- Sparse clothes reformat after data-window crop optimization: about 37 ms isolated.
- Proxy viewer frame via float16 tiled WebSocket: about 4.8-5.3 s cold.
- Frontend viewer cache frame swap: single-digit milliseconds in observed runs.
- Full-res frame through GPU float tiles: about 6.8 s in observed run.

The largest remaining cold-frame costs are CPU EXR decode and full-resolution upstream evaluation. The largest full-resolution transport cost is moving large float buffers through the browser.

## Current Limitations

- Graph operations are CPU/NumPy; GPU is currently viewer/display only.
- EXR decode is Python OpenEXR based and CPU-bound.
- Proxy mode does not yet push proxy resolution upstream through the whole graph; it mostly affects viewer extraction/resize.
- Tile streaming is full-width row tiles, not visible-region tile streaming.
- Float tiles are sent sequentially over one WebSocket.
- Backend caches are in-memory and vanish on restart.
- Cache sizes are process memory budgets, not disk cache.
- Full AOV and cryptomatte workflows require loading auxiliary channels, which increases memory and read time.
- Write node has a simple single-frame render endpoint, not a robust render queue.
- Script editor executes Python in the backend process; it is powerful but not sandboxed for hostile code.
- BBox/data-window tracking exists, but v1 does not preserve arbitrary off-format pixels outside the array.
- Deep EXR, multipart EXR, and advanced production image formats are not complete.
- OCIO GPU shader support depends on OpenColorIO shader generation and WebGL compatibility; CPU fallback remains required.

## Potential Upgrades

### High-Impact Performance

- True tile/visible-region renderer:
  - evaluate only visible tiles
  - cache tiles independently
  - prioritize visible tiles before offscreen tiles
  - reuse tile cache across zoom/pan
- Proxy-aware graph evaluation:
  - propagate proxy scale upstream
  - read lower-resolution mip/thumbnail where possible
  - avoid full 4K arrays for proxy playback
- Deeper OpenImageIO integration:
  - keep OIIO as the default EXR reader backend
  - expand use of OIIO tiled and region reads
  - evaluate OIIO C++ OCIO color conversion deeper in the processing path
  - preserve OpenEXR fallback for compatibility-sensitive cases
- C++ or Rust image core:
  - merge/grade/reformat kernels
  - SIMD/vectorized tile loops
  - lower Python overhead
- Worker process pool:
  - isolate heavy renders
  - avoid blocking backend process
  - allow cancelable jobs
- Compressed or packed float transfer:
  - float16 is current
  - add visible-region compression
  - consider GPU-friendly texture compression or WebCodecs-like transport where viable

### GPU Expansion

- Move grade, exposure, saturation, merge, transform, and reformat kernels to WebGPU or native GPU backend.
- Keep CPU fallback for all operations.
- Add OCIO shader baking and persistent shader cache.
- Add GPU texture cache in frontend for current frame range.
- Consider WebGPU compute for viewer-only operations first.

### Caching

- Persistent disk cache for decoded EXR tiles and rendered node tiles.
- Cache budget per cache type instead of shared coarse limits.
- Better cache diagnostics:
  - inclusive vs exclusive node timing
  - per-phase read/decode/resize/merge/send/upload timing
  - cache eviction reporting
- Predictive background caching with cancellation and priority.

### Image Model

- Native bbox/off-format storage instead of only full arrays.
- Deep EXR support.
- Multipart/multiview EXR support.
- Channel masks that lazily load missing AOVs on demand.
- More precise color metadata contracts per node.

### Production Features

- Render queue and frame-range render jobs.
- Project file autosave/recovery.
- Plugin discovery and user init scripts.
- Sandboxed script execution mode.
- Node presets, group node internals, gizmos, and custom node registration.
- Roto/paint data model and viewer interaction tools.
