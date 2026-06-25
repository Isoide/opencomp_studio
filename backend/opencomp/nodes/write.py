from __future__ import annotations

from opencomp.core.models import ImageFrame, Node
from opencomp.io.image_writer import write_image
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class WriteNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        if _outside_limited_range(node, context.frame):
            return ImageFrame(
                width=source.width,
                height=source.height,
                data=source.data.copy(),
                channels=source.channels,
                channel_data=source.copy_channel_data(),
                pixel_aspect=source.pixel_aspect,
                colorspace=source.colorspace,
                frame=context.frame,
                metadata={**source.metadata, "write/skipped": "outside limit_to_range", "write/node": node.id},
                format_bbox=source.format_bbox,
                data_window=source.data_window,
            )
        path = str(node.params.get("path") or node.params.get("file") or context.settings.default_output_path)
        try:
            written_path = write_image(
                source,
                path,
                overwrite=bool(node.params.get("overwrite", True)),
                metadata_policy=str(node.params.get("metadata", "all")),
                channels=str(node.params.get("channels") or "rgba"),
                create_directories=bool(node.params.get("create_directories", True)),
                backend=context.settings.image_io_backend,
            )
        except Exception as exc:
            raise NodeEvaluationError(node.id, str(exc)) from exc
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=source.data.copy(),
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={**source.metadata, "write/filename": str(written_path), "write/node": node.id},
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )


def _outside_limited_range(node: Node, frame: int) -> bool:
    if not bool(node.params.get("limit_to_range", False)):
        return False
    try:
        first = int(node.params.get("frame_start") or node.params.get("first") or frame)
        last = int(node.params.get("frame_end") or node.params.get("last") or frame)
    except (TypeError, ValueError):
        return False
    return frame < first or frame > last
