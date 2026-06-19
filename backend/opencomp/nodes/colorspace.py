from __future__ import annotations

import time

from opencomp.core.models import ImageFrame, Node
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class ColorspaceNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        src = str(node.params.get("src") or source.colorspace)
        dst = str(node.params.get("dst") or context.settings.working_colorspace)
        try:
            started = time.perf_counter()
            data = context.ocio.convert_colorspace(source.data, src, dst)
            context.record_metric(
                node.id,
                "ocio.colorspace",
                (time.perf_counter() - started) * 1000.0,
                {"src": src, "dst": dst, "width": source.width, "height": source.height},
            )
        except Exception as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=data,
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=dst,
            frame=context.frame,
            metadata={**source.metadata, "colorspace_src": src, "colorspace_dst": dst},
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )
