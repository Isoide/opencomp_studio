import numpy as np

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, ImageFrame, Node, ProjectGraph, ProjectSettings, TileWindow
from opencomp.io.image_reader import read_image
from opencomp.nodes.base import EvaluationContext
from opencomp.nodes.channel import AddChannelsNode, ChannelMergeNode, CopyNode, ModifyMetadataNode, PremultNode, RemoveNode, ShuffleNode
from opencomp.nodes.crop import CropNode
from opencomp.nodes.merge import MergeNode
from opencomp.nodes.reformat import ReformatNode
from opencomp.nodes.transform import TransformNode
from opencomp.nodes.write import WriteNode


class IdentityOcio:
    pass


def _context() -> EvaluationContext:
    return EvaluationContext(frame=1001, settings=ProjectSettings(), ocio=IdentityOcio())  # type: ignore[arg-type]


def _frame(color: list[float]) -> ImageFrame:
    data = np.zeros((2, 2, 4), dtype=np.float32)
    data[:, :] = color
    return ImageFrame(width=2, height=2, data=data, colorspace="ACEScg", frame=1001)


def test_merge_multiply_operation() -> None:
    a = _frame([0.5, 0.25, 0.1, 1.0])
    b = _frame([0.2, 0.4, 0.8, 1.0])
    node = Node(id="Merge1", type="Merge", params={"operation": "multiply"})

    result = MergeNode().evaluate(node, {"a": a, "b": b}, _context())

    assert np.allclose(result.data[0, 0, :3], [0.1, 0.1, 0.08])
    assert np.allclose(result.data[:, :, 3], 1.0)


def test_merge_stacks_multiple_a_inputs_over_b_in_order() -> None:
    a1 = _frame([0.5, 0.0, 0.0, 0.5])
    a2 = _frame([0.0, 0.5, 0.0, 0.5])
    b = _frame([0.0, 0.0, 0.5, 1.0])
    node = Node(id="Merge1", type="Merge", params={"operation": "over"})

    result = MergeNode().evaluate(node, {"a": a1, "a2": a2, "b": b}, _context())
    chained = MergeNode().evaluate(
        node,
        {"a": a2, "b": MergeNode().evaluate(node, {"a": a1, "b": b}, _context())},
        _context(),
    )

    assert np.allclose(result.data, chained.data)


def test_merge_extra_blend_modes() -> None:
    a = _frame([0.25, 0.5, 0.75, 1.0])
    b = _frame([0.8, 0.4, 0.2, 1.0])

    exclusion = MergeNode().evaluate(Node(id="Merge1", type="Merge", params={"operation": "exclusion"}), {"a": a, "b": b}, _context())
    grain_merge = MergeNode().evaluate(Node(id="Merge2", type="Merge", params={"operation": "grain_merge"}), {"a": a, "b": b}, _context())

    assert np.allclose(exclusion.data[0, 0, :3], [0.65, 0.5, 0.65])
    assert np.allclose(grain_merge.data[0, 0, :3], [0.55, 0.4, 0.45])


def test_merge_tiled_matches_untiled_operation() -> None:
    height = 128
    width = 64
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    a_data = np.stack([x / width, y / height, np.full_like(x, 0.25), np.full_like(x, 0.4)], axis=-1)
    b_data = np.stack([np.full_like(x, 0.2), x / width, y / height, np.ones_like(x)], axis=-1)
    a = ImageFrame(width=width, height=height, data=a_data, colorspace="ACEScg", frame=1001)
    b = ImageFrame(width=width, height=height, data=b_data, colorspace="ACEScg", frame=1001)
    node = Node(id="Merge1", type="Merge", params={"operation": "over"})
    tiled_context = EvaluationContext(
        frame=1001,
        settings=ProjectSettings(tile_rendering_enabled=True, tile_height=16, tile_workers=2),
        ocio=IdentityOcio(),  # type: ignore[arg-type]
    )
    untiled_context = EvaluationContext(
        frame=1001,
        settings=ProjectSettings(tile_rendering_enabled=False),
        ocio=IdentityOcio(),  # type: ignore[arg-type]
    )

    tiled = MergeNode().evaluate(node, {"a": a, "b": b}, tiled_context)
    untiled = MergeNode().evaluate(node, {"a": a, "b": b}, untiled_context)

    assert np.allclose(tiled.data, untiled.data)


def test_tile_evaluator_matches_full_frame_for_local_graph() -> None:
    graph = ProjectGraph(
        nodes={
            "A": Node(id="A", type="Constant", params={"width": 64, "height": 32, "r": 0.25, "g": 0.5, "b": 0.75, "a": 0.4}),
            "B": Node(id="B", type="Constant", params={"width": 64, "height": 32, "r": 0.1, "g": 0.2, "b": 0.3, "a": 1.0}),
            "Merge1": Node(id="Merge1", type="Merge", params={"operation": "over"}),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.5, "offset": 0.05}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="a-merge", source_node="A", target_node="Merge1", target_socket="a"),
            Edge(id="b-merge", source_node="B", target_node="Merge1", target_socket="b"),
            Edge(id="merge-grade", source_node="Merge1", target_node="Grade1", target_socket="in"),
            Edge(id="grade-viewer", source_node="Grade1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True, tile_workers=2))
    full = evaluator.evaluate_node(graph, "Viewer1", 1001)
    evaluator.clear_cache()

    tile = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(7, 5, 19, 11))

    assert tile.metadata["tile/full_width"] == full.width
    assert tile.metadata["tile/full_height"] == full.height
    assert np.allclose(tile.data, full.data[5:16, 7:26])
    assert evaluator.cache_snapshot()["entries"] == 0


def test_disabled_merge_bypasses_b_input_without_evaluating_a() -> None:
    graph = ProjectGraph(
        nodes={
            "BrokenA": Node(id="BrokenA", type="DefinitelyUnsupported"),
            "B": Node(id="B", type="Constant", params={"width": 8, "height": 6, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0}),
            "Merge1": Node(id="Merge1", type="Merge", params={"operation": "over", "disabled": True}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="a-merge", source_node="BrokenA", target_node="Merge1", target_socket="a"),
            Edge(id="b-merge", source_node="B", target_node="Merge1", target_socket="b"),
            Edge(id="merge-viewer", source_node="Merge1", target_node="Viewer1", target_socket="0"),
        ],
    )
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, tile_rendering_enabled=True, tile_workers=2))

    full = evaluator.evaluate_node(graph, "Viewer1", 1001)
    tile = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(2, 1, 3, 2))

    assert np.allclose(full.data[:, :, :3], [0.2, 0.4, 0.6])
    assert full.metadata["node/bypassed"] == "Merge1"
    assert np.allclose(tile.data[:, :, :3], [0.2, 0.4, 0.6])
    assert tile.metadata["node/bypassed"] == "Merge1"


def test_crop_black_outside_updates_data_window() -> None:
    source = _frame([0.2, 0.4, 0.6, 1.0])
    source.data_window = {"x": 0, "y": 0, "width": 2, "height": 2}
    source.data[0, 0] = [1.0, 1.0, 1.0, 1.0]
    node = Node(id="Crop1", type="Crop", params={"x": 1, "y": 0, "width": 1, "height": 2, "black_outside": True})

    result = CropNode().evaluate(node, {"in": source}, _context())

    assert np.allclose(result.data[:, 0], 0.0)
    assert np.allclose(result.data[:, 1], source.data[:, 1])
    assert result.data_window == {"x": 1, "y": 0, "width": 1, "height": 2}


def test_crop_reformat_shifts_crop_to_origin() -> None:
    data = np.zeros((3, 4, 4), dtype=np.float32)
    data[1, 2] = [0.7, 0.2, 0.1, 1.0]
    source = ImageFrame(width=4, height=3, data=data, colorspace="ACEScg", frame=1001)
    node = Node(id="Crop1", type="Crop", params={"x": 2, "y": 1, "width": 2, "height": 2, "reformat": True})

    result = CropNode().evaluate(node, {"in": source}, _context())

    assert (result.width, result.height) == (2, 2)
    assert np.allclose(result.data[0, 0], [0.7, 0.2, 0.1, 1.0])
    assert result.format_bbox == {"x": 0, "y": 0, "width": 2, "height": 2}


def test_transform_translation_updates_data_window() -> None:
    source = _frame([0.1, 0.2, 0.3, 1.0])
    source.data_window = {"x": 0, "y": 0, "width": 2, "height": 2}
    node = Node(id="Transform1", type="Transform", params={"translate_x": 1, "translate_y": 0, "scale": 1, "filter": "nearest"})

    result = TransformNode().evaluate(node, {"in": source}, _context())

    assert result.data_window == {"x": 1, "y": 0, "width": 2, "height": 2}
    assert np.allclose(result.data[:, 1], source.data[:, 0])


def test_reformat_fit_preserves_aspect_inside_target() -> None:
    data = np.ones((2, 4, 4), dtype=np.float32)
    source = ImageFrame(width=4, height=2, data=data, colorspace="ACEScg", frame=1001)
    node = Node(id="Reformat1", type="Reformat", params={"width": 8, "height": 8, "resize": "fit", "centered": True})

    result = ReformatNode().evaluate(node, {"in": source}, _context())

    assert (result.width, result.height) == (8, 8)
    assert result.data_window == {"x": 0, "y": 2, "width": 8, "height": 4}
    assert np.allclose(result.data[0:2], 0.0)


def test_merge_can_take_metadata_from_b() -> None:
    a = _frame([1.0, 0.0, 0.0, 0.5])
    b = _frame([0.0, 0.0, 1.0, 1.0])
    a.metadata["input/filename"] = "foreground.exr"
    b.metadata["input/filename"] = "background.exr"
    node = Node(id="Merge1", type="Merge", params={"operation": "over", "metadata_from": "b"})

    result = MergeNode().evaluate(node, {"a": a, "b": b}, _context())

    assert result.metadata["input/filename"] == "background.exr"
    assert result.metadata["merge/a"] == "foreground.exr"
    assert result.metadata["merge/b"] == "background.exr"


def test_shuffle_can_copy_alpha_to_rgb() -> None:
    source = _frame([0.1, 0.2, 0.3, 0.75])
    node = Node(
        id="Shuffle1",
        type="Shuffle",
        params={"out_r": "a", "out_g": "a", "out_b": "a", "out_a": "white"},
    )

    result = ShuffleNode().evaluate(node, {"in": source}, _context())

    assert np.allclose(result.data[0, 0], [0.75, 0.75, 0.75, 1.0])


def test_copy_replaces_b_channels_with_a_channels() -> None:
    a = _frame([0.9, 0.1, 0.2, 0.25])
    b = _frame([0.1, 0.2, 0.3, 1.0])
    node = Node(id="Copy1", type="Copy", params={"from0": "rgba.alpha", "to0": "rgba.alpha"})

    result = CopyNode().evaluate(node, {"a": a, "b": b}, _context())

    assert np.allclose(result.data[0, 0], [0.1, 0.2, 0.3, 0.25])


def test_channelmerge_writes_one_merged_channel_over_b() -> None:
    a = _frame([0.0, 0.0, 0.0, 0.25])
    b = _frame([0.0, 0.0, 0.0, 0.5])
    node = Node(
        id="ChannelMerge1",
        type="ChannelMerge",
        params={"a_channel": "rgba.alpha", "b_channel": "rgba.alpha", "operation": "union", "output": "rgba.alpha"},
    )

    result = ChannelMergeNode().evaluate(node, {"a": a, "b": b}, _context())

    assert np.allclose(result.data[:, :, 3], 0.625)


def test_addchannels_and_remove_channel_sets() -> None:
    source = _frame([0.1, 0.2, 0.3, 1.0])
    add_node = Node(id="AddChannels1", type="AddChannels", params={"channels": "mask.alpha", "color": 0.75})
    remove_node = Node(id="Remove1", type="Remove", params={"operation": "keep", "channels": "mask.alpha"})

    added = AddChannelsNode().evaluate(add_node, {"in": source}, _context())
    removed = RemoveNode().evaluate(remove_node, {"in": added}, _context())

    assert "mask.alpha" in added.channels
    assert np.allclose(added.channel_data["mask"][:, :, 3], 0.75)
    assert np.allclose(removed.data, 0.0)
    assert "mask.alpha" in removed.channels


def test_premult_multiplies_rgb_by_alpha() -> None:
    source = _frame([0.8, 0.6, 0.4, 0.5])
    node = Node(id="Premult1", type="Premult", params={})

    result = PremultNode().evaluate(node, {"in": source}, _context())

    assert np.allclose(result.data[0, 0], [0.4, 0.3, 0.2, 0.5])


def test_modify_metadata_and_write_sequence_png(tmp_path) -> None:
    source = _frame([0.2, 0.4, 0.6, 1.0])
    source.metadata["input/filename"] = "plate.exr"
    metadata_node = Node(
        id="Meta1",
        type="ModifyMetadata",
        params={"action": "set", "key": "shot/name", "value": "LAL_101"},
    )
    write_node = Node(
        id="Write1",
        type="Write",
        params={"path": str(tmp_path / "render.####.png"), "overwrite": True, "metadata": "all"},
    )

    modified = ModifyMetadataNode().evaluate(metadata_node, {"in": source}, _context())
    result = WriteNode().evaluate(write_node, {"in": modified}, _context())

    output_path = tmp_path / "render.1001.png"
    assert output_path.exists()
    assert result.metadata["write/filename"] == str(output_path)
    assert result.metadata["shot/name"] == "LAL_101"


def test_write_sequence_exr_round_trips_float_data(tmp_path) -> None:
    source = _frame([1.25, 0.4, 0.1, 0.5])
    source.pixel_aspect = 2.0
    source.metadata["shot/name"] = "LAL_101"
    write_node = Node(
        id="Write1",
        type="Write",
        params={"path": str(tmp_path / "render.####.exr"), "overwrite": True, "metadata": "all"},
    )

    result = WriteNode().evaluate(write_node, {"in": source}, _context())
    reread = read_image(str(tmp_path / "render.####.exr"), frame=1001, colorspace="ACEScg")

    assert (tmp_path / "render.1001.exr").exists()
    assert result.metadata["write/filename"] == str(tmp_path / "render.1001.exr")
    assert reread.width == source.width
    assert reread.height == source.height
    assert reread.pixel_aspect == 2.0
    assert np.allclose(reread.data[0, 0], source.data[0, 0])
    assert reread.metadata["exr/opencomp.shot_name"] == "LAL_101"
