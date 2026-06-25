import { describe, expect, it } from "vitest";

import {
  interactiveBackendWarmFrameLimit,
  interactiveFrontendWarmFrameLimit,
  playbackFrontendWarmFrameLimit,
  playbackWarmFrameCount,
  projectExecutionBackend,
  projectFrameEnd,
  projectFrameStart,
  viewerTileHeight,
  viewerTileLanes,
} from "./projectRuntime";
import { makeProject, makeProjectSettings } from "./test/projectFixtures";

describe("projectRuntime helpers", () => {
  it("applies stable viewer transport defaults", () => {
    expect(viewerTileHeight(null)).toBe(128);
    expect(viewerTileLanes(null)).toBe(1);
    expect(viewerTileLanes(makeProjectSettings({ viewer_tile_lanes: 99 }))).toBe(8);
  });

  it("exposes frame bounds and warm limits from settings", () => {
    const full = makeProjectSettings({ frame_start: 1005, frame_end: 1020, proxy_enabled: false, render_workers: 10 });
    const proxy = makeProjectSettings({ proxy_enabled: true, viewer_tile_lanes: 5 });
    expect(projectFrameStart(full)).toBe(1005);
    expect(projectFrameEnd(full)).toBe(1020);
    expect(interactiveBackendWarmFrameLimit(full)).toBe(1);
    expect(interactiveBackendWarmFrameLimit(proxy)).toBe(3);
    expect(interactiveFrontendWarmFrameLimit(full)).toBe(1);
    expect(interactiveFrontendWarmFrameLimit(proxy)).toBe(2);
    expect(playbackWarmFrameCount(full)).toBe(12);
    expect(playbackFrontendWarmFrameLimit(full)).toBe(1);
    expect(playbackFrontendWarmFrameLimit(proxy)).toBe(5);
  });

  it("falls back to auto for execution backend", () => {
    expect(projectExecutionBackend(null)).toBe("auto");
    expect(projectExecutionBackend(makeProject({ settings: makeProjectSettings({ execution_backend: "vulkan" }) }))).toBe("vulkan");
  });
});
