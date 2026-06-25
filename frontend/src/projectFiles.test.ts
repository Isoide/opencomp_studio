import { describe, expect, it } from "vitest";

import { ensureNukeExtension, ensureOpenCompExtension, isBackendFilesystemPath, projectWithCurrentGraph } from "./projectFiles";
import { makeProject, makeProjectGraph, makeProjectSettings } from "./test/projectFixtures";

describe("projectFiles helpers", () => {
  it("detects backend filesystem paths across platforms", () => {
    expect(isBackendFilesystemPath("C:\\shots\\plate.####.exr")).toBe(true);
    expect(isBackendFilesystemPath("\\\\server\\share\\plate.####.exr")).toBe(true);
    expect(isBackendFilesystemPath("/mnt/shots/plate.####.exr")).toBe(true);
    expect(isBackendFilesystemPath("browser_export.opencomp")).toBe(false);
  });

  it("normalizes OpenComp and Nuke filename extensions", () => {
    expect(ensureOpenCompExtension("test")).toBe("test.opencomp");
    expect(ensureOpenCompExtension("test.opencomp")).toBe("test.opencomp");
    expect(ensureNukeExtension("test")).toBe("test.nk");
    expect(ensureNukeExtension("test.nk")).toBe("test.nk");
  });

  it("attaches the current graph to the active script and can clear the project path", () => {
    const original = makeProject({ settings: makeProjectSettings({ project_path: "shots/test.opencomp" }) });
    const graph = makeProjectGraph();
    graph.nodes.Viewer1 = {
      id: "Viewer1",
      type: "Viewer",
      position: [0, 0],
      params: {},
      param_expressions: {},
      inputs: {},
      outputs: {},
    };

    const next = projectWithCurrentGraph(original, graph, "print('fallback')", true);

    expect(next.graph).toBe(graph);
    expect(next.script_tabs[0]?.graph).toBe(graph);
    expect(next.settings.project_path).toBeNull();
  });
});
