import type { NodeMetadata } from "../api/client";

export type SourceSize = {
  width: number;
  height: number;
};

export type ViewerBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ViewerBoxScale = {
  x: number;
  y: number;
};

export type ViewerContext = NodeMetadata["viewer_context"];

export function nodeMetadataPixelAspect(metadata: NodeMetadata | null | undefined): number {
  const pixelAspect = metadata?.pixel_aspect;
  return pixelAspect && pixelAspect > 0 ? pixelAspect : 1;
}

export function nodeMetadataFormatBox(metadata: NodeMetadata): ViewerBox {
  return metadata.format_bbox ?? { x: 0, y: 0, width: metadata.width, height: metadata.height };
}

export function nodeMetadataDataWindow(metadata: NodeMetadata): ViewerBox {
  return metadata.data_window ?? nodeMetadataFormatBox(metadata);
}

export function nodeMetadataScale(metadata: NodeMetadata, source: SourceSize): ViewerBoxScale {
  return {
    x: source.width / Math.max(metadata.width, 1),
    y: source.height / Math.max(metadata.height, 1),
  };
}

export function nodeMetadataUsesProxy(metadata: NodeMetadata | null | undefined, source: SourceSize | null | undefined): boolean {
  if (!metadata || !source) return false;
  return source.width < metadata.width || source.height < metadata.height;
}

export function nodeMetadataSummary(metadata: NodeMetadata): string {
  const parts = [`${metadata.width}x${metadata.height}`];
  const pixelAspect = nodeMetadataPixelAspect(metadata);
  if (pixelAspect !== 1) parts.push(`PA ${pixelAspect}`);
  parts.push(metadata.colorspace);
  return parts.join(" | ");
}

export function nodeMetadataViewerContext(metadata: NodeMetadata | null | undefined): ViewerContext | null {
  return metadata?.viewer_context ?? null;
}

export function nodeMetadataResolutionLabel(metadata: NodeMetadata | null | undefined): string {
  return nodeMetadataViewerContext(metadata)?.resolution_label ?? "viewer unavailable";
}

export function nodeMetadataDisplayViewLabel(metadata: NodeMetadata | null | undefined): string {
  const context = nodeMetadataViewerContext(metadata);
  if (!context) return "default / default";
  return `${context.display ?? "default"} / ${context.view ?? "default"}`;
}
