import { describe, expect, it } from "vitest";

import { cpuPreviewPlan, displayPreviewTransport, viewerCompareInputs, viewerCompareTiming } from "./viewerCompare";

describe("viewerCompare helpers", () => {
  it("builds compare input lists for warm requests", () => {
    expect(viewerCompareInputs(false, "0", "1")).toEqual([null]);
    expect(viewerCompareInputs(true, "0", "1")).toEqual(["0", "1"]);
  });

  it("builds CPU preview plans for none, difference, and wipe modes", () => {
    const process = { gain: 1, saturation: 1, fstop: 0 };
    expect(cpuPreviewPlan(false, "wipe", "0", "1", process)).toEqual({
      kind: "single",
      primary: process,
      compare: null,
    });
    expect(cpuPreviewPlan(true, "difference", "0", "1", process)).toEqual({
      kind: "single",
      primary: { ...process, viewerInput: "0", compareInput: "1", compareMode: "difference" },
      compare: null,
    });
    expect(cpuPreviewPlan(true, "wipe", "0", "1", process)).toEqual({
      kind: "compare",
      primary: { ...process, viewerInput: "0" },
      compare: { ...process, viewerInput: "1" },
    });
  });

  it("builds frontend request timing compare fields", () => {
    expect(viewerCompareTiming(false, "wipe", "0", "1")).toEqual({
      viewerInput: null,
      compareInput: null,
      compareMode: "none",
    });
    expect(viewerCompareTiming(true, "difference", "0", "1")).toEqual({
      viewerInput: "0",
      compareInput: "1",
      compareMode: "difference",
    });
    expect(viewerCompareTiming(true, "wipe", "0", "1")).toEqual({
      viewerInput: "0,1",
      compareInput: "1",
      compareMode: "wipe",
    });
  });

  it("formats display-preview transport labels from the active playback mode", () => {
    expect(displayPreviewTransport("fast-display")).toBe("display-preview-fast-display");
    expect(displayPreviewTransport("hybrid-preview")).toBe("display-preview-hybrid-preview");
  });
});
