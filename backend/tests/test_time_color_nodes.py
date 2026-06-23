import numpy as np

from opencomp.core.models import ImageFrame, Node, ProjectSettings
from opencomp.nodes.base import EvaluationContext
from opencomp.nodes.time_color import ColorCorrectNode, FrameRangeNode, HueCorrectNode, RetimeNode


class IdentityOcio:
    pass


def _context(frame: int) -> EvaluationContext:
    return EvaluationContext(frame=frame, settings=ProjectSettings(frame_start=1001, frame_end=1010), ocio=IdentityOcio())  # type: ignore[arg-type]


def _frame(color: list[float]) -> ImageFrame:
    data = np.zeros((2, 2, 4), dtype=np.float32)
    data[:, :] = color
    return ImageFrame(width=2, height=2, data=data, colorspace="ACEScg", frame=1001)


def test_framerange_black_mode_returns_black() -> None:
    node = Node(id="FrameRange1", type="FrameRange", params={"frame_start": 1001, "frame_end": 1010, "mode": "black"})

    result = FrameRangeNode().evaluate(node, {}, _context(999))

    assert np.allclose(result.data, 0.0)


def test_retime_linear_blends_neighbor_frames() -> None:
    first = _frame([0.0, 0.0, 0.0, 1.0])
    second = _frame([1.0, 1.0, 1.0, 1.0])
    node = Node(id="Retime1", type="Retime", params={"speed": 0.5, "filter": "linear", "src_start": 1001, "src_end": 1010})
    context = _context(1002)
    context.evaluate_input_at = lambda socket, frame: first if frame <= 1001 else second

    result = RetimeNode().evaluate(node, {}, context)

    assert np.allclose(result.data[0, 0, :3], [0.5, 0.5, 0.5])


def test_colorcorrect_changes_master_rgb() -> None:
    source = _frame([0.2, 0.4, 0.6, 1.0])
    node = Node(id="ColorCorrect1", type="ColorCorrect", params={"gain": 2.0, "offset": 0.1, "gamma": 1.0, "contrast": 1.0, "saturation": 1.0})

    result = ColorCorrectNode().evaluate(node, {"in": source}, _context(1001))

    assert np.allclose(result.data[0, 0, :3], [0.5, 0.9, 1.3])


def test_huecorrect_mixes_output() -> None:
    source = _frame([0.9, 0.2, 0.2, 1.0])
    node = Node(
        id="HueCorrect1",
        type="HueCorrect",
        params={"sat_points": [[0.0, 0.0], [1.0, 0.0]], "mix": 1.0},
    )

    result = HueCorrectNode().evaluate(node, {"in": source}, _context(1001))

    assert result.data[0, 0, 0] != source.data[0, 0, 0]
