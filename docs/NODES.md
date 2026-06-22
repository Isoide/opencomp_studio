# Nodes

Nodes are registered in `backend/opencomp/nodes/__init__.py` as `NodeDefinition` entries. Each definition exposes:

- type
- label
- category
- operation class
- input sockets
- output sockets

The frontend retrieves the catalog from `/api/nodes/catalog` and builds the node menu from the returned categories.

## Node Graph Model

Core graph objects live in `backend/opencomp/core/models.py`.

```text
Project
  ScriptTab[]
    ProjectGraph
      nodes: dict[str, Node]
      edges: list[Edge]
```

Each node has:

- `id`
- `type`
- `name`
- `position`
- `params`
- `inputs`
- `outputs`

Each edge has:

- `source_node`
- `source_socket`
- `target_node`
- `target_socket`

The graph is directed. Visual convention is top-to-bottom.

## Current Node Categories

### I/O

- Read
- Write

### Image

- Constant

### Color

- Grade
- Exposure
- Saturation
- Invert
- Clamp
- Colorspace

### Channel

- Shuffle
- Copy
- ChannelMerge
- AddChannels
- Remove
- Premult
- Unpremult

### Keyer

- Cryptomatte

### Merge

- Merge

### Transform

- Reformat
- Scale
- Transform

### Filter

- Blur

### Metadata

- ViewMetaData
- CompareMetaData
- Modify Metadata
- CopyMetaData
- AddTimeCode

### Organization

- Group

### Output

- Viewer

## Important Nodes

### Read

Read loads sources into `ImageFrame`.

Common params:

- `path` or `file`
- `colorspace`
- `frame_start`
- `frame_end`
- `before`
- `after`
- `missing_frames`
- `read_all_channels`
- `read_channels`

For EXR, Read parses:

- width/height
- pixel aspect ratio
- channels
- metadata
- data window
- display window

Default Read behavior is smart channel demand: RGBA is loaded for normal slapcomps, and extra AOVs are loaded when a downstream node or the viewer asks for them. Use `read_all_channels=True` or `read_channels="all"` for workflows that intentionally need every layer loaded immediately.

### Write

Write renders its input to disk.

Common params:

- `path` or `file`
- `channels`
- `file_type`
- `create_directories`
- `overwrite`
- `metadata`
- `limit_to_range`
- `frame_start`
- `frame_end`

Supported output formats are EXR, PNG, JPG, and JPEG.

### Viewer

Viewer is a node with inputs `0` through `9`.

Viewer behavior:

- active input determines what upstream branch is evaluated
- viewer input switching is intended to be instant when frames are cached
- viewer process controls are not graph state
- viewer supports proxy/full-res preview
- viewer supports pixel readout, bbox overlay, playback, and cache indicators

### Merge

Merge expects A and B inputs.

Common sockets:

- `a`: foreground
- `b`: background
- `mask`: optional mask

Supported operations include `over`, `under`, `plus`, `minus`, `difference`, `multiply`, `screen`, `copy`, `matte`, and others.

### Reformat

Reformat changes image dimensions.

Current implementation:

- bilinear resize per float channel
- bbox/data-window scaling
- sparse data-window optimization
- optional auxiliary channel preservation

### Grade

Grade applies scene-linear RGB math:

- gain
- multiply
- offset
- add
- gamma

### Cryptomatte

Cryptomatte reads layer metadata and pixel ID channels from loaded channel data.

Capabilities:

- list cryptomatte layers
- preview IDs with varied colors
- pick ID by viewer pixel
- build matte from selected IDs

## Node Evaluation Contract

Each backend node operation implements:

```python
evaluate(node: Node, inputs: dict[str, ImageFrame], context: EvaluationContext) -> ImageFrame
```

The evaluator:

1. Finds incoming edges.
2. Evaluates upstream nodes.
3. Provides evaluated `ImageFrame` inputs to the node.
4. Records node runtime metrics.
5. Stores non-viewer results in the node cache.

## Adding a Backend Node

1. Create a node class in `backend/opencomp/nodes/`.
2. Implement `evaluate`.
3. Register it in `NODE_DEFINITIONS`.
4. Add tests.
5. If it needs custom frontend controls, update the inspector UI.

Minimal example:

```python
class MyNode:
    def evaluate(self, node, inputs, context):
        source = require_input(node, inputs)
        data = source.data.copy()
        data[:, :, :3] *= float(node.params.get("gain", 1.0))
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=data,
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "node": node.id},
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )
```

Register:

```python
NodeDefinition("MyNode", "My Node", "Color", MyNode(), ("in",))
```

## Current Node Limitations

- Many nodes are v1 approximations of Nuke-like behavior.
- Some knobs are simplified.
- Auxiliary channels require loaded `channel_data`.
- Most operations process full arrays, even when bbox/data-window is smaller.
- Group nodes are structural placeholders and not full nested graphs yet.
- Viewer is deeply integrated into app state; multiple viewers are not fully production-grade yet.
