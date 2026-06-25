import type { Project, ProjectGraph, ProjectPreferences, ProjectSettings } from "../api/client";

/**
 * Shared project fixtures for frontend unit tests.
 * These keep helper-module tests concise and avoid repeating large
 * Project/Settings/Preferences literals in each spec.
 */

export function makeProjectGraph(): ProjectGraph {
  return {
    nodes: {},
    edges: [],
  };
}

export function makeProjectSettings(overrides: Partial<ProjectSettings> = {}): ProjectSettings {
  return {
    fps: 24,
    frame_start: 1001,
    frame_end: 1010,
    width: 1920,
    height: 1080,
    working_colorspace: "ACES2065-1",
    ocio_config: null,
    viewer_display: "sRGB - Display",
    viewer_view: "ACES 2.0 - SDR 100 nits (Rec.709)",
    proxy_enabled: false,
    viewer_max_width: 1280,
    viewer_max_height: 720,
    project_path: null,
    default_output_path: "renders/output.exr",
    cache_enabled: true,
    auto_refresh: true,
    tile_rendering_enabled: true,
    tile_height: 128,
    tile_workers: 2,
    render_workers: 4,
    read_workers: 2,
    viewer_tile_lanes: 3,
    execution_backend: "auto",
    image_io_backend: "auto",
    gpu_memory_limit_mb: 2048,
    gpu_warm_neighbor_frames: 2,
    ...overrides,
  };
}

export function makeProjectPreferences(overrides: Partial<ProjectPreferences> = {}): ProjectPreferences {
  return {
    autosave_seconds: 30,
    idle_autosave_seconds: 10,
    cache_memory_limit_mb: 1024,
    viewer_zoom_speed: 1,
    wheel_zoom_enabled: true,
    auto_connect_new_nodes: true,
    playback_transfer_mode: "hybrid-preview",
    viewer_transfer_precision: "float16",
    read_preload_enabled: true,
    read_preload_max_frames: 6,
    default_read_colorspace: "ACES2065-1",
    custom_init_scripts: [],
    path_substitutions: [],
    hotkeys: {
      add_read: "r",
      add_write: "w",
      add_merge: "m",
      add_shuffle: "s",
      add_group: "g",
      toggle_disable: "d",
      refresh_viewer: "space",
      fit_viewer: "f",
    },
    ...overrides,
  };
}

export function makeProject(overrides: Partial<Project> = {}): Project {
  const graph = overrides.graph ?? makeProjectGraph();
  return {
    schema_version: "0.1.0",
    project_name: "Comp Test",
    settings: makeProjectSettings(),
    graph,
    script_tabs: [
      {
        id: "main",
        name: "Comp 1",
        graph,
        code: "print('hello')",
        path: null,
        startup_scripts: [],
        kind: "comp",
      },
    ],
    active_script_id: "main",
    preferences: makeProjectPreferences(),
    plugin_menu: [],
    startup_scripts: [],
    ...overrides,
  };
}
