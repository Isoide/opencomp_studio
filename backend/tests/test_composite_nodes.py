import numpy as np

from opencomp.core.models import ImageFrame, Node, ProjectSettings
from opencomp.io.image_reader import read_image
from opencomp.nodes.base import EvaluationContext
from opencomp.nodes.channel import AddChannelsNode, ChannelMergeNode, CopyNode, ModifyMetadataNode, PremultNode, RemoveNode, ShuffleNode
from opencomp.nodes.merge import MergeNode
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
