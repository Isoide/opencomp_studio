import { describe, expect, it } from "vitest";

import {
  playbackTransferMode,
  projectCacheLimitMb,
  projectHotkeys,
  projectPreferencesOrNull,
  readPreloadEnabled,
  readPreloadMaxFrames,
  viewerTransferPrecision,
} from "./projectPreferences";
import { makeProject, makeProjectPreferences } from "./test/projectFixtures";

describe("projectPreferences helpers", () => {
  it("returns null for missing preferences and respects defaults", () => {
    expect(projectPreferencesOrNull(null)).toBeNull();
    expect(playbackTransferMode(null)).toBe("hybrid-preview");
    expect(viewerTransferPrecision(null)).toBe("float16");
    expect(readPreloadEnabled(null)).toBe(true);
    expect(readPreloadMaxFrames(null)).toBe(6);
    expect(projectCacheLimitMb(null)).toBe(1024);
  });

  it("reads explicit project preferences", () => {
    const project = makeProject({
      preferences: makeProjectPreferences({
        playback_transfer_mode: "fast-display",
        viewer_transfer_precision: "uint8",
        read_preload_enabled: false,
        read_preload_max_frames: 12,
        cache_memory_limit_mb: 2048,
      }),
    });
    expect(projectPreferencesOrNull(project)).toEqual(project.preferences);
    expect(playbackTransferMode(project)).toBe("fast-display");
    expect(viewerTransferPrecision(project)).toBe("uint8");
    expect(readPreloadEnabled(project)).toBe(false);
    expect(readPreloadMaxFrames(project)).toBe(12);
    expect(projectCacheLimitMb(project)).toBe(2048);
    expect(projectHotkeys(project)?.add_read).toBe("r");
  });

  it("clamps preload frame defaults to at least one frame", () => {
    const project = makeProject({ preferences: makeProjectPreferences({ read_preload_max_frames: 0 }) });
    expect(readPreloadMaxFrames(project)).toBe(1);
  });
});
