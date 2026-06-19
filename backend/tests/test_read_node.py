import numpy as np

from opencomp.core.models import Node, ProjectSettings
from opencomp.nodes.base import EvaluationContext
from opencomp.nodes.read import ReadNode


class IdentityOcio:
    pass


def _context(frame: int) -> EvaluationContext:
    return EvaluationContext(frame=frame, settings=ProjectSettings(width=4, height=3), ocio=IdentityOcio())  # type: ignore[arg-type]


def test_read_holds_frames_outside_range() -> None:
    node = Node(
        id="Read1",
        type="Read",
        params={"path": "builtin://gradient", "frame_start": 1001, "frame_end": 1010, "before": "hold", "after": "hold"},
    )

    result = ReadNode().evaluate(node, {}, _context(999))

    assert result.frame == 1001


def test_read_can_black_outside_range() -> None:
    node = Node(
        id="Read1",
        type="Read",
        params={"path": "builtin://gradient", "frame_start": 1001, "frame_end": 1010, "before": "black"},
    )

    result = ReadNode().evaluate(node, {}, _context(999))

    assert result.width == 4
    assert result.height == 3
    assert np.allclose(result.data, 0.0)
