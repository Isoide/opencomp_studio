import { describe, expect, it } from "vitest";

import { appendFrontendRequestTiming, buildFrontendViewerRequestTiming, nextFrontendTimingHistory, shouldReuseFrontendCache, viewerResultKind } from "./viewerResult";

describe("viewerResult helpers", () => {
  it("trims frontend timing history to the configured limit", () => {
    expect(nextFrontendTimingHistory([1, 2, 3], 4, 3)).toEqual([2, 3, 4]);
  });

  it("builds a frontend viewer request timing payload", () => {
    const timing = buildFrontendViewerRequestTiming({
      nodeId: "Viewer1",
      frame: 1001,
      viewerInput: "0",
      compareInput: "1",
      compareMode: "difference",
      channel: "rgba",
      transport: "browser-float-cache",
      frontendMs: 10.126,
      payloadBytes: 1234,
      frontendCacheHit: true,
      metrics: {
        ws_wait_ms: 1,
        receive_ms: 2,
        tile_copy_ms: 3,
        bytes: 4,
        browser_cache_hit_ms: 5,
      },
      timestampSeconds: 42,
    });
    expect(timing).toEqual({
      type: "frontend_viewer_frame",
      node_id: "Viewer1",
      frame: 1001,
      viewer_input: "0",
      compare_input: "1",
      compare_mode: "difference",
      channel: "rgba",
      transport: "browser-float-cache",
      total_ms: 10.13,
      backend_render_ms: 0,
      send_ms: 0,
      bytes: 1234,
      frontend_cache_hit: true,
      ws_wait_ms: 1,
      receive_ms: 2,
      tile_copy_ms: 3,
      browser_cache_hit_ms: 5,
      timestamp: 42,
    });
  });

  it("trims accumulated frontend request timings", () => {
    const a = buildFrontendViewerRequestTiming({
      nodeId: "Viewer1",
      frame: 1001,
      viewerInput: null,
      compareInput: null,
      compareMode: "none",
      channel: "rgba",
      transport: "browser",
      frontendMs: 1,
      payloadBytes: 1,
      frontendCacheHit: false,
      metrics: null,
      timestampSeconds: 1,
    });
    const b = { ...a, frame: 1002, timestamp: 2 };
    expect(appendFrontendRequestTiming([a], b, 1)).toEqual([b]);
  });

  it("classifies the viewer result kind and cache reuse path", () => {
    expect(viewerResultKind({} as never, null)).toBe("gpu");
    expect(viewerResultKind(null, new Blob())).toBe("blob");
    expect(viewerResultKind(null, null)).toBe("none");
    expect(shouldReuseFrontendCache(true, {} as never)).toBe(true);
    expect(shouldReuseFrontendCache(true, null)).toBe(false);
  });
});
