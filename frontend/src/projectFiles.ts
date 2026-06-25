import type { Project, ProjectGraph, ScriptTab } from "./api/client";

export function isBackendFilesystemPath(path: string): boolean {
  const value = path.trim();
  return /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("\\\\") || value.startsWith("/") || value.includes("/") || value.includes("\\");
}

export function ensureOpenCompExtension(filename: string): string {
  const trimmed = filename.trim() || "opencomp_project.opencomp";
  return trimmed.toLowerCase().endsWith(".opencomp") ? trimmed : `${trimmed}.opencomp`;
}

export function ensureNukeExtension(filename: string): string {
  const trimmed = filename.trim() || "opencomp_project.nk";
  return trimmed.toLowerCase().endsWith(".nk") ? trimmed : `${trimmed}.nk`;
}

export function projectWithCurrentGraph(
  project: Project,
  graph: ProjectGraph,
  fallbackScriptCode: string,
  clearProjectPath = false,
): Project {
  const currentScriptId = project.active_script_id || project.script_tabs[0]?.id || "main";
  const scriptTabs: ScriptTab[] =
    project.script_tabs.length > 0
      ? project.script_tabs.map((tab) => (tab.id === currentScriptId ? { ...tab, graph } : tab))
      : [{ id: currentScriptId, name: "Comp 1", graph, code: fallbackScriptCode, path: null, startup_scripts: [], kind: "comp" }];
  return {
    ...project,
    active_script_id: currentScriptId,
    graph,
    script_tabs: scriptTabs,
    settings: {
      ...project.settings,
      project_path: clearProjectPath ? null : project.settings.project_path,
    },
  };
}
