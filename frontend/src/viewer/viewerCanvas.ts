/*
 * Canvas drawing helpers for the viewer panel.
 * The component owns scheduling and state, while this module keeps the
 * immediate-mode drawing code together so image overlays stay easier to scan.
 */

import type { DraftPoint, ViewerTransform } from "./viewerGeometry";
import { clamp } from "./viewerGeometry";

export function drawCheckerboard(ctx: CanvasRenderingContext2D, width: number, height: number) {
  const size = 16;
  for (let y = 0; y < height; y += size) {
    for (let x = 0; x < width; x += size) {
      const light = (x / size + y / size) % 2 === 0;
      ctx.fillStyle = light ? "#161616" : "#101010";
      ctx.fillRect(x, y, size, size);
    }
  }
}

export function resizeCanvas(canvas: HTMLCanvasElement, rect: DOMRect, ratio: number) {
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
}

export function drawResolutionBadge(
  ctx: CanvasRenderingContext2D,
  label: string,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  canvasWidth: number,
  canvasHeight: number,
) {
  const imageRight = transform.x + displayWidth * transform.scale;
  const imageBottom = transform.y + displayHeight * transform.scale;
  if (imageRight < 0 || imageBottom < 0 || transform.x > canvasWidth || transform.y > canvasHeight) return;

  ctx.save();
  ctx.font = '10px ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace';
  ctx.textBaseline = "middle";
  const width = Math.ceil(ctx.measureText(label).width) + 9;
  const height = 14;
  const outsideX = imageRight + 4;
  const insideX = imageRight - width - 4;
  const x = outsideX + width <= canvasWidth - 4 ? outsideX : insideX;
  const y = imageBottom + 2 + height <= canvasHeight - 4 ? imageBottom + 2 : imageBottom - height - 4;
  const clampedX = clamp(x, 4, Math.max(4, canvasWidth - width - 4));
  const clampedY = clamp(y, 4, Math.max(4, canvasHeight - height - 4));

  ctx.fillStyle = "rgba(12, 12, 12, 0.72)";
  ctx.strokeStyle = "#8a8a8a";
  ctx.lineWidth = 1;
  ctx.fillRect(clampedX, clampedY, width, height);
  ctx.strokeRect(clampedX + 0.5, clampedY + 0.5, width - 1, height - 1);
  ctx.fillStyle = "#d6d6d6";
  ctx.fillText(label, clampedX + 5, clampedY + height / 2 + 0.5);
  ctx.restore();
}

export function drawViewerImage(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
) {
  ctx.drawImage(image, transform.x, transform.y, displayWidth * transform.scale, displayHeight * transform.scale);
}

export function drawWipeImage(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  position: number,
  angle: number,
  canvasExtent: number,
) {
  const width = displayWidth * transform.scale;
  const height = displayHeight * transform.scale;
  const pointX = transform.x + width * position;
  const pointY = transform.y + height * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const normalX = Math.cos(angleRad);
  const normalY = Math.sin(angleRad);
  const tangentX = -normalY;
  const tangentY = normalX;
  const far = Math.max(width, height, canvasExtent) * 3;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.lineTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX - tangentX * far + normalX * far, pointY - tangentY * far + normalY * far);
  ctx.lineTo(pointX + tangentX * far + normalX * far, pointY + tangentY * far + normalY * far);
  ctx.closePath();
  ctx.clip();
  drawViewerImage(ctx, image, transform, displayWidth, displayHeight);
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = "#f29b18";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.stroke();
  ctx.restore();
}

export function drawWipeGuide(
  ctx: CanvasRenderingContext2D,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  position: number,
  angle: number,
  canvasExtent: number,
) {
  const width = displayWidth * transform.scale;
  const height = displayHeight * transform.scale;
  const pointX = transform.x + width * position;
  const pointY = transform.y + height * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const tangentX = -Math.sin(angleRad);
  const tangentY = Math.cos(angleRad);
  const far = Math.max(width, height, canvasExtent) * 3;
  ctx.save();
  ctx.strokeStyle = "#f29b18";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.stroke();
  ctx.restore();
}

export function drawDraftGeometry(
  ctx: CanvasRenderingContext2D,
  points: DraftPoint[],
  transform: ViewerTransform,
  pixelAspect: number,
) {
  if (points.length === 0) return;
  ctx.save();
  ctx.strokeStyle = "#77c8b4";
  ctx.fillStyle = "#ffd15c";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = transform.x + point.x * pixelAspect * transform.scale;
    const y = transform.y + point.y * transform.scale;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  if (points.length > 1) ctx.stroke();
  for (const point of points) {
    const x = transform.x + point.x * pixelAspect * transform.scale;
    const y = transform.y + point.y * transform.scale;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}
