import type { Project, ProjectSettings } from "./api/client";

/**
 * Centralizes runtime-facing project defaults used by viewer transport,
 * preload planning, and playback warming so the app coordinator does not
 * repeat fallback math inline across multiple callbacks.
 */

export function viewerTileHeight(settings: ProjectSettings | null | undefined): number {
  return settings?.tile_height ?? 128;
}

export function viewerTileLanes(settings: ProjectSettings | null | undefined): number {
  return Math.max(1, Math.min(Math.round(settings?.viewer_tile_lanes ?? 1), 8));
}

export function projectFrameStart(settings: ProjectSettings | null | undefined): number {
  return settings?.frame_start ?? 1001;
}

export function projectFrameEnd(settings: ProjectSettings | null | undefined): number {
  return settings?.frame_end ?? 1010;
}

export function interactiveBackendWarmFrameLimit(settings: ProjectSettings | null | undefined): number {
  return settings?.proxy_enabled ? 3 : 1;
}

export function interactiveFrontendWarmFrameLimit(settings: ProjectSettings | null | undefined): number {
  return settings?.proxy_enabled ? 2 : 1;
}

export function playbackWarmFrameCount(settings: ProjectSettings | null | undefined): number {
  return Math.max(2, Math.min((settings?.render_workers ?? 4) * 2, 12));
}

export function playbackFrontendWarmFrameLimit(settings: ProjectSettings | null | undefined): number {
  if (!settings?.proxy_enabled) return 1;
  return Math.max(2, Math.min(settings.viewer_tile_lanes ?? 3, 6));
}

export function projectExecutionBackend(project: Project | null | undefined): ProjectSettings["execution_backend"] {
  return project?.settings.execution_backend ?? "auto";
}
