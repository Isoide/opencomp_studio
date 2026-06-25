/*
 * Viewer float-frame helpers shared across the viewer UI and WebGL renderer.
 * These accessors keep transport header details in one place so callers can
 * work with direct concepts like dimensions, format keys, and render revisions.
 */

import type { FloatViewerFrame } from "../api/client";

export type ViewerFrameSize = {
  width: number;
  height: number;
};

export function viewerFrameDimensions(frame: FloatViewerFrame | null | undefined): ViewerFrameSize | null {
  if (!frame) return null;
  return {
    width: frame.header.width,
    height: frame.header.height,
  };
}

export function viewerFramePixelAspect(frame: FloatViewerFrame | null | undefined): number | null {
  const pixelAspect = frame?.header.pixel_aspect;
  return pixelAspect && pixelAspect > 0 ? pixelAspect : null;
}

export function viewerFrameTileProgress(frame: FloatViewerFrame | null | undefined): string | null {
  if (!frame?.header.partial) return null;
  return `tiles ${frame.header.tiles_received ?? 0}/${frame.header.tile_count ?? 0}`;
}

export function viewerFrameFormatKey(frame: FloatViewerFrame, pixelAspect = 1): string {
  return `${frame.header.width}x${frame.header.height}@${pixelAspect}`;
}

export function viewerFrameTransport(frame: FloatViewerFrame): string {
  return `webgl-${frame.header.dtype}${frame.header.tile_stream ? "-tiles" : ""}`;
}

export function viewerFrameByteLength(frame: FloatViewerFrame): number {
  return frame.header.byte_length || frame.pixels.byteLength;
}

export function viewerFrameRenderKey(frameA: FloatViewerFrame, frameB: FloatViewerFrame | null): string {
  const a = frameA.header;
  const b = frameB?.header;
  return [
    a.node_id,
    a.viewer_input ?? "",
    a.frame,
    a.channel,
    a.dtype,
    `${a.width}x${a.height}`,
    a.byte_length,
    b?.node_id ?? "",
    b?.viewer_input ?? "",
    b?.frame ?? "",
    b?.channel ?? "",
    b?.dtype ?? "",
    b ? `${b.width}x${b.height}` : "",
    b?.byte_length ?? "",
  ].join("|");
}

export function viewerFrameIsFinal(frame: FloatViewerFrame | null | undefined): boolean {
  if (!frame) return false;
  if (!frame.header.partial) return true;
  return !frame.header.tile_stream && Boolean(frame.header.roi);
}

export function ocioShaderCacheKey(
  frame: FloatViewerFrame,
  display: string | null,
  view: string | null,
): string {
  return `${frame.header.colorspace}|${display ?? ""}|${view ?? ""}`;
}
