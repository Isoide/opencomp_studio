from opencomp.nodes.channel import (
    BlurNode,
    AddChannelsNode,
    AddTimeCodeNode,
    ChannelMergeNode,
    ClampNode,
    CompareMetadataNode,
    ConstantNode,
    CopyNode,
    CopyMetadataNode,
    ExposureNode,
    GroupNode,
    InvertNode,
    ModifyMetadataNode,
    PremultNode,
    RemoveNode,
    SaturationNode,
    ShuffleNode,
    UnpremultNode,
    ViewMetadataNode,
)
from opencomp.nodes.colorspace import ColorspaceNode
from opencomp.nodes.crop import CropNode
from opencomp.nodes.cryptomatte import CryptomatteNode
from opencomp.nodes.grade import GradeNode
from opencomp.nodes.merge import MergeNode
from opencomp.nodes.read import ReadNode
from opencomp.nodes.reformat import ReformatNode
from opencomp.nodes.transform import ScaleNode, TransformNode
from opencomp.nodes.time_color import ColorCorrectNode, FrameHoldNode, FrameRangeNode, HueCorrectNode, RetimeNode
from opencomp.nodes.viewer import ViewerNode
from opencomp.nodes.write import WriteNode
from opencomp.nodes.base import NodeDefinition

NODE_DEFINITIONS = (
    NodeDefinition("Read", "Read", "I/O", ReadNode()),
    NodeDefinition("Write", "Write", "I/O", WriteNode(), ("in",)),
    NodeDefinition("Constant", "Constant", "Image", ConstantNode()),
    NodeDefinition("Group", "Group", "Organization", GroupNode(), ("in",)),
    NodeDefinition("Grade", "Grade", "Color", GradeNode(), ("in",)),
    NodeDefinition("Exposure", "Exposure", "Color", ExposureNode(), ("in",)),
    NodeDefinition("Saturation", "Saturation", "Color", SaturationNode(), ("in",)),
    NodeDefinition("Invert", "Invert", "Color", InvertNode(), ("in",)),
    NodeDefinition("Clamp", "Clamp", "Color", ClampNode(), ("in",)),
    NodeDefinition("Colorspace", "Colorspace", "Color", ColorspaceNode(), ("in",)),
    NodeDefinition("Blur", "Blur", "Filter", BlurNode(), ("in",)),
    NodeDefinition("Crop", "Crop", "Transform", CropNode(), ("in",)),
    NodeDefinition("Reformat", "Reformat", "Transform", ReformatNode(), ("in",)),
    NodeDefinition("Scale", "Scale", "Transform", ScaleNode(), ("in",)),
    NodeDefinition("Transform", "Transform", "Transform", TransformNode(), ("in",)),
    NodeDefinition("FrameHold", "FrameHold", "Transform", FrameHoldNode(), ("in",)),
    NodeDefinition("FrameRange", "FrameRange", "Transform", FrameRangeNode(), ("in",)),
    NodeDefinition("Retime", "Retime", "Transform", RetimeNode(), ("in",)),
    NodeDefinition("Shuffle", "Shuffle", "Channel", ShuffleNode(), ("b", "a")),
    NodeDefinition("Copy", "Copy", "Channel", CopyNode(), ("a", "b")),
    NodeDefinition("ChannelMerge", "ChannelMerge", "Channel", ChannelMergeNode(), ("a", "b", "mask")),
    NodeDefinition("AddChannels", "AddChannels", "Channel", AddChannelsNode(), ("in",)),
    NodeDefinition("Remove", "Remove", "Channel", RemoveNode(), ("in",)),
    NodeDefinition("Premult", "Premult", "Channel", PremultNode(), ("in",)),
    NodeDefinition("Unpremult", "Unpremult", "Channel", UnpremultNode(), ("in",)),
    NodeDefinition("Cryptomatte", "Cryptomatte", "Keyer", CryptomatteNode(), ("in",)),
    NodeDefinition("ColorCorrect", "ColorCorrect", "Color", ColorCorrectNode(), ("in",)),
    NodeDefinition("HueCorrect", "HueCorrect", "Color", HueCorrectNode(), ("in",)),
    NodeDefinition("ViewMetadata", "ViewMetaData", "Metadata", ViewMetadataNode(), ("in",)),
    NodeDefinition("CompareMetadata", "CompareMetaData", "Metadata", CompareMetadataNode(), ("a", "b")),
    NodeDefinition("ModifyMetadata", "Modify Metadata", "Metadata", ModifyMetadataNode(), ("in",)),
    NodeDefinition("CopyMetadata", "CopyMetaData", "Metadata", CopyMetadataNode(), ("a", "b")),
    NodeDefinition("AddTimeCode", "AddTimeCode", "Metadata", AddTimeCodeNode(), ("in",)),
    NodeDefinition("Merge", "Merge", "Merge", MergeNode(), ("b", "a", "mask", "a2", "a3", "a4", "a5")),
    NodeDefinition("Viewer", "Viewer", "Output", ViewerNode(), tuple(str(index) for index in range(10))),
)

NODE_REGISTRY = {definition.type.lower(): definition.operation for definition in NODE_DEFINITIONS}

__all__ = ["NODE_DEFINITIONS", "NODE_REGISTRY"]
