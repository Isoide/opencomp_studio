from __future__ import annotations

from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, require_input


class ViewerNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        active_input = str(node.params.get("active_input", "0"))
        return require_input(node, inputs, active_input)
