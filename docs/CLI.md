# OpenComp Studio CLI

The backend package includes a headless command line interface for loading `.opencomp` projects, running project scripts, inspecting graphs, exporting Nuke scripts, and rendering Write nodes without the browser UI.

Use either form:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --list-nodes
opencomp E:\show\shot\comp.opencomp --list-nodes
```

The shorter `opencomp` command is available after installing the backend package into the Python environment.

## Project Files

OpenComp script/project files use the `.opencomp` extension. The file is JSON encoded and stores:

- project/root settings
- preferences
- active script id
- script tabs
- node graph nodes and edges
- plugin menu/startup script metadata

Save a project:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --save
```

Save to a new path:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --save E:\show\shot\comp_v002.opencomp
```

Create a new default project:

```powershell
python -m opencomp.cli --new --save E:\show\shot\new_comp.opencomp
```

## Rendering

Render one Write node:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render Write1 --range 1001-1005
```

Render multiple Write nodes:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render WriteMain,WritePreview --range 1001,1008,1010
```

Render all Write nodes:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render-all-writes --range 1001-1010
```

Range syntax:

```text
1001
1001-1005
1001,1002,1005
1001-1010x2
```

CLI rendering currently evaluates Write nodes through the same backend `GraphEvaluator` and `WriteNode` used by the app. Output paths come from each Write node's `path` parameter, using `####` frame padding.

## Scripting

Run a Python script file against the project:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --run-script E:\show\shot\setup_comp.py --save
```

Run inline code:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --eval "root.value('name').setValue('shot_comp')" --save
```

The script namespace matches the Script Editor:

```python
read = opencomp.node("Read1")
read.value("path").setValue(r"E:\plates\shot_####.exr")
read.value("first_frame").setValue(1001)
read.value("last_frame").setValue(1010)

grade = opencomp.create_node("Grade", name="Grade2")
grade.value("gain").setValue(1.15)
grade.setInput("in", read)

root.value("name").setValue("shot_comp")
```

## Overrides

Set root/project settings:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --set frame_start=1001 --set frame_end=1050 --save
```

Set node parameters:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --set Read1.path=E:\plates\shot_####.exr --set Grade1.gain=1.2 --save
```

## Inspection

List scripts:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --list-scripts
```

List nodes:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --list-nodes
```

Evaluate one node and print metadata:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --metadata Read1 --range 1001
```

JSON output:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render Write1 --range 1001 --json
```

## Nuke Export

Export a v1 `.nk`:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --export-nuke E:\show\shot\comp.nk
```

The exporter writes a Nuke root, common native node blocks, and stack-based `set`/`push` connections. Current native mappings include Read, Write, Constant, Grade, Merge2, Transform, Reformat, Viewer, OCIOColorSpace, and several color/channel nodes. Unsupported OpenComp node types are written as labeled NoOp nodes so the script can open and the graph shape is still visible.

## Cache Options

Set cache memory for a headless run:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render Write1 --range 1001-1010 --cache-mb 8192
```

Disable node cache:

```powershell
python -m opencomp.cli E:\show\shot\comp.opencomp --render Write1 --range 1001 --no-cache
```

The CLI does not use the browser viewer cache. It only uses backend node evaluation cache during the current process.
