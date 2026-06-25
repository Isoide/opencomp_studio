import type { FloatViewerFrame, RequestTiming } from "../api/client";

/**
 * Centralizes frontend viewer result bookkeeping that is independent from
 * React state setters. The app can then focus on applying a chosen result
 * instead of rebuilding timing payloads and history windows inline.
 */

export function nextFrontendTimingHistory(history: number[], frontendMs: number, limit = 30): number[] {
  return [...history, frontendMs].slice(-limit);
}

export type ViewerRequestTimingArgs = {
  nodeId: string;
  frame: number;
  viewerInput: string | null;
  compareInput: string | null;
  compareMode: "none" | "difference" | "wipe";
  channel: string;
  transport: string;
  frontendMs: number;
  payloadBytes: number;
  frontendCacheHit: boolean;
  metrics: FloatViewerFrame["metrics"] | null;
  timestampSeconds?: number;
};

export function buildFrontendViewerRequestTiming(args: ViewerRequestTimingArgs): RequestTiming {
  return {
    type: "frontend_viewer_frame",
    node_id: args.nodeId,
    frame: args.frame,
    viewer_input: args.viewerInput,
    compare_input: args.compareInput,
    compare_mode: args.compareMode,
    channel: args.channel,
    transport: args.transport,
    total_ms: Math.round(args.frontendMs * 100) / 100,
    backend_render_ms: 0,
    send_ms: 0,
    bytes: args.payloadBytes,
    frontend_cache_hit: args.frontendCacheHit,
    ws_wait_ms: args.metrics?.ws_wait_ms ?? 0,
    receive_ms: args.metrics?.receive_ms ?? 0,
    tile_copy_ms: args.metrics?.tile_copy_ms ?? 0,
    browser_cache_hit_ms: args.metrics?.browser_cache_hit_ms ?? 0,
    timestamp: args.timestampSeconds ?? Date.now() / 1000,
  };
}

export function appendFrontendRequestTiming(
  currentTimings: RequestTiming[],
  nextTiming: RequestTiming,
  limit = 80,
): RequestTiming[] {
  return [...currentTimings, nextTiming].slice(-limit);
}

export function viewerResultKind(nextGpuFrame: FloatViewerFrame | null, blob: Blob | null): "gpu" | "blob" | "none" {
  if (nextGpuFrame) return "gpu";
  if (blob) return "blob";
  return "none";
}

export function shouldReuseFrontendCache(servedFromFrontendViewerCache: boolean, nextGpuFrame: FloatViewerFrame | null): boolean {
  return servedFromFrontendViewerCache && Boolean(nextGpuFrame);
}
