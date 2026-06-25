import type { ViewerFrameOptions } from "../api/client";

/**
 * Centralizes viewer compare-mode request planning so the main app does not
 * repeat compare-input branching across warm requests, CPU preview fallback,
 * and frontend request timing payloads.
 */

export type ViewerCompareMode = "wipe" | "difference";

export type ViewerProcessOptions = {
  gain: number;
  saturation: number;
  fstop: number;
};

export type ViewerPreviewRequestOptions = Pick<ViewerFrameOptions, "viewerInput" | "compareInput" | "compareMode"> & ViewerProcessOptions;

export type CpuPreviewPlan =
  | {
      kind: "single";
      primary: ViewerPreviewRequestOptions;
      compare: null;
    }
  | {
      kind: "compare";
      primary: ViewerPreviewRequestOptions;
      compare: ViewerPreviewRequestOptions;
    };

export function viewerCompareInputs(compareEnabled: boolean, viewerCompareInputA: string, viewerCompareInputB: string): Array<string | null> {
  return compareEnabled ? [viewerCompareInputA, viewerCompareInputB] : [null];
}

export function cpuPreviewPlan(
  compareEnabled: boolean,
  compareMode: ViewerCompareMode,
  viewerCompareInputA: string,
  viewerCompareInputB: string,
  viewerProcessOptions: ViewerProcessOptions,
): CpuPreviewPlan {
  if (compareEnabled && compareMode === "difference") {
    return {
      kind: "single",
      primary: {
        ...viewerProcessOptions,
        viewerInput: viewerCompareInputA,
        compareInput: viewerCompareInputB,
        compareMode: "difference",
      },
      compare: null,
    };
  }
  if (compareEnabled && compareMode === "wipe") {
    return {
      kind: "compare",
      primary: { ...viewerProcessOptions, viewerInput: viewerCompareInputA },
      compare: { ...viewerProcessOptions, viewerInput: viewerCompareInputB },
    };
  }
  return {
    kind: "single",
    primary: viewerProcessOptions,
    compare: null,
  };
}

export function viewerCompareTiming(
  compareEnabled: boolean,
  compareMode: ViewerCompareMode,
  viewerCompareInputA: string,
  viewerCompareInputB: string,
): {
  viewerInput: string | null;
  compareInput: string | null;
  compareMode: ViewerCompareMode | "none";
} {
  if (!compareEnabled) {
    return { viewerInput: null, compareInput: null, compareMode: "none" };
  }
  if (compareMode === "difference") {
    return {
      viewerInput: viewerCompareInputA,
      compareInput: viewerCompareInputB,
      compareMode: "difference",
    };
  }
  return {
    viewerInput: `${viewerCompareInputA},${viewerCompareInputB}`,
    compareInput: viewerCompareInputB,
    compareMode: "wipe",
  };
}

export function displayPreviewTransport(playbackMode: "hybrid-preview" | "always-float" | "fast-display"): string {
  return `display-preview-${playbackMode}`;
}
