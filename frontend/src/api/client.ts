export const API_BASE = import.meta.env.VITE_OPENCOMP_API ?? "http://127.0.0.1:8000";

export type NodeModel = {
  id: string;
  type: string;
  name?: string | null;
  position: [number, number];
  params: Record<string, unknown>;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
};

export type EdgeModel = {
  id: string;
  source_node: string;
  source_socket: string;
  target_node: string;
  target_socket: string;
};

export type ProjectGraph = {
  nodes: Record<string, NodeModel>;
  edges: EdgeModel[];
};

export type Project = {
  schema_version: string;
  project_name: string;
  settings: ProjectSettings;
  graph: ProjectGraph;
  script_tabs: ScriptTab[];
  active_script_id: string;
  preferences: ProjectPreferences;
  plugin_menu: Array<Record<string, unknown>>;
  startup_scripts: string[];
};

export type ProjectSettings = {
  fps: number;
  frame_start: number;
  frame_end: number;
  width: number;
  height: number;
  working_colorspace: string;
  ocio_config: string | null;
  viewer_display: string | null;
  viewer_view: string | null;
  proxy_enabled: boolean;
  viewer_max_width: number;
  viewer_max_height: number;
  project_path: string | null;
  default_output_path: string;
  cache_enabled: boolean;
  auto_refresh: boolean;
  tile_rendering_enabled: boolean;
  tile_height: number;
  tile_workers: number;
};

export type ScriptTab = {
  id: string;
  name: string;
  graph: ProjectGraph;
  path: string | null;
  startup_scripts: string[];
  kind: string;
};

export type ProjectPreferences = {
  autosave_seconds: number;
  idle_autosave_seconds: number;
  cache_memory_limit_mb: number;
  viewer_zoom_speed: number;
  wheel_zoom_enabled: boolean;
  auto_connect_new_nodes: boolean;
  default_read_colorspace: string;
  custom_init_scripts: string[];
  path_substitutions: Array<{ source: string; target: string }>;
  hotkeys: {
    add_read: string;
    add_write: string;
    add_merge: string;
    add_shuffle: string;
    add_group: string;
    refresh_viewer: string;
    fit_viewer: string;
  };
};

export type ColorConfig = {
  available: boolean;
  current_config: string | null;
  builtin_configs: Array<{
    name: string;
    description: string;
    is_default: boolean;
    is_recommended: boolean;
  }>;
  colorspaces: string[];
  displays: string[];
  views: string[];
  default_display: string | null;
  default_view: string | null;
  viewer_display: string | null;
  viewer_view: string | null;
};

export type OcioGpuTexture = {
  texture_name: string;
  sampler_name: string;
  binding: number;
  width: number;
  height: number;
  channels: string;
  dimensions: string;
  interpolation: string;
  values: number[];
};

export type OcioGpuShader = {
  available: boolean;
  reason: string | null;
  source: string;
  display: string | null;
  view: string | null;
  language: string;
  shader_text: string | null;
  function_name: string | null;
  resource_prefix?: string;
  requires_lut_textures?: boolean;
  textures: OcioGpuTexture[];
};

export type NodeCatalogItem = {
  type: string;
  label: string;
  category: string;
  inputs: string[];
  outputs: string[];
};

export type NodeMetadata = {
  node_id: string;
  frame: number;
  width: number;
  height: number;
  pixel_aspect: number;
  display_width: number;
  display_height: number;
  colorspace: string;
  channels: string[];
  format_bbox: BBox;
  data_window: BBox;
  cryptomatte_layers: CryptomatteLayer[];
  metadata: Record<string, unknown>;
};

export type BBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type CryptomatteLayer = {
  key: string;
  name: string;
  hash: string;
  conversion: string;
  manifest_count: number;
  manifest_entries: Array<{ name: string; id: string }>;
  channels: string[];
};

export type CryptomattePick = {
  node_id: string;
  frame: number;
  layer: string;
  id: string;
  id_float: number;
  name: string | null;
  coverage: number;
  x: number;
  y: number;
};

export type PythonScriptResult = {
  success: boolean;
  stdout: string;
  stderr: string;
  error: string | null;
  traceback: string | null;
  changed: boolean;
  project: Project;
};

export type NodeTiming = {
  type: string;
  duration_ms: number;
  cache_hit: boolean;
  timestamp: number;
};

export type PreviewTiming = {
  cache_hit: boolean;
  total_ms: number;
  evaluate_ms: number;
  resize_ms: number;
  viewer_process_ms?: number;
  ocio_ms: number;
  encode_ms: number;
  source_width?: number;
  source_height?: number;
  preview_width?: number;
  preview_height?: number;
  bytes: number;
  timestamp: number;
  channel?: string;
  float_cache_hit?: boolean;
};

export type PhaseTiming = {
  node_id: string;
  phase: string;
  duration_ms: number;
  details: Record<string, unknown>;
  timestamp: number;
};

export type RequestTiming = {
  type: string;
  node_id: string;
  frame: number;
  viewer_input: string | null;
  compare_input: string | null;
  compare_mode: string;
  channel: string;
  transport: string;
  total_ms: number;
  backend_render_ms: number;
  send_ms: number;
  bytes: number;
  float_cache_hit?: boolean;
  frontend_cache_hit?: boolean;
  timestamp: number;
};

export type FloatViewerFrameHeader = {
  type: string;
  node_id: string;
  frame: number;
  viewer_input: string | null;
  channel: string;
  width: number;
  height: number;
  source_width: number;
  source_height: number;
  pixel_aspect: number;
  colorspace: string;
  apply_ocio: boolean;
  format_bbox: BBox;
  data_window: BBox;
  dtype: "float32" | "float16";
  layout: "rgba";
  byte_length: number;
  cache_hit: boolean;
  evaluate_ms: number;
  resize_ms: number;
  tile_stream?: boolean;
  tile_height?: number | null;
  tile_count?: number;
  partial?: boolean;
  tiles_received?: number;
  tile_revision?: number;
};

export type FloatViewerPixels = Float32Array | Uint16Array;

export type FloatViewerFrame = {
  header: FloatViewerFrameHeader;
  pixels: FloatViewerPixels;
};

type FloatViewerTileHeader = {
  type: "viewer_float_tile";
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
  byte_length: number;
};

export type CacheStatus = {
  enabled: boolean;
  entries: number;
  preview_entries: number;
  float_preview_entries: number;
  hits: number;
  misses: number;
  preview_hits: number;
  preview_misses: number;
  float_preview_hits: number;
  float_preview_misses: number;
  memory_bytes: number;
  preview_memory_bytes: number;
  float_preview_memory_bytes: number;
  max_memory_bytes: number;
  max_preview_memory_bytes: number;
  max_float_preview_memory_bytes: number;
  graph_revision: number;
  cached_frames: number[];
  cached_final_preview_frames: number[];
  cached_float_preview_frames: number[];
  cached_node_frames: number[];
  cached_all_frames: number[];
  active_nodes: string[];
  node_timings: Record<string, NodeTiming>;
  preview_timings: Record<string, PreviewTiming>;
  phase_timings: PhaseTiming[];
  request_timings: RequestTiming[];
  last_request_timing: RequestTiming | null;
};

export type ViewerFrameOptions = {
  viewerInput?: string | null;
  compareInput?: string | null;
  compareMode?: "none" | "difference";
  gain?: number;
  saturation?: number;
  fstop?: number;
  precision?: "float32" | "float16";
  streamTiles?: boolean;
  tileHeight?: number;
};

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

function viewerFramePayload(
  nodeId: string,
  frame: number,
  display: string | null,
  view: string | null,
  channel: string | null,
  options: ViewerFrameOptions = {},
) {
  return {
    node_id: nodeId,
    frame,
    display,
    view,
    channel,
    viewer_input: options.viewerInput ?? null,
    compare_input: options.compareInput ?? null,
    compare_mode: options.compareMode ?? "none",
    gain: options.gain ?? 1,
    saturation: options.saturation ?? 1,
    fstop: options.fstop ?? 0,
    precision: options.precision ?? "float32",
    stream_tiles: options.streamTiles ?? false,
    tile_height: options.tileHeight ?? null,
  };
}

function websocketUrl(path: string): string {
  const url = new URL(API_BASE, window.location.origin);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  url.search = "";
  return url.toString();
}

function allocateFloatPixels(header: FloatViewerFrameHeader): FloatViewerPixels {
  const length = header.width * header.height * 4;
  return header.dtype === "float16" ? new Uint16Array(length) : new Float32Array(length);
}

function pixelsFromBuffer(dtype: FloatViewerFrameHeader["dtype"], buffer: ArrayBuffer): FloatViewerPixels {
  return dtype === "float16" ? new Uint16Array(buffer) : new Float32Array(buffer);
}

function websocketBinary(path: string, payload: unknown, signal: AbortSignal | undefined, mimeType: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(websocketUrl(path));
    let settled = false;

    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
    };
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback();
    };
    const onAbort = () => {
      finish(() => {
        socket.close();
        reject(new DOMException("The request was aborted.", "AbortError"));
      });
    };

    if (signal?.aborted) {
      onAbort();
      return;
    }

    signal?.addEventListener("abort", onAbort, { once: true });
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      socket.send(JSON.stringify(payload));
    };
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        finish(() => {
          reject(new Error(event.data));
        });
        return;
      }
      finish(() => {
        if (event.data instanceof Blob) {
          resolve(event.data);
        } else {
          resolve(new Blob([event.data], { type: mimeType }));
        }
      });
    };
    socket.onerror = () => {
      finish(() => reject(new Error("Viewer WebSocket failed.")));
    };
    socket.onclose = () => {
      finish(() => reject(new Error("Viewer WebSocket closed before returning data.")));
    };
  });
}

function websocketFloatFrame(
  path: string,
  payload: unknown,
  signal: AbortSignal | undefined,
  onProgress?: (frame: FloatViewerFrame) => void,
): Promise<FloatViewerFrame> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(websocketUrl(path));
    let settled = false;
    let header: FloatViewerFrameHeader | null = null;
    let tileHeader: FloatViewerTileHeader | null = null;
    let tiledPixels: FloatViewerPixels | null = null;
    let tilesReceived = 0;

    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
    };
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback();
    };
    const onAbort = () => {
      finish(() => {
        socket.close();
        reject(new DOMException("The request was aborted.", "AbortError"));
      });
    };

    if (signal?.aborted) {
      onAbort();
      return;
    }

    signal?.addEventListener("abort", onAbort, { once: true });
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      socket.send(JSON.stringify(payload));
    };
    socket.onmessage = (event) => {
      if (typeof event.data === "string") {
        const parsed = JSON.parse(event.data);
        if (parsed.type === "error") {
          finish(() => {
            reject(new Error(parsed.detail ?? event.data));
          });
          return;
        }
        if (parsed.type === "viewer_float_tile") {
          tileHeader = parsed as FloatViewerTileHeader;
          return;
        }
        if (parsed.type === "viewer_float_tiles_done") {
          if (!header || !tiledPixels) {
            finish(() => {
              socket.close();
              reject(new Error("Float tile stream finished before frame allocation."));
            });
            return;
          }
          const expectedTiles = header.tile_count ?? tilesReceived;
          if (tilesReceived !== expectedTiles) {
            finish(() => {
              socket.close();
              reject(new Error(`Float tile stream ended after ${tilesReceived}/${expectedTiles} tiles.`));
            });
            return;
          }
          const resolvedHeader = {
            ...header,
            partial: false,
            tiles_received: tilesReceived,
            tile_revision: tilesReceived,
          };
          const resolvedPixels = tiledPixels;
          finish(() => {
            resolve({ header: resolvedHeader, pixels: resolvedPixels });
          });
          return;
        }
        header = parsed as FloatViewerFrameHeader;
        if (header.tile_stream) {
          tiledPixels = allocateFloatPixels(header);
          tilesReceived = 0;
        }
        return;
      }
      if (!header) {
        finish(() => {
          socket.close();
          reject(new Error("Float viewer stream returned pixels before metadata."));
        });
        return;
      }
      const buffer = event.data instanceof Blob ? null : (event.data as ArrayBuffer);
      if (!buffer) {
        finish(() => {
          socket.close();
          reject(new Error("Float viewer stream returned an unsupported binary payload."));
        });
        return;
      }
      if (header.tile_stream) {
        if (!tileHeader || !tiledPixels) {
          finish(() => {
            socket.close();
            reject(new Error("Float tile stream returned tile pixels before tile metadata."));
          });
          return;
        }
        const tilePixels = pixelsFromBuffer(header.dtype, buffer);
        const rowStride = header.width * 4;
        const expectedValues = tileHeader.width * tileHeader.height * 4;
        if (tilePixels.length !== expectedValues) {
          finish(() => {
            socket.close();
            reject(new Error(`Float tile size mismatch: got ${tilePixels.length}, expected ${expectedValues}.`));
          });
          return;
        }
        for (let row = 0; row < tileHeader.height; row += 1) {
          const sourceStart = row * tileHeader.width * 4;
          const sourceEnd = sourceStart + tileHeader.width * 4;
          const targetStart = (tileHeader.y + row) * rowStride + tileHeader.x * 4;
          tiledPixels.set(tilePixels.subarray(sourceStart, sourceEnd), targetStart);
        }
        tilesReceived += 1;
        onProgress?.({
          header: {
            ...header,
            partial: true,
            tiles_received: tilesReceived,
            tile_revision: tilesReceived,
          },
          pixels: tiledPixels,
        });
        tileHeader = null;
        return;
      }
      const resolvedHeader = header;
      finish(() => {
        resolve({ header: resolvedHeader, pixels: pixelsFromBuffer(resolvedHeader.dtype, buffer) });
      });
    };
    socket.onerror = () => {
      finish(() => reject(new Error("Viewer float WebSocket failed.")));
    };
    socket.onclose = () => {
      finish(() => reject(new Error("Viewer float WebSocket closed before returning data.")));
    };
  });
}

let viewerFrameWebSocketSupported: boolean | null = null;
let viewerFloatWebSocketSupported: boolean | null = null;

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export const client = {
  health: () => jsonRequest<{ status: string; app: string }>("/api/health"),
  newProject: () => jsonRequest<Project>("/api/projects/new", { method: "POST", body: "{}" }),
  saveProject: (path: string | null, project: Project) =>
    jsonRequest<Project>("/api/projects/save", {
      method: "POST",
      body: JSON.stringify({ path, project }),
    }),
  loadProject: (path: string) =>
    jsonRequest<Project>("/api/projects/load", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  importProject: (project: Project) =>
    jsonRequest<Project>("/api/projects/import", {
      method: "POST",
      body: JSON.stringify({ project }),
    }),
  exportNuke: (path: string | null, project: Project) =>
    jsonRequest<{ status: string; path: string; message: string }>("/api/projects/export-nuke", {
      method: "POST",
      body: JSON.stringify({ path, project }),
    }),
  exportNukeContent: async (path: string | null, project: Project): Promise<Blob> => {
    const response = await fetch(`${API_BASE}/api/projects/export-nuke/content`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, project }),
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.blob();
  },
  getGraph: () => jsonRequest<ProjectGraph>("/api/graph"),
  putGraph: (graph: ProjectGraph) =>
    jsonRequest<ProjectGraph>("/api/graph", { method: "PUT", body: JSON.stringify({ graph }) }),
  createScript: (name: string, kind = "comp") =>
    jsonRequest<Project>("/api/scripts", { method: "POST", body: JSON.stringify({ name, kind }) }),
  setActiveScript: (scriptId: string) =>
    jsonRequest<Project>("/api/scripts/active", { method: "PUT", body: JSON.stringify({ script_id: scriptId }) }),
  renameScript: (scriptId: string, name: string) =>
    jsonRequest<Project>(`/api/scripts/${encodeURIComponent(scriptId)}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  runPython: (code: string) =>
    jsonRequest<PythonScriptResult>("/api/python/run", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  putProjectSettings: (settings: ProjectSettings) =>
    jsonRequest<ProjectSettings>("/api/projects/settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    }),
  putPreferences: (preferences: ProjectPreferences) =>
    jsonRequest<ProjectPreferences>("/api/projects/preferences", {
      method: "PUT",
      body: JSON.stringify({ preferences }),
    }),
  nodeCatalog: () => jsonRequest<NodeCatalogItem[]>("/api/nodes/catalog"),
  nodeMetadata: (nodeId: string, frame: number) =>
    jsonRequest<NodeMetadata>(`/api/nodes/${encodeURIComponent(nodeId)}/metadata?frame=${frame}`),
  nodeCryptomatte: (nodeId: string, frame: number) =>
    jsonRequest<{ node_id: string; frame: number; layers: CryptomatteLayer[] }>(
      `/api/nodes/${encodeURIComponent(nodeId)}/cryptomatte?frame=${frame}`,
    ),
  colorConfig: () => jsonRequest<ColorConfig>("/api/color/config"),
  colorGpuShader: (src: string, display: string | null, view: string | null) => {
    const params = new URLSearchParams({ src });
    if (display) params.set("display", display);
    if (view) params.set("view", view);
    return jsonRequest<OcioGpuShader>(`/api/color/gpu-shader?${params.toString()}`);
  },
  cacheStatus: () => jsonRequest<CacheStatus>("/api/cache/status"),
  clearCache: () => jsonRequest<{ status: string }>("/api/cache/clear", { method: "POST", body: "{}" }),
  renderFrame: (nodeId: string, frame: number) =>
    jsonRequest<{ status: string; node_id: string; frame: number }>("/api/render", {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, frame }),
    }),
  cryptomattePick: (nodeId: string, frame: number, layer: string | null, x: number, y: number) =>
    jsonRequest<CryptomattePick>("/api/cryptomatte/pick", {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, frame, layer, x, y }),
    }),
  cryptomatteMatte: async (
    nodeId: string,
    frame: number,
    layer: string | null,
    matteIds: string[],
    maxWidth: number | null,
    maxHeight: number | null,
    signal?: AbortSignal,
  ): Promise<Blob> => {
    const response = await fetch(`${API_BASE}/api/cryptomatte/matte`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node_id: nodeId,
        frame,
        layer,
        matte_ids: matteIds,
        max_width: maxWidth,
        max_height: maxHeight,
      }),
      signal,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `${response.status} ${response.statusText}`);
    }
    return response.blob();
  },
  viewerFrame: async (
    nodeId: string,
    frame: number,
    display: string | null,
    view: string | null,
    channel: string | null,
    signal?: AbortSignal,
    options: ViewerFrameOptions = {},
  ): Promise<Blob> => {
    const response = await fetch(`${API_BASE}/api/viewer/frame`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(viewerFramePayload(nodeId, frame, display, view, channel, options)),
      signal,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `${response.status} ${response.statusText}`);
    }
    return response.blob();
  },
  viewerFrameStream: async (
    nodeId: string,
    frame: number,
    display: string | null,
    view: string | null,
    channel: string | null,
    signal?: AbortSignal,
    options: ViewerFrameOptions = {},
  ): Promise<Blob> => {
    if (viewerFrameWebSocketSupported === false) {
      throw new Error("Viewer WebSocket is unavailable in this backend session.");
    }
    try {
      const blob = await websocketBinary(
        "/ws/viewer/frame",
        viewerFramePayload(nodeId, frame, display, view, channel, options),
        signal,
        "image/png",
      );
      viewerFrameWebSocketSupported = true;
      return blob;
    } catch (error) {
      if (!isAbortError(error)) {
        viewerFrameWebSocketSupported = false;
      }
      throw error;
    }
  },
  viewerFloatFrameStream: async (
    nodeId: string,
    frame: number,
    display: string | null,
    view: string | null,
    channel: string | null,
    signal?: AbortSignal,
    options: ViewerFrameOptions = {},
    onProgress?: (frame: FloatViewerFrame) => void,
  ): Promise<FloatViewerFrame> => {
    if (viewerFloatWebSocketSupported === false) {
      throw new Error("Viewer float WebSocket is unavailable in this backend session.");
    }
    try {
      const data = await websocketFloatFrame(
        "/ws/viewer/float",
        viewerFramePayload(nodeId, frame, display, view, channel, {
          ...options,
          gain: 1,
          saturation: 1,
          fstop: 0,
          compareInput: null,
          compareMode: "none",
          precision: options.precision ?? "float16",
          streamTiles: options.streamTiles ?? true,
          tileHeight: options.tileHeight ?? 128,
        }),
        signal,
        onProgress,
      );
      viewerFloatWebSocketSupported = true;
      return data;
    } catch (error) {
      // Backend restarts and transient socket closes should not permanently
      // disable the GPU viewer path for the whole app session.
      viewerFloatWebSocketSupported = true;
      throw error;
    }
  },
};
