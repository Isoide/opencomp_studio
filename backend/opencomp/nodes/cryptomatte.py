from __future__ import annotations

import re

import numpy as np

from opencomp.core.models import ImageFrame, Node
from opencomp.io.cryptomatte import build_cryptomatte_matte, cryptomatte_layers
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError, require_input


class CryptomatteNode:
    def evaluate(
        self,
        node: Node,
        inputs: dict[str, ImageFrame],
        context: EvaluationContext,
    ) -> ImageFrame:
        source = require_input(node, inputs)
        layers = cryptomatte_layers(source)
        if not layers:
            raise NodeEvaluationError(node.id, "Input has no Cryptomatte metadata.")

        layer_name = str(node.params.get("layer") or layers[0].name)
        matte_ids = _matte_tokens(str(node.params.get("matte_list") or ""))
        matte = build_cryptomatte_matte(source, layer_name, matte_ids, settings=context.settings)

        data = source.data.copy()
        mode = str(node.params.get("output") or "alpha").lower()
        if mode == "matte":
            data[:, :, 0] = matte
            data[:, :, 1] = matte
            data[:, :, 2] = matte
            data[:, :, 3] = 1.0
        else:
            data[:, :, 3] = matte
        return ImageFrame(
            width=source.width,
            height=source.height,
            data=data,
            channels=source.channels,
            channel_data=source.copy_channel_data(),
            pixel_aspect=source.pixel_aspect,
            colorspace=source.colorspace,
            frame=context.frame,
            metadata={
                **source.metadata,
                "cryptomatte/layer": layer_name,
                "cryptomatte/matte_list": matte_ids,
                "cryptomatte/output": mode,
            },
            format_bbox=source.format_bbox,
            data_window=source.data_window,
        )


def _matte_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", value.strip()) if token]
