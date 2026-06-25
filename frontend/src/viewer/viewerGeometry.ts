/*
 * Pure viewer-geometry helpers shared by the viewer panel.
 * These functions keep coordinate transforms, ROI math, wipe-line checks,
 * and small viewer labels out of the React component so the UI file stays
 * focused on state and event orchestration instead of low-level math.
 */

import type { SourceSize } from "./viewerMetadata";

export type ViewerTransform = {
  x: number;
  y: number;
  scale: number;
};

export type DraftPoint = {
  x: number;
  y: number;
};

export type ViewerRoi = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type RoiDragMode =
  | "draw"
  | "move"
  | "left"
  | "right"
  | "top"
  | "bottom"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

export type ViewerViewport = {
  width: number;
  height: number;
};

export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function compactCacheStatus(status: string) {
  const match = status.match(/^cache:\s*([^|]+)/i);
  return match ? `cache ${match[1].trim()}` : "cache";
}

export function displaySize(source: SourceSize, pixelAspect: number) {
  return {
    width: source.width * pixelAspect,
    height: source.height,
  };
}

export function fitViewerTransform(viewport: ViewerViewport, source: SourceSize, pixelAspect: number): ViewerTransform {
  const size = displaySize(source, pixelAspect);
  const scale = Math.min(viewport.width / size.width, viewport.height / size.height) * 0.94;
  const nextScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  return {
    scale: nextScale,
    x: (viewport.width - size.width * nextScale) / 2,
    y: (viewport.height - size.height * nextScale) / 2,
  };
}

export function oneToOneViewerTransform(viewport: ViewerViewport, source: SourceSize, pixelAspect: number): ViewerTransform {
  const size = displaySize(source, pixelAspect);
  return {
    scale: 1,
    x: (viewport.width - size.width) / 2,
    y: (viewport.height - size.height) / 2,
  };
}

export function zoomViewerTransform(current: ViewerTransform, center: DraftPoint, factor: number): ViewerTransform {
  const worldX = (center.x - current.x) / current.scale;
  const worldY = (center.y - current.y) / current.scale;
  const scale = clamp(current.scale * factor, 0.05, 16);
  return {
    scale,
    x: center.x - worldX * scale,
    y: center.y - worldY * scale,
  };
}

export function screenToImage(
  mouseX: number,
  mouseY: number,
  transform: ViewerTransform,
  pixelAspect: number,
  imageSize: SourceSize,
): DraftPoint | null {
  if (imageSize.width <= 0 || imageSize.height <= 0) return null;
  const x = (mouseX - transform.x) / transform.scale / pixelAspect;
  const y = (mouseY - transform.y) / transform.scale;
  if (x < 0 || y < 0 || x >= imageSize.width || y >= imageSize.height) return null;
  return { x, y };
}

export function roiHitTest(
  mouseX: number,
  mouseY: number,
  roi: ViewerRoi | null,
  transform: ViewerTransform,
  pixelAspect: number,
): { mode: RoiDragMode } | null {
  if (!roi) return null;
  const x = transform.x + roi.x * pixelAspect * transform.scale;
  const y = transform.y + roi.y * transform.scale;
  const width = roi.width * pixelAspect * transform.scale;
  const height = roi.height * transform.scale;
  const edge = 8;
  const inside = mouseX >= x && mouseX <= x + width && mouseY >= y && mouseY <= y + height;
  if (!inside) return null;
  const nearLeft = Math.abs(mouseX - x) <= edge;
  const nearRight = Math.abs(mouseX - (x + width)) <= edge;
  const nearTop = Math.abs(mouseY - y) <= edge;
  const nearBottom = Math.abs(mouseY - (y + height)) <= edge;
  if (nearLeft && nearTop) return { mode: "top-left" };
  if (nearRight && nearTop) return { mode: "top-right" };
  if (nearLeft && nearBottom) return { mode: "bottom-left" };
  if (nearRight && nearBottom) return { mode: "bottom-right" };
  if (nearLeft) return { mode: "left" };
  if (nearRight) return { mode: "right" };
  if (nearTop) return { mode: "top" };
  if (nearBottom) return { mode: "bottom" };
  return { mode: "move" };
}

export function normalizeViewerRoi(start: DraftPoint, end: DraftPoint, source: SourceSize): ViewerRoi {
  const x0 = clamp(Math.floor(Math.min(start.x, end.x)), 0, Math.max(0, source.width - 1));
  const y0 = clamp(Math.floor(Math.min(start.y, end.y)), 0, Math.max(0, source.height - 1));
  const x1 = clamp(Math.ceil(Math.max(start.x, end.x)), x0 + 1, source.width);
  const y1 = clamp(Math.ceil(Math.max(start.y, end.y)), y0 + 1, source.height);
  return { x: x0, y: y0, width: Math.max(1, x1 - x0), height: Math.max(1, y1 - y0) };
}

export function updateViewerRoiFromDrag(
  drag: { mode: RoiDragMode; startPoint: DraftPoint; startRoi: ViewerRoi | null },
  point: DraftPoint,
  source: SourceSize,
): ViewerRoi {
  if (!drag.startRoi || drag.mode === "draw") {
    return normalizeViewerRoi(drag.startPoint, point, source);
  }
  const start = drag.startRoi;
  const dx = Math.round(point.x - drag.startPoint.x);
  const dy = Math.round(point.y - drag.startPoint.y);
  let left = start.x;
  let top = start.y;
  let right = start.x + start.width;
  let bottom = start.y + start.height;
  if (drag.mode === "move") {
    const width = start.width;
    const height = start.height;
    left = clamp(start.x + dx, 0, Math.max(0, source.width - width));
    top = clamp(start.y + dy, 0, Math.max(0, source.height - height));
    return { x: left, y: top, width, height };
  }
  if (drag.mode.includes("left")) left = clamp(start.x + dx, 0, right - 1);
  if (drag.mode.includes("right")) right = clamp(start.x + start.width + dx, left + 1, source.width);
  if (drag.mode.includes("top")) top = clamp(start.y + dy, 0, bottom - 1);
  if (drag.mode.includes("bottom")) bottom = clamp(start.y + start.height + dy, top + 1, source.height);
  return { x: left, y: top, width: Math.max(1, right - left), height: Math.max(1, bottom - top) };
}

export function isNearWipeLine(
  mouseX: number,
  mouseY: number,
  transform: ViewerTransform,
  source: SourceSize,
  pixelAspect: number,
  position: number,
  angle: number,
) {
  const size = displaySize(source, pixelAspect);
  const pointX = transform.x + size.width * clamp(position, 0, 1) * transform.scale;
  const pointY = transform.y + size.height * transform.scale * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const normalX = Math.cos(angleRad);
  const normalY = Math.sin(angleRad);
  const distance = Math.abs((mouseX - pointX) * normalX + (mouseY - pointY) * normalY);
  return distance <= 10;
}

export function wipePositionFromPointer(mouseX: number, transform: ViewerTransform, source: SourceSize, pixelAspect: number) {
  const size = displaySize(source, pixelAspect);
  const width = size.width * transform.scale;
  return clamp((mouseX - transform.x) / Math.max(width, 1), 0, 1);
}
