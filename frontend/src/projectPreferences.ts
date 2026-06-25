import type { Project, ProjectPreferences } from "./api/client";

export function projectPreferencesOrNull(project: Project | null | undefined): ProjectPreferences | null {
  return project?.preferences ?? null;
}

export function playbackTransferMode(project: Project | null | undefined): ProjectPreferences["playback_transfer_mode"] {
  return project?.preferences.playback_transfer_mode ?? "hybrid-preview";
}

export function viewerTransferPrecision(project: Project | null | undefined): ProjectPreferences["viewer_transfer_precision"] {
  return project?.preferences.viewer_transfer_precision ?? "float16";
}

export function readPreloadEnabled(project: Project | null | undefined): boolean {
  return project?.preferences.read_preload_enabled ?? true;
}

export function readPreloadMaxFrames(project: Project | null | undefined): number {
  return Math.max(1, Math.round(project?.preferences.read_preload_max_frames ?? 6));
}

export function projectHotkeys(project: Project | null | undefined): ProjectPreferences["hotkeys"] | null {
  return project?.preferences.hotkeys ?? null;
}

export function projectCacheLimitMb(project: Project | null | undefined): number {
  return project?.preferences.cache_memory_limit_mb ?? 1024;
}
