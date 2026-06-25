/*
 * Float-viewer pixel sampling and formatting helpers.
 * These utilities decode transport formats, map proxy pixels back to source
 * coordinates, and keep readout formatting separate from the viewer widget.
 */

import type { FloatViewerFrame } from "../api/client";
import { clamp } from "./viewerGeometry";

export type PixelSample = {
  x: number;
  y: number;
  rgba: [number, number, number, number];
  swatch: string;
  hsv: { h: number; s: number; v: number };
  luma: number;
};

export function sampleFloatPixel(frame: FloatViewerFrame, x: number, y: number): PixelSample | null {
  const { width, height, source_width: sourceWidth, source_height: sourceHeight } = frame.header;
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  const rgba = sampleFrameRgba(frame, y * width + x);
  if (!rgba) return null;
  const [r, g, b, a] = rgba.map(finitePixelValue) as [number, number, number, number];
  return {
    x: mapProxyCoordinateToSource(x, width, sourceWidth),
    y: mapProxyCoordinateToSource(y, height, sourceHeight),
    rgba: [r, g, b, a],
    swatch: linearRgbToCss([r, g, b]),
    hsv: rgbToHsv(r, g, b),
    luma: r * 0.2126 + g * 0.7152 + b * 0.0722,
  };
}

export function formatPixelValue(value: number) {
  return Number.isFinite(value) ? value.toFixed(5) : "0.00000";
}

export function formatCompactValue(value: number) {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function sampleFrameRgba(frame: FloatViewerFrame, pixelIndex: number): [number, number, number, number] | null {
  if (frame.header.dtype === "rgb10a2") {
    if (pixelIndex >= frame.pixels.length) return null;
    const packed = frame.pixels[pixelIndex] ?? 0;
    return [
      (packed & 0x3ff) / 1023,
      ((packed >>> 10) & 0x3ff) / 1023,
      ((packed >>> 20) & 0x3ff) / 1023,
      ((packed >>> 30) & 0x03) / 3,
    ];
  }
  const index = pixelIndex * 4;
  if (index + 3 >= frame.pixels.length) return null;
  if (frame.header.dtype === "float16") {
    return [
      halfToFloat(frame.pixels[index]),
      halfToFloat(frame.pixels[index + 1]),
      halfToFloat(frame.pixels[index + 2]),
      halfToFloat(frame.pixels[index + 3]),
    ];
  }
  if (frame.header.dtype === "uint8") {
    return [
      frame.pixels[index] / 255,
      frame.pixels[index + 1] / 255,
      frame.pixels[index + 2] / 255,
      frame.pixels[index + 3] / 255,
    ];
  }
  return [frame.pixels[index], frame.pixels[index + 1], frame.pixels[index + 2], frame.pixels[index + 3]];
}

function halfToFloat(value: number) {
  const sign = (value & 0x8000) ? -1 : 1;
  const exponent = (value >> 10) & 0x1f;
  const fraction = value & 0x03ff;
  if (exponent === 0) {
    return sign * 2 ** -14 * (fraction / 1024);
  }
  if (exponent === 0x1f) {
    return fraction ? 0 : sign * Infinity;
  }
  return sign * 2 ** (exponent - 15) * (1 + fraction / 1024);
}

function mapProxyCoordinateToSource(value: number, proxySize: number, sourceSize: number) {
  const targetSize = Math.max(1, sourceSize || proxySize);
  if (proxySize <= 1) return 0;
  return clamp(Math.round(((value + 0.5) * targetSize) / proxySize - 0.5), 0, targetSize - 1);
}

function finitePixelValue(value: number) {
  return Number.isFinite(value) ? value : 0;
}

function linearRgbToCss(rgb: [number, number, number]) {
  const channels = rgb.map((value) => Math.round(linearToSrgb(clamp(value, 0, 1)) * 255));
  return `rgb(${channels[0]} ${channels[1]} ${channels[2]})`;
}

function linearToSrgb(value: number) {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;
}

function rgbToHsv(r: number, g: number, b: number) {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let h = 0;
  if (delta > 1e-8) {
    if (max === r) h = ((g - b) / delta) % 6;
    else if (max === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return {
    h,
    s: Math.abs(max) > 1e-8 ? delta / Math.abs(max) : 0,
    v: max,
  };
}
