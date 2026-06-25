from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from opencomp.color.ocio_engine import OCIOColorEngine
from opencomp.core.channel_demand import ChannelDemand
from opencomp.core.models import ImageFrame, Node, ProjectSettings


@dataclass(slots=True)
class EvaluationContext:
    frame: int
    settings: ProjectSettings
    ocio: OCIOColorEngine
    requested_channels: ChannelDemand | None = None
    metrics: Callable[[str, str, float, dict[str, Any] | None], None] | None = None
    evaluate_input_at: Callable[[str, int], ImageFrame] | None = None

    def record_metric(
        self,
        node_id: str,
        phase: str,
        duration_ms: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.metrics is not None:
            self.metrics(node_id, phase, duration_ms, details)

    def fetch_input(self, socket: str, frame: int) -> ImageFrame:
        if self.evaluate_input_at is None:
            raise RuntimeError("This node cannot request upstream frames in the current evaluation context.")
        return self.evaluate_input_at(socket, frame)


class NodeOperation(Protocol):
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        ...


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    type: str
    label: str
    category: str
    operation: NodeOperation
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ("out",)
    execution_capability: Literal["cpu_only", "vulkan_supported", "vulkan_preferred"] = "cpu_only"


class NodeEvaluationError(RuntimeError):
    def __init__(self, node_id: str, message: str) -> None:
        super().__init__(f"{node_id}: {message}")
        self.node_id = node_id
        self.message = message


def require_input(node: Node, inputs: dict[str, ImageFrame], socket: str = "in") -> ImageFrame:
    image = inputs.get(socket) or next(iter(inputs.values()), None)
    if image is None:
        raise NodeEvaluationError(node.id, f"Node '{node.type}' requires an input image.")
    return image
