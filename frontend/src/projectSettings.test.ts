import { describe, expect, it } from "vitest";

import {
  activeScriptId,
  applyViewerDefaults,
  clampProjectFrame,
  projectPath,
  suggestedNukePath,
  suggestedProjectFilename,
  viewerResolutionLabel,
  viewerSettingsSnapshot,
} from "./projectSettings";
import { makeProject, makeProjectSettings } from "./test/projectFixtures";

describe("projectSettings helpers", () => {
  it("prefers the active script id and falls back to the first tab", () => {
    expect(activeScriptId(makeProject({ active_script_id: "main" }))).toBe("main");
    expect(activeScriptId(makeProject({ active_script_id: "", script_tabs: [{ ...makeProject().script_tabs[0], id: "fallback" }] }))).toBe(
      "fallback",
    );
  });

  it("applies viewer defaults only when unset", () => {
    const settings = makeProjectSettings({ viewer_display: null, viewer_view: "Existing View" });
    const next = applyViewerDefaults(settings, { default_display: "Display A", default_view: "View A" });
    expect(next.viewer_display).toBe("Display A");
    expect(next.viewer_view).toBe("Existing View");
  });

  it("clamps project frames to the configured range", () => {
    const settings = makeProjectSettings({ frame_start: 1001, frame_end: 1010 });
    expect(clampProjectFrame(settings, 900)).toBe(1001);
    expect(clampProjectFrame(settings, 1005)).toBe(1005);
    expect(clampProjectFrame(settings, 2000)).toBe(1010);
  });

  it("builds suggested project and nuke paths from the project name or saved path", () => {
    const project = makeProject({ project_name: "Comp Test v001" });
    expect(suggestedProjectFilename(project)).toBe("Comp_Test_v001.opencomp");
    expect(suggestedNukePath(project)).toBe("Comp_Test_v001.nk");
    expect(suggestedNukePath(makeProject({ settings: makeProjectSettings({ project_path: "shots/test.opencomp" }) }))).toBe("shots/test.nk");
  });

  it("reports viewer resolution and snapshot state", () => {
    const full = makeProjectSettings({ proxy_enabled: false, width: 2048, height: 858 });
    const proxy = makeProjectSettings({ proxy_enabled: true, viewer_max_width: 1280, viewer_max_height: 720 });
    expect(projectPath(makeProject({ settings: makeProjectSettings({ project_path: "shots/test.opencomp" }) }))).toBe("shots/test.opencomp");
    expect(viewerResolutionLabel(full)).toBe("2048x858 full");
    expect(viewerResolutionLabel(proxy)).toBe("1280x720 proxy");
    expect(viewerSettingsSnapshot(proxy)).toEqual({
      width: 1280,
      height: 720,
      display: proxy.viewer_display,
      view: proxy.viewer_view,
      proxyEnabled: true,
    });
  });
});
