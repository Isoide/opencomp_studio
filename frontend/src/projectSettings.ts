import type { ColorConfig, Project, ProjectSettings } from "./api/client";

export type ViewerProxySize = {
  width: number | null;
  height: number | null;
};

export type ViewerSettingsSnapshot = ViewerProxySize & {
  display: string | null;
  view: string | null;
  proxyEnabled: boolean;
};

export function projectSettingsOrNull(project: Project | null | undefined): ProjectSettings | null {
  return project?.settings ?? null;
}

export function activeScriptId(project: Project | null | undefined): string | null {
  if (!project) return null;
  return project.active_script_id || project.script_tabs[0]?.id || null;
}

export function activeScript(project: Project | null | undefined) {
  const scriptId = activeScriptId(project);
  if (!project || !scriptId) return null;
  return project.script_tabs.find((tab) => tab.id === scriptId) ?? null;
}

export function activeScriptName(project: Project | null | undefined): string | null {
  return activeScript(project)?.name ?? null;
}

export function projectPath(project: Project | null | undefined): string | null {
  return project?.settings.project_path ?? null;
}

export function suggestedProjectFilename(project: Project | null | undefined): string {
  const baseName = project?.project_name || "opencomp_project";
  return ensureProjectExtension(baseName.replace(/[^A-Za-z0-9_.-]+/g, "_"));
}

export function suggestedProjectPath(project: Project | null | undefined): string {
  return projectPath(project) ?? suggestedProjectFilename(project);
}

export function suggestedNukePath(project: Project | null | undefined): string {
  const savedPath = projectPath(project);
  if (savedPath) {
    return savedPath.replace(/\.opencomp$/i, ".nk");
  }
  const baseName = project?.project_name || "opencomp_project";
  return ensureNukeExtension(baseName.replace(/[^A-Za-z0-9_.-]+/g, "_"));
}

export function applyViewerDefaults(settings: ProjectSettings, config: Pick<ColorConfig, "default_display" | "default_view">): ProjectSettings {
  return {
    ...settings,
    viewer_display: settings.viewer_display ?? config.default_display,
    viewer_view: settings.viewer_view ?? config.default_view,
  };
}

export function projectWithViewerDefaults(project: Project, config: Pick<ColorConfig, "default_display" | "default_view">): Project {
  return {
    ...project,
    settings: applyViewerDefaults(project.settings, config),
  };
}

export function clampProjectFrame(settings: ProjectSettings | null | undefined, frame: number): number {
  if (!settings) return frame;
  return Math.min(Math.max(frame, settings.frame_start), settings.frame_end);
}

export function viewerProxySize(settings: ProjectSettings | null | undefined): ViewerProxySize {
  if (!settings?.proxy_enabled) {
    return { width: null, height: null };
  }
  return {
    width: settings.viewer_max_width,
    height: settings.viewer_max_height,
  };
}

export function viewerDisplaySelection(settings: ProjectSettings | null | undefined) {
  return {
    display: settings?.viewer_display ?? null,
    view: settings?.viewer_view ?? null,
  };
}

export function viewerSettingsSnapshot(settings: ProjectSettings | null | undefined): ViewerSettingsSnapshot {
  const proxy = viewerProxySize(settings);
  const selection = viewerDisplaySelection(settings);
  return {
    width: proxy.width,
    height: proxy.height,
    display: selection.display,
    view: selection.view,
    proxyEnabled: Boolean(settings?.proxy_enabled),
  };
}

export function viewerResolutionLabel(settings: ProjectSettings | null | undefined): string {
  if (!settings) return "viewer unavailable";
  if (!settings.proxy_enabled) {
    return `${settings.width}x${settings.height} full`;
  }
  return `${settings.viewer_max_width}x${settings.viewer_max_height} proxy`;
}

export function proxyCacheToken(project: Project | null | undefined): string {
  const settings = project?.settings;
  if (!settings?.proxy_enabled) return "full";
  return `${settings.viewer_max_width}x${settings.viewer_max_height}`;
}

function ensureProjectExtension(filename: string): string {
  const trimmed = filename.trim() || "opencomp_project.opencomp";
  return trimmed.toLowerCase().endsWith(".opencomp") ? trimmed : `${trimmed}.opencomp`;
}

function ensureNukeExtension(filename: string): string {
  const trimmed = filename.trim() || "opencomp_project.nk";
  return trimmed.toLowerCase().endsWith(".nk") ? trimmed : `${trimmed}.nk`;
}
