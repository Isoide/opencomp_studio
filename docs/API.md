# OpenComp Studio API

The backend API is local-first and runs at:

```text
http://127.0.0.1:8000
```

The frontend development server runs at:

```text
http://127.0.0.1:5173
```

All examples assume the backend is running.

## Health

```http
GET /api/health
```

Example:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Response:

```json
{
  "status": "ok",
  "app": "OpenComp Studio"
}
```

## Project

### Create New Project

```http
POST /api/projects/new
```

Creates a default project and resets the backend project/evaluator state.

### Save Project

```http
POST /api/projects/save
```

Body:

```json
{
  "path": "E:/PROJECTS/opencomp_studio/example.opencomp",
  "project": null
}
```

If `project` is null, the current backend project is saved. Paths without an extension are saved with the `.opencomp` extension.

In the browser UI, a plain filename saves/downloads a local `.opencomp` file through the browser. A full filesystem path, such as `E:/show/shot/comp.opencomp` or a UNC path, is treated as a backend-side save path.

### Load Project

```http
POST /api/projects/load
```

Body:

```json
{
  "path": "E:/PROJECTS/opencomp_studio/example.opencomp"
}
```

Loads a `.opencomp` script/project file into the live backend session, resets the evaluator, and clears stale cache state.

### Import Project Data

```http
POST /api/projects/import
```

Body:

```json
{
  "project": {
    "schema_version": "0.1.0",
    "project_name": "Imported Comp"
  }
}
```

Imports `.opencomp` JSON data selected in the browser and installs it as the live backend session. This is used by the Open File button because browser apps cannot provide arbitrary local filesystem paths to the backend.

### Export Nuke Script

```http
POST /api/projects/export-nuke
POST /api/projects/export-nuke/content
```

Body:

```json
{
  "path": "E:/PROJECTS/opencomp_studio/example.nk",
  "project": null
}
```

Writes a v1 `.nk` file with a Nuke root, native node blocks for common OpenComp nodes, and stack-based `set`/`push` connections. Unsupported node types are exported as labeled `NoOp` placeholders so the script can still open and preserve graph shape.

`/api/projects/export-nuke` writes to a backend filesystem path. `/api/projects/export-nuke/content` returns the generated `.nk` text and is used by the browser Save/Download flow.

### Get Settings

```http
GET /api/projects/settings
```

### Update Settings

```http
PUT /api/projects/settings
```

Body:

```json
{
  "settings": {
    "fps": 24,
    "frame_start": 1001,
    "frame_end": 1010,
    "width": 1920,
    "height": 1080,
    "working_colorspace": "ACEScg",
    "ocio_config": null,
    "viewer_display": "sRGB - Display",
    "viewer_view": "ACES 2.0 - SDR 100 nits (Rec.709)",
    "proxy_enabled": true,
    "viewer_max_width": 1280,
    "viewer_max_height": 720,
    "project_path": null,
    "default_output_path": "renders/output.####.png",
    "cache_enabled": true,
    "auto_refresh": true,
    "tile_rendering_enabled": true,
    "tile_height": 64,
    "tile_workers": 4
  }
}
```

### Preferences

```http
GET /api/projects/preferences
PUT /api/projects/preferences
```

Preferences include cache memory limit, hotkeys, custom init script paths, and viewer interaction defaults.

## Graph

### Get Graph

```http
GET /api/graph
```

### Replace Graph

```http
PUT /api/graph
```

Body:

```json
{
  "graph": {
    "nodes": {
      "Read1": {
        "id": "Read1",
        "type": "Read",
        "name": "Read1",
        "position": [120, 80],
        "params": {
          "path": "E:/plates/shot_####.exr",
          "colorspace": "ACES2065-1",
          "frame_start": 1001,
          "frame_end": 1010
        },
        "inputs": {},
        "outputs": {
          "out": "ImageFrame"
        }
      }
    },
    "edges": []
  }
}
```

Most graph edits are easier through the Python script API.

## Script Tabs

```http
GET /api/scripts
POST /api/scripts
PUT /api/scripts/active
PATCH /api/scripts/{script_id}
```

Create tab body:

```json
{
  "name": "Comp 2",
  "kind": "comp"
}
```

Set active body:

```json
{
  "script_id": "comp-2"
}
```

## Python Script Editor API

```http
POST /api/python/run
```

Body:

```json
{
  "code": "print(opencomp.nodes())"
}
```

Response includes:

- `success`
- `stdout`
- `stderr`
- `error`
- `traceback`
- `changed`
- `project`

The frontend Script Editor uses this endpoint.

## Script Editor Object Model

The script namespace exposes:

- `opencomp`: session object
- `root`: root node/settings handle

### Root Settings

```python
root.value("name").setValue("test")
root.value("frame_start").setValue(1001)
root.value("frame_end").setValue(1010)
root.value("width").setValue(4096)
root.value("height").setValue(3024)
root.value("proxy_enabled").setValue(True)
root.value("viewer_max_width").setValue(1280)
root.value("viewer_max_height").setValue(720)
root.value("cache_memory_limit_mb").setValue(10240)
```

Aliases:

- `first` -> `frame_start`
- `last` -> `frame_end`
- `first_frame` -> `frame_start`
- `last_frame` -> `frame_end`
- `name` -> project name

### Create Nodes

```python
read = opencomp.create_node(
    "Read",
    name="Read_Plate",
    position=[120, 80],
    path=r"E:\plates\shot_####.exr",
    colorspace="ACES2065-1",
    frame_start=1001,
    frame_end=1010,
    read_all_channels=False,
)
```

### Find Nodes

```python
node = opencomp.node("Read_Plate")
viewer = opencomp.node("Viewer1")
root = opencomp.node("root")
```

If a name looks like a node type plus number, for example `Read2`, `opencomp.node("Read2")` can create that node when it does not exist.

### Set Values

```python
node.value("path").setValue(r"E:\plates\new_####.exr")
node.value("first_frame").setValue(1001)
node.value("last_frame").setValue(1010)
node.value("gain").setValue(1.2)
```

Aliases:

- `file` -> `path`
- `first` -> `frame_start`
- `last` -> `frame_end`
- `first_frame` -> `frame_start`
- `last_frame` -> `frame_end`

### Move Nodes

```python
node.setPosition(280, 120)
print(node.xpos(), node.ypos())
```

### Connect Nodes

```python
grade.setInput("in", read)
merge.setInput("a", foreground)
merge.setInput("b", background)
viewer.setInput("0", merge)
```

Equivalent session call:

```python
opencomp.connect(read, grade, input="in")
```

### Disconnect and Delete

```python
opencomp.disconnect(grade, input="in")
node.delete()
```

### Example Slapcomp Script

```python
for n in list(opencomp.nodes()):
    n.delete()

root.value("name").setValue("example_slapcomp")
root.value("frame_start").setValue(1001)
root.value("frame_end").setValue(1010)
root.value("width").setValue(4096)
root.value("height").setValue(3024)
root.value("working_colorspace").setValue("ACES2065-1")
root.value("proxy_enabled").setValue(True)
root.value("cache_memory_limit_mb").setValue(10240)

plate = opencomp.create_node(
    "Read",
    name="Read_Plate",
    position=[120, 80],
    path=r"E:\show\shot\plate_####.exr",
    colorspace="ACES2065-1",
    frame_start=1001,
    frame_end=1010,
    read_all_channels=False,
)

render = opencomp.create_node(
    "Read",
    name="Read_Render",
    position=[420, 80],
    path=r"E:\show\shot\render_####.exr",
    colorspace="ACES2065-1",
    frame_start=1001,
    frame_end=1010,
    read_all_channels=False,
)

grade = opencomp.create_node("Grade", name="Grade_Render", position=[420, 240], gain=1.1)
merge = opencomp.create_node("Merge", name="Merge_Render_over_Plate", position=[300, 420], operation="over")
viewer = opencomp.create_node("Viewer", name="Viewer1", position=[300, 600], active_input="0")
write = opencomp.create_node(
    "Write",
    name="Write_EXR",
    position=[560, 600],
    path=r"E:\show\shot\renders\slapcomp_####.exr",
    channels="rgba",
    create_directories=True,
)

grade.setInput("in", render)
merge.setInput("a", grade)
merge.setInput("b", plate)
viewer.setInput("0", merge)
write.setInput("in", merge)
```

## Node Catalog

```http
GET /api/nodes/catalog
```

Returns all registered node definitions:

```json
[
  {
    "type": "Read",
    "label": "Read",
    "category": "I/O",
    "inputs": [],
    "outputs": ["out"]
  }
]
```

## Metadata

```http
GET /api/nodes/{node_id}/metadata?frame=1001
```

Returns:

- image size
- pixel aspect
- display dimensions
- color space
- channel list
- format bbox
- data window
- cryptomatte layers
- metadata dictionary

## Color and OCIO

### Color Config

```http
GET /api/color/config
```

Returns OCIO availability, current config, builtin configs, colorspaces, displays, views, and default display/view.

### GPU Display Shader

```http
GET /api/color/gpu-shader?src=ACES2065-1&display=sRGB%20-%20Display&view=ACES%202.0%20-%20SDR%20100%20nits%20(Rec.709)
```

Returns generated GLSL shader data and optional LUT texture payloads.

## Viewer

### PNG Viewer Frame

```http
POST /api/viewer/frame
```

Body:

```json
{
  "node_id": "Viewer1",
  "frame": 1001,
  "display": "sRGB - Display",
  "view": "ACES 2.0 - SDR 100 nits (Rec.709)",
  "channel": "rgba",
  "viewer_input": "0",
  "gain": 1,
  "saturation": 1,
  "fstop": 0
}
```

Returns `image/png`.

### WebSocket PNG Viewer

```text
ws://127.0.0.1:8000/ws/viewer/frame
```

Send the same JSON payload as `/api/viewer/frame`. Receive PNG bytes.

### WebSocket Float Viewer

```text
ws://127.0.0.1:8000/ws/viewer/float
```

Payload:

```json
{
  "node_id": "Viewer1",
  "frame": 1001,
  "display": "sRGB - Display",
  "view": "ACES 2.0 - SDR 100 nits (Rec.709)",
  "channel": "rgba",
  "viewer_input": "0",
  "precision": "float16",
  "stream_tiles": true,
  "tile_height": 128
}
```

Response sequence:

1. JSON header:

```json
{
  "type": "viewer_float_frame",
  "width": 975,
  "height": 720,
  "source_width": 4096,
  "source_height": 3024,
  "pixel_aspect": 2,
  "colorspace": "ACES2065-1",
  "dtype": "float16",
  "layout": "rgba",
  "tile_stream": true,
  "tile_count": 6
}
```

2. For each tile:
   - JSON tile header
   - binary tile payload
3. Done message:

```json
{
  "type": "viewer_float_tiles_done",
  "tiles": 6
}
```

The frontend assembles the float buffer and uploads it to WebGL.

## Cryptomatte

### Layers

```http
GET /api/nodes/{node_id}/cryptomatte?frame=1001
```

### Pick ID

```http
POST /api/cryptomatte/pick
```

Body:

```json
{
  "node_id": "Read_3D",
  "frame": 1001,
  "layer": "VRayCryptomatte",
  "x": 120,
  "y": 340
}
```

### Build Matte Preview

```http
POST /api/cryptomatte/matte
```

Body:

```json
{
  "node_id": "Read_3D",
  "frame": 1001,
  "layer": "VRayCryptomatte",
  "matte_ids": ["3f800000"],
  "max_width": 1280,
  "max_height": 720
}
```

Returns `image/png`.

## Render

```http
POST /api/render
```

Body:

```json
{
  "node_id": "Write1",
  "frame": 1001
}
```

Current behavior is single-frame render through a Write node.

## Headless CLI

The same backend project model and evaluator are available through:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render Write1 --range 1001-1005
opencomp E:\show\shot\comp.opencomp --list-nodes
```

See [CLI](CLI.md) for range syntax, scripting, metadata inspection, cache flags, and Nuke export.

## Cache

### Status

```http
GET /api/cache/status
```

Returns:

- cache entry counts
- memory bytes
- hit/miss counts
- cached frames
- active nodes
- node timings
- preview timings
- phase timings
- request timings

### Clear

```http
POST /api/cache/clear
```

Clears backend node, PNG preview, and float preview caches.

The frontend Clear Cache button also clears the browser viewer cache.

## Customizing App Behavior

Current customization points:

- Project preferences:
  - cache memory limit
  - hotkeys
  - default read colorspace
  - path substitutions
  - custom init script paths
- Python script editor:
  - create nodes
  - move nodes
  - connect/disconnect
  - edit params
  - change root settings
- Plugin menu data:
  - stored in project model as `plugin_menu`
  - intended for future user plugin commands
- Startup scripts:
  - stored in project and script tab models
  - execution policy is still early-stage

Security note: Python scripts currently run inside the backend process. Treat this as a trusted local automation feature, not a sandbox.
