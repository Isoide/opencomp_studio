from __future__ import annotations

import numpy as np

from opencomp.core.models import ImageFrame, Node
from opencomp.core.tile_engine import map_rgba_rows
from opencomp.nodes.base import EvaluationContext, require_input


class GradeNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        gain = float(node.params.get("gain", node.params.get("multiply", 1.0)))
        multiply = float(node.params.get("multiply", 1.0))
        offset = float(node.params.get("offset", node.params.get("add", 0.0)))
        add = float(node.params.get("add", 0.0))
        gamma = max(float(node.params.get("gamma", 1.0)), 1e-6)

        def grade_tile(tile: np.ndarray) -> np.ndarray:
            data = tile.copy()
            rgb = data[:, :, :3]
            rgb = (rgb * gain * multiply) + offset + add
            if gamma != 1.0:
                rgb = np.power(np.maximum(rgb, 0.0), 1.0 / gamma)
            data[:, :, :3] = rgb
            return data

        data = map_rgba_rows(source.data, context.settings, grade_tile)
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
