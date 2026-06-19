import numpy as np

from opencomp.core.models import ImageFrame, Node, ProjectSettings
from opencomp.nodes.base import EvaluationContext
from opencomp.nodes.grade import GradeNode


class IdentityOcio:
    pass


def test_grade_node_changes_rgb_and_preserves_alpha() -> None:
    data = np.ones((2, 2, 4), dtype=np.float32)
    data[:, :, :3] = 0.5
    frame = ImageFrame(width=2, height=2, data=data, colorspace="ACEScg", frame=1001)
    node = Node(id="Grade1", type="Grade", params={"gain": 2.0, "offset": 0.1, "gamma": 1.0})
    context = EvaluationContext(frame=1001, settings=ProjectSettings(), ocio=IdentityOcio())  # type: ignore[arg-type]

    result = GradeNode().evaluate(node, {"in": frame}, context)

    assert np.allclose(result.data[:, :, :3], 1.1)
    assert np.allclose(result.data[:, :, 3], 1.0)
