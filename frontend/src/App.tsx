import { Cable, Download, FolderOpen, Play, Plus, Save, Search, Upload, X } from "lucide-react";
import { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  client,
  type CacheStatus,
  type CryptomattePick,
  type FloatViewerFrame,
  type NodeCatalogItem,
  type NodeMetadata,
  type NodeTiming,
  type OcioGpuShader,
  type Project,
  type ProjectGraph,
  type PythonScriptResult,
  type RequestTiming,
} from "./api/client";
import { Inspector, type InspectorTab } from "./inspector/Inspector";
import { CanvasNodeGraph } from "./nodegraph/CanvasNodeGraph";
import { ScriptEditor } from "./scripting/ScriptEditor";
import { useAppStore } from "./store/appStore";
import { ViewerPanel } from "./viewer/ViewerPanel";
import { isWebglFloatViewerSupported, type WebglViewerMetrics } from "./viewer/webglFloatViewer";

const ADDABLE_NODES = [
  "Read",
  "Write",
  "Constant",
  "Group",
  "Grade",
  "Exposure",
  "Saturation",
  "Invert",
  "Clamp",
  "Colorspace",
  "Blur",
  "Crop",
  "Shuffle",
  "Copy",
  "ChannelMerge",
  "AddChannels",
  "Remove",
  "Premult",
  "Unpremult",
  "Cryptomatte",
  "ViewMetadata",
  "CompareMetadata",
  "ModifyMetadata",
  "CopyMetadata",
  "AddTimeCode",
  "Reformat",
  "Scale",
  "Transform",
  "FrameHold",
  "FrameRange",
  "Retime",
  "Merge",
  "ColorCorrect",
  "HueCorrect",
];

const NODE_CATEGORY_ORDER = ["I/O", "Image", "Color", "Channel", "Keyer", "Merge", "Transform", "Filter", "Metadata", "Organization", "Output", "Node"];
const DEFAULT_VIEWER_CHANNELS = ["rgba", "rgb", "r", "g", "b", "a", "luma"];
const DEFAULT_PYTHON_SCRIPT = `node = opencomp.node("Read2")
node.value("path").setValue(r"<path>")
node.value("first_frame").setValue(1001)
node.value("last_frame").setValue(1010)
node.setPosition(280, 120)

root = opencomp.node("root")
root.value("name").setValue("test")
`;
type ViewerTool = "pan" | "crypto-add" | "crypto-remove" | "point" | "spline";
type ViewerCompareMode = "wipe" | "difference";
type FrontendViewerCacheEntry = {
  frame: FloatViewerFrame;
  bytes: number;
};
type FrontendFloatFrameResult = {
  frame: FloatViewerFrame;
  bytes: number;
  frontendCacheHit: boolean;
};
type FrontendViewerCacheState = {
  entries: Map<string, FrontendViewerCacheEntry>;
  bytes: number;
  hits: number;
  misses: number;
  evictions: number;
};
type FileSystemWritableFileStreamLike = {
  write: (data: Blob) => Promise<void>;
  close: () => Promise<void>;
};
type FileSystemFileHandleLike = {
  createWritable: () => Promise<FileSystemWritableFileStreamLike>;
};
type WindowWithSavePicker = Window & {
  showSaveFilePicker?: (options: {
    suggestedName?: string;
    types?: Array<{ description: string; accept: Record<string, string[]> }>;
  }) => Promise<FileSystemFileHandleLike>;
};

export default function App() {
  const {
    backendStatus,
    colorConfig,
    frame,
    graph,
    isPlaying,
    isRendering,
    logs,
    project,
    renderRevision,
    scriptPath,
    selectedNodeId,
    viewerUrl,
    addLog,
    addNode,
    connectNodes,
    deleteSelectedNode,
    moveNode,
    selectNode,
    setBackendStatus,
    setColorConfig,
    setFrame,
    setPlaying,
    setProject,
    setRendering,
    setScriptPath,
    setViewerUrl,
    updateNode,
    updatePreferences,
    updateProjectSettings,
    setViewerInput,
  } = useAppStore();
  const [cacheStatus, setCacheStatus] = useState("cache: -");
  const [showPreferences, setShowPreferences] = useState(false);
  const [showScriptEditor, setShowScriptEditor] = useState(false);
  const [scriptEditorCode, setScriptEditorCode] = useState(DEFAULT_PYTHON_SCRIPT);
  const [scriptOutput, setScriptOutput] = useState("");
  const [isRunningScript, setRunningScript] = useState(false);
  const [nodeCatalog, setNodeCatalog] = useState<NodeCatalogItem[]>([]);
  const [nodePaletteOpen, setNodePaletteOpen] = useState(false);
  const [nodePaletteQuery, setNodePaletteQuery] = useState("");
  const [nodePaletteIndex, setNodePaletteIndex] = useState(0);
  const [lastGraphPosition, setLastGraphPosition] = useState<[number, number]>([220, 180]);
  const [activeRuntimeNodeIds, setActiveRuntimeNodeIds] = useState<string[]>([]);
  const [nodeTimings, setNodeTimings] = useState<Record<string, NodeTiming>>({});
  const [metricsStatus, setMetricsStatus] = useState<CacheStatus | null>(null);
  const [frontendRequestTimings, setFrontendRequestTimings] = useState<RequestTiming[]>([]);
  const [frontendFrameMs, setFrontendFrameMs] = useState<number | null>(null);
  const [playbackStatus, setPlaybackStatus] = useState<string | null>(null);
  const [cachedFrames, setCachedFrames] = useState<number[]>([]);
  const [selectedMetadata, setSelectedMetadata] = useState<NodeMetadata | null>(null);
  const [viewerMetadata, setViewerMetadata] = useState<NodeMetadata | null>(null);
  const [viewerChannel, setViewerChannel] = useState("rgba");
  const [compareViewerUrl, setCompareViewerUrl] = useState<string | null>(null);
  const [viewerGpuFrame, setViewerGpuFrame] = useState<FloatViewerFrame | null>(null);
  const [compareViewerGpuFrame, setCompareViewerGpuFrame] = useState<FloatViewerFrame | null>(null);
  const [ocioGpuShader, setOcioGpuShader] = useState<OcioGpuShader | null>(null);
  const [viewerGpuMetrics, setViewerGpuMetrics] = useState<WebglViewerMetrics | null>(null);
  const [viewerGain, setViewerGain] = useState(1);
  const [viewerSaturation, setViewerSaturation] = useState(1);
  const [viewerFstop, setViewerFstop] = useState(0);
  const [viewerCompareEnabled, setViewerCompareEnabled] = useState(false);
  const [viewerCompareMode, setViewerCompareMode] = useState<ViewerCompareMode>("wipe");
  const [viewerCompareInputA, setViewerCompareInputA] = useState("1");
  const [viewerCompareInputB, setViewerCompareInputB] = useState("2");
  const [wipePosition, setWipePosition] = useState(0.5);
  const [wipeAngle, setWipeAngle] = useState(0);
  const [viewerTool, setViewerTool] = useState<ViewerTool>("pan");
  const [cryptoLayer, setCryptoLayer] = useState("");
  const [cryptoSelection, setCryptoSelection] = useState<CryptomattePick[]>([]);
  const [cryptoPreviewEnabled, setCryptoPreviewEnabled] = useState(false);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("node");
  const [showGraphPanel, setShowGraphPanel] = useState(true);
  const [showViewerPanel, setShowViewerPanel] = useState(true);

  const abortRef = useRef<AbortController | null>(null);
  const openProjectInputRef = useRef<HTMLInputElement | null>(null);
  const nodePaletteInputRef = useRef<HTMLInputElement | null>(null);
  const lastSyncedSettingsRef = useRef("");
  const lastSyncedGraphRef = useRef("");
  const skipNextAutoRefreshRef = useRef(false);
  const lastCompletedAutoRefreshKeyRef = useRef<string | null>(null);
  const firstVisibleFrameRequestRef = useRef<{ key: string; attempts: number }>({ key: "", attempts: 0 });
  const idlePrefetchTimerRef = useRef<number | null>(null);
  const idlePrefetchControllerRef = useRef<AbortController | null>(null);
  const idlePrefetchSessionRef = useRef(0);
  const frontendTimingRef = useRef<{ lastMs: number | null; history: number[] }>({ lastMs: null, history: [] });
  const ocioGpuShaderRef = useRef<{ key: string; shader: OcioGpuShader | null } | null>(null);
  const gpuFallbackLoggedRef = useRef(false);
  const viewerGpuMetricsRef = useRef<WebglViewerMetrics | null>(null);
  const frontendViewerCacheRef = useRef<FrontendViewerCacheState>({
    entries: new Map(),
    bytes: 0,
    hits: 0,
    misses: 0,
    evictions: 0,
  });
  const frontendViewerInflightRef = useRef<Map<string, Promise<FrontendFloatFrameResult>>>(new Map());
  const viewerRenderRequestIdRef = useRef(0);
  const lastCacheStatusRef = useRef<CacheStatus | null>(null);
  const renderRevisionRef = useRef(renderRevision);
  const latestRef = useRef({
    frame,
    graph,
    project,
    cryptoLayer,
    cryptoPreviewEnabled,
    cryptoSelection,
    viewerChannel,
    compareViewerUrl,
    viewerGain,
    viewerSaturation,
    viewerFstop,
    viewerCompareEnabled,
    viewerCompareMode,
    viewerCompareInputA,
    viewerCompareInputB,
    isPlaying,
    viewerNodeId: "Viewer1",
    viewerUrl,
  });

  const selectedNode = useMemo(
    () => (selectedNodeId && graph ? graph.nodes[selectedNodeId] ?? null : null),
    [graph, selectedNodeId],
  );

  const viewerNodeId = useMemo(
    () => Object.values(graph?.nodes ?? {}).find((node) => node.type.toLowerCase() === "viewer")?.id ?? "Viewer1",
    [graph],
  );

  const activeScriptName = useMemo(
    () => project?.script_tabs.find((tab) => tab.id === project.active_script_id)?.name ?? null,
    [project?.active_script_id, project?.script_tabs],
  );

  const paletteNodes = useMemo(() => {
    const catalog =
      nodeCatalog.length > 0
        ? nodeCatalog
        : ADDABLE_NODES.map((type) => ({ type, label: type, category: "Node", inputs: [], outputs: ["out"] }));
    const query = nodePaletteQuery.trim().toLowerCase();
    if (!query) return catalog;
    return catalog.filter((item) =>
      [item.type, item.label, item.category].some((value) => value.toLowerCase().includes(query)),
    );
  }, [nodeCatalog, nodePaletteQuery]);

  const groupedNodeCatalog = useMemo(() => {
    const catalog =
      nodeCatalog.length > 0
        ? nodeCatalog
        : ADDABLE_NODES.map((type) => ({ type, label: type, category: "Node", inputs: [], outputs: ["out"] }));
    const groups = new Map<string, NodeCatalogItem[]>();
    for (const item of catalog) {
      const items = groups.get(item.category) ?? [];
      items.push(item);
      groups.set(item.category, items);
    }
    return [...groups.entries()].sort(([a], [b]) => {
      const aIndex = NODE_CATEGORY_ORDER.includes(a) ? NODE_CATEGORY_ORDER.indexOf(a) : NODE_CATEGORY_ORDER.length;
      const bIndex = NODE_CATEGORY_ORDER.includes(b) ? NODE_CATEGORY_ORDER.indexOf(b) : NODE_CATEGORY_ORDER.length;
      return aIndex - bIndex || a.localeCompare(b);
    });
  }, [nodeCatalog]);

  const availableViewerChannels = useMemo(() => {
    const channels = viewerMetadata?.channels?.length ? viewerMetadata.channels : DEFAULT_VIEWER_CHANNELS;
    return [...new Set([viewerChannel, ...DEFAULT_VIEWER_CHANNELS, ...channels])];
  }, [viewerChannel, viewerMetadata?.channels]);

  const cryptomatteLayers = useMemo(() => viewerMetadata?.cryptomatte_layers ?? [], [viewerMetadata?.cryptomatte_layers]);

  useEffect(() => {
    renderRevisionRef.current = renderRevision;
    latestRef.current = {
      frame,
      graph,
      project,
      cryptoLayer,
      cryptoPreviewEnabled,
      cryptoSelection,
      viewerChannel,
      compareViewerUrl,
      viewerGain,
      viewerSaturation,
      viewerFstop,
      viewerCompareEnabled,
      viewerCompareMode,
      viewerCompareInputA,
      viewerCompareInputB,
      isPlaying,
      viewerNodeId,
      viewerUrl,
    };
  }, [
    compareViewerUrl,
    cryptoLayer,
    cryptoPreviewEnabled,
    cryptoSelection,
    frame,
    graph,
    isPlaying,
    project,
    viewerChannel,
    viewerCompareEnabled,
    viewerCompareInputA,
    viewerCompareInputB,
    viewerCompareMode,
    viewerFstop,
    viewerGain,
    viewerNodeId,
    viewerSaturation,
    viewerUrl,
  ]);

  const refreshCacheStatusLabel = useCallback(
    (status = lastCacheStatusRef.current) => {
      if (!status) return;
      const viewerTiming = status.preview_timings[latestRef.current.viewerNodeId];
      const viewerTimingLabel = viewerTiming
        ? ` | viewer ${viewerTiming.cache_hit ? "cache" : `${Math.round(viewerTiming.total_ms)}ms`}`
        : "";
      const requestTiming = status.last_request_timing;
      const requestTimingLabel = requestTiming
        ? ` | backend ${Math.round(requestTiming.total_ms)}ms/${requestTiming.transport}`
        : "";
      const frameTimingLabel = formatFrameTimingLabel(frontendTimingRef.current);
      const gpuTiming = viewerGpuMetricsRef.current;
      const gpuTimingLabel = gpuTiming
        ? ` | gpu ${Math.round(gpuTiming.upload_ms)}+${Math.round(gpuTiming.draw_ms)}ms/${gpuTiming.ocio_gpu ? "ocio" : "fallback"}`
        : "";
      const frontendCache = frontendViewerCacheRef.current;
      const frontendCacheLabel = ` | browser ${frontendCache.entries.size}/${formatBytes(frontendCache.bytes)} h/m ${frontendCache.hits}/${frontendCache.misses}`;
      const floatEntries = status.float_preview_entries ?? 0;
      const floatHits = status.float_preview_hits ?? 0;
      const floatMisses = status.float_preview_misses ?? 0;
      const floatMemory = status.float_preview_memory_bytes ?? 0;
      setCacheStatus(
        `cache: ${status.entries}+${floatEntries}+${status.preview_entries} | ${formatBytes(
          status.memory_bytes + floatMemory + status.preview_memory_bytes + frontendCache.bytes,
        )} | hit ${status.hits}/${floatHits}/${status.preview_hits} | miss ${status.misses}/${floatMisses}/${status.preview_misses}${frontendCacheLabel}${viewerTimingLabel}${requestTimingLabel}${frameTimingLabel}${gpuTimingLabel}`,
      );
    },
    [],
  );

  const loadCacheStatus = useCallback(async () => {
    try {
      const status = await client.cacheStatus();
      lastCacheStatusRef.current = status;
      setMetricsStatus(status);
      setActiveRuntimeNodeIds(status.active_nodes);
      setNodeTimings(status.node_timings);
      setCachedFrames(viewerReadyCachedFrames(frontendViewerCacheRef.current, latestRef.current, renderRevisionRef.current));
      refreshCacheStatusLabel(status);
    } catch {
      setCacheStatus("cache: unavailable");
      setCachedFrames([]);
      setMetricsStatus(null);
    }
  }, [refreshCacheStatusLabel]);

  const handleViewerGpuMetrics = useCallback((metrics: WebglViewerMetrics | null) => {
    viewerGpuMetricsRef.current = metrics;
    setViewerGpuMetrics(metrics);
    if (metrics) {
      setFrontendRequestTimings((currentTimings) => {
        if (currentTimings.length === 0) return currentTimings;
        const next = [...currentTimings];
        const last = next[next.length - 1];
        next[next.length - 1] = {
          ...last,
          webgl_upload_ms: metrics.upload_ms,
          webgl_draw_ms: metrics.draw_ms,
        };
        return next;
      });
    }
    refreshCacheStatusLabel();
  }, [refreshCacheStatusLabel]);

  const loadColorConfig = useCallback(
    async (logResult = false) => {
      const config = await client.colorConfig();
      setColorConfig(config);
      if (logResult) {
        addLog("info", config.available ? "OCIO configuration loaded." : "OCIO bindings unavailable.");
      }
      return config;
    },
    [addLog, setColorConfig],
  );

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      const retryDelays = [0, 350, 750, 1250, 2000, 3000];
      let lastError: unknown = null;
      for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
        if (cancelled) return;
        if (retryDelays[attempt] > 0) {
          setBackendStatus(`connecting (${attempt + 1}/${retryDelays.length})`);
          await sleep(retryDelays[attempt]);
        }
        try {
          const health = await client.health();
          if (cancelled) return;
          setBackendStatus(`${health.status}: ${health.app}`);
          const catalog = await client.nodeCatalog();
          if (cancelled) return;
          setNodeCatalog(catalog);
          const newProject = await client.newProject();
          if (cancelled) return;
          const config = await loadColorConfig();
          if (cancelled) return;
          const settings = {
            ...newProject.settings,
            viewer_display: newProject.settings.viewer_display ?? config.default_display,
            viewer_view: newProject.settings.viewer_view ?? config.default_view,
          };
          await client.putProjectSettings(settings);
          if (cancelled) return;
          setProject({ ...newProject, settings });
          lastSyncedSettingsRef.current = JSON.stringify(settings);
          lastSyncedGraphRef.current = JSON.stringify(newProject.graph);
          await loadCacheStatus();
          if (!cancelled) addLog("info", "Backend connected and reference sequence project loaded.");
          return;
        } catch (error) {
          lastError = error;
        }
      }
      if (cancelled) return;
      setBackendStatus("offline");
      addLog("error", lastError instanceof Error ? lastError.message : String(lastError ?? "Backend unavailable."));
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [addLog, loadCacheStatus, loadColorConfig, setBackendStatus, setProject]);

  useEffect(() => {
    if (!project) return;
    void loadColorConfig();
  }, [loadColorConfig, project?.settings.ocio_config, project?.settings.viewer_display]);

  useEffect(() => {
    if (!selectedNodeId) {
      setSelectedMetadata(null);
      return;
    }
    const nodeId = selectedNodeId;
    let cancelled = false;
    async function loadSelectedMetadata() {
      try {
        const metadata = await client.nodeMetadata(nodeId, latestRef.current.frame);
        if (!cancelled) setSelectedMetadata(metadata);
      } catch {
        if (!cancelled) setSelectedMetadata(null);
      }
    }
    void loadSelectedMetadata();
    return () => {
      cancelled = true;
    };
  }, [frame, renderRevision, selectedNodeId]);

  useEffect(() => {
    if (!graph || !viewerNodeId) {
      setViewerMetadata(null);
      return;
    }
    let cancelled = false;
    async function loadViewerMetadata() {
      try {
        const metadata = await client.nodeMetadata(viewerNodeId, latestRef.current.frame);
        if (!cancelled) setViewerMetadata(metadata);
      } catch {
        if (!cancelled) setViewerMetadata(null);
      }
    }
    void loadViewerMetadata();
    return () => {
      cancelled = true;
    };
  }, [frame, graph, renderRevision, viewerNodeId]);

  useEffect(() => {
    if (cryptomatteLayers.length === 0) {
      setCryptoLayer("");
      setCryptoSelection([]);
      setCryptoPreviewEnabled(false);
      if (viewerTool === "crypto-add" || viewerTool === "crypto-remove") setViewerTool("pan");
      return;
    }
    if (!cryptomatteLayers.some((layer) => layer.name === cryptoLayer)) {
      setCryptoLayer(cryptomatteLayers[0].name);
      setCryptoSelection([]);
      setCryptoPreviewEnabled(true);
    }
  }, [cryptoLayer, cryptomatteLayers, viewerTool]);

  const syncGraphAndSettings = useCallback(async () => {
    const current = latestRef.current;
    if (!current.graph || !current.project) return;
    const settingsPayload = JSON.stringify(current.project.settings);
    if (settingsPayload !== lastSyncedSettingsRef.current) {
      await client.putProjectSettings(current.project.settings);
      lastSyncedSettingsRef.current = settingsPayload;
    }
    const graphPayload = JSON.stringify(current.graph);
    if (graphPayload !== lastSyncedGraphRef.current) {
      await client.putGraph(current.graph);
      lastSyncedGraphRef.current = graphPayload;
    }
  }, []);

  const currentRenderKey = useCallback(
    (frameNumber = latestRef.current.frame) => {
      const current = latestRef.current;
      const settings = current.project?.settings;
      return JSON.stringify({
        frame: frameNumber,
        renderRevision,
        viewerNodeId: current.viewerNodeId,
        viewerChannel: current.viewerChannel,
        viewerProcess: isWebglFloatViewerSupported()
          ? null
          : {
              gain: current.viewerGain,
              saturation: current.viewerSaturation,
              fstop: current.viewerFstop,
            },
        viewerCompareEnabled: current.viewerCompareEnabled,
        viewerCompareMode: current.viewerCompareMode,
        viewerCompareInputA: current.viewerCompareInputA,
        viewerCompareInputB: current.viewerCompareInputB,
        viewerDisplay: settings?.viewer_display ?? null,
        viewerView: settings?.viewer_view ?? null,
        proxyEnabled: settings?.proxy_enabled ?? false,
        viewerMaxWidth: settings?.viewer_max_width ?? null,
        viewerMaxHeight: settings?.viewer_max_height ?? null,
        tileRenderingEnabled: settings?.tile_rendering_enabled ?? false,
        tileHeight: settings?.tile_height ?? null,
        tileWorkers: settings?.tile_workers ?? null,
        cryptoLayer: current.cryptoLayer,
        cryptoPreviewEnabled: current.cryptoPreviewEnabled,
        cryptoSelection: current.cryptoSelection.map((selection) => selection.id).sort(),
      });
    },
    [renderRevision],
  );

  const loadOcioGpuShader = useCallback(
    async (frameData: FloatViewerFrame, signal: AbortSignal) => {
      if (!frameData.header.apply_ocio) {
        setOcioGpuShader(null);
        ocioGpuShaderRef.current = null;
        return null;
      }
      const display = latestRef.current.project?.settings.viewer_display ?? null;
      const view = latestRef.current.project?.settings.viewer_view ?? null;
      const key = `${frameData.header.colorspace}|${display ?? ""}|${view ?? ""}`;
      if (ocioGpuShaderRef.current?.key === key) {
        return ocioGpuShaderRef.current.shader;
      }
      try {
        const shader = await client.colorGpuShader(frameData.header.colorspace, display, view);
        if (signal.aborted) return null;
        ocioGpuShaderRef.current = { key, shader };
        setOcioGpuShader(shader);
        return shader;
      } catch (error) {
        if (signal.aborted) return null;
        const fallbackShader: OcioGpuShader = {
          available: false,
          reason: error instanceof Error ? error.message : String(error),
          source: frameData.header.colorspace,
          display,
          view,
          language: "GLSL",
          shader_text: null,
          function_name: null,
          textures: [],
        };
        ocioGpuShaderRef.current = { key, shader: fallbackShader };
        setOcioGpuShader(fallbackShader);
        return fallbackShader;
      }
    },
    [],
  );

  const requestCachedFloatFrame = useCallback(
    async (
      frameNumber: number,
      viewerInput: string | null,
      signal: AbortSignal,
      snapshot = latestRef.current,
      onProgress?: (frame: FloatViewerFrame) => void,
    ): Promise<FrontendFloatFrameResult> => {
      if (!snapshot.graph || !snapshot.project) {
        throw new Error("No project graph is loaded.");
      }
      const cacheKey = viewerFloatCacheKey(
        snapshot.graph,
        snapshot.project,
        renderRevisionRef.current,
        snapshot.viewerNodeId,
        frameNumber,
        snapshot.viewerChannel,
        viewerInput,
      );
      const cacheLookupStarted = performance.now();
      const cachedFrame = getFrontendViewerFrame(frontendViewerCacheRef.current, cacheKey);
      if (cachedFrame) {
        const browserCacheHitMs = performance.now() - cacheLookupStarted;
        return {
          frame: {
            ...cachedFrame,
            metrics: {
              ws_wait_ms: 0,
              receive_ms: 0,
              tile_copy_ms: 0,
              bytes: 0,
              browser_cache_hit_ms: Math.round(browserCacheHitMs * 100) / 100,
            },
          },
          bytes: 0,
          frontendCacheHit: true,
        };
      }
      const inFlight = frontendViewerInflightRef.current.get(cacheKey);
      if (inFlight) return inFlight;

      const requestPromise = (async () => {
        const settings = snapshot.project!.settings;
        const viewerPrecision = snapshot.project!.preferences.viewer_transfer_precision ?? "float16";
        const frameData = await client.viewerFloatFrameStream(
          snapshot.viewerNodeId,
          frameNumber,
          settings.viewer_display,
          settings.viewer_view,
          snapshot.viewerChannel,
          signal,
          {
            ...(viewerInput === null ? {} : { viewerInput }),
            precision: viewerPrecision,
            tileHeight: settings.tile_height ?? 128,
            tileLanes: Math.max(1, Math.min(Math.round(settings.viewer_tile_lanes ?? 1), 8)),
            transferMode: transferModeForPrecision(viewerPrecision),
          },
          onProgress,
        );
        storeFrontendViewerFrame(
          frontendViewerCacheRef.current,
          cacheKey,
          frameData,
          frontendViewerCacheLimitBytes(snapshot.project),
        );
        return { frame: frameData, bytes: frameData.header.byte_length, frontendCacheHit: false };
      })();

      frontendViewerInflightRef.current.set(cacheKey, requestPromise);
      try {
        return await requestPromise;
      } finally {
        if (frontendViewerInflightRef.current.get(cacheKey) === requestPromise) {
          frontendViewerInflightRef.current.delete(cacheKey);
        }
      }
    },
    [],
  );

  const resetFrontendViewerCache = useCallback(() => {
    clearFrontendViewerCache(frontendViewerCacheRef.current);
    frontendViewerInflightRef.current.clear();
    lastCompletedAutoRefreshKeyRef.current = null;
    firstVisibleFrameRequestRef.current = { key: "", attempts: 0 };
  }, []);

  const scheduleReadPreload = useCallback(
    (frames: number[], snapshot: typeof latestRef.current) => {
      const snapshotProject = snapshot.project;
      if (!snapshotProject || !snapshotProject.preferences.read_preload_enabled || frames.length === 0) return;
      const maxFrames = Math.max(1, Math.round(snapshotProject.preferences.read_preload_max_frames ?? 6));
      const boundedFrames = [
        ...new Set(frames.map((value) => clampFrame(value, snapshotProject.settings.frame_start, snapshotProject.settings.frame_end))),
      ].slice(0, maxFrames);
      const inputs = snapshot.viewerCompareEnabled ? [snapshot.viewerCompareInputA, snapshot.viewerCompareInputB] : [null];
      for (const viewerInput of inputs) {
        void client
          .warmReadFrames(snapshot.viewerNodeId, boundedFrames, { viewerInput, channel: snapshot.viewerChannel })
          .catch((error) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              addLog("error", `Read preload failed: ${error instanceof Error ? error.message : String(error)}`);
            }
          });
      }
    },
    [addLog],
  );

  const renderViewerFrame = useCallback(
    async (frameNumber: number, controller: AbortController) => {
      const current = latestRef.current;
      if (!current.graph || !current.project) return false;
      const requestId = ++viewerRenderRequestIdRef.current;
      const isCurrentRequest = () => !controller.signal.aborted && viewerRenderRequestIdRef.current === requestId;

      await syncGraphAndSettings();
      const latest = latestRef.current;
      if (!latest.project || !isCurrentRequest()) return false;

      const selectedMatteIds = latest.cryptoSelection.map((selection) => selection.id);
      const proxyWidth = latest.project.settings.proxy_enabled ? latest.project.settings.viewer_max_width : null;
      const proxyHeight = latest.project.settings.proxy_enabled ? latest.project.settings.viewer_max_height : null;
      const viewerProcessOptions = {
        gain: latest.viewerGain,
        saturation: latest.viewerSaturation,
        fstop: latest.viewerFstop,
      };
      const requestViewerFrame = (
        viewerInputOptions: Parameters<typeof client.viewerFrame>[6] = {},
      ) =>
        client
          .viewerFrameStream(
            latest.viewerNodeId,
            frameNumber,
            latest.project!.settings.viewer_display,
            latest.project!.settings.viewer_view,
            latest.viewerChannel,
            controller.signal,
            viewerInputOptions,
          )
          .catch((error) => {
            if (error instanceof DOMException && error.name === "AbortError") throw error;
            return client.viewerFrame(
              latest.viewerNodeId,
              frameNumber,
              latest.project!.settings.viewer_display,
              latest.project!.settings.viewer_view,
              latest.viewerChannel,
              controller.signal,
              viewerInputOptions,
            );
          });
      const requestFloatFrame = (viewerInput: string | null = null, onProgress?: (frame: FloatViewerFrame) => void) =>
        requestCachedFloatFrame(frameNumber, viewerInput, controller.signal, latest, onProgress);
      const requestCpuPreview = async () => {
        if (latest.viewerCompareEnabled && latest.viewerCompareMode === "difference") {
          return {
            blob: await requestViewerFrame({
              ...viewerProcessOptions,
              viewerInput: latest.viewerCompareInputA,
              compareInput: latest.viewerCompareInputB,
              compareMode: "difference",
            }),
            compareBlob: null,
          };
        }
        if (latest.viewerCompareEnabled && latest.viewerCompareMode === "wipe") {
          const [nextBlob, nextCompareBlob] = await Promise.all([
            requestViewerFrame({ ...viewerProcessOptions, viewerInput: latest.viewerCompareInputA }),
            requestViewerFrame({ ...viewerProcessOptions, viewerInput: latest.viewerCompareInputB }),
          ]);
          return { blob: nextBlob, compareBlob: nextCompareBlob };
        }
        return { blob: await requestViewerFrame(viewerProcessOptions), compareBlob: null };
      };
      let blob: Blob | null = null;
      let compareBlob: Blob | null = null;
      let nextGpuFrame: FloatViewerFrame | null = null;
      let nextGpuCompareFrame: FloatViewerFrame | null = null;
      let frontendTransport = "browser";
      let payloadBytes = 0;
      let servedFromFrontendViewerCache = false;
      let partialShaderRequested = false;
      let clientFrameMetrics: FloatViewerFrame["metrics"] | null = null;
      const playbackTransferMode = latest.project.preferences.playback_transfer_mode ?? "hybrid-preview";
      const useDisplayPreviewForPlayback = latest.isPlaying && playbackTransferMode === "fast-display";
      const publishPartialFrame = (partialFrame: FloatViewerFrame) => {
        if (!isCurrentRequest() || latest.viewerCompareEnabled) return;
        if (!partialShaderRequested) {
          partialShaderRequested = true;
          void loadOcioGpuShader(partialFrame, controller.signal);
        }
        if (latestRef.current.frame !== frameNumber) setFrame(frameNumber);
        setViewerUrl(null);
        setCompareViewerUrl(null);
        setViewerGpuFrame(partialFrame);
        setCompareViewerGpuFrame(null);
      };
      const frontendStarted = performance.now();
      if (latest.cryptoPreviewEnabled && latest.cryptoLayer) {
        blob = await client.cryptomatteMatte(
          latest.viewerNodeId,
          frameNumber,
          latest.cryptoLayer,
          selectedMatteIds,
          proxyWidth,
          proxyHeight,
          controller.signal,
        );
        payloadBytes = blob.size;
      } else {
        if (!useDisplayPreviewForPlayback && isWebglFloatViewerSupported()) {
          try {
            if (latest.viewerCompareEnabled) {
              const [frameA, frameB] = await Promise.all([
                requestFloatFrame(latest.viewerCompareInputA),
                requestFloatFrame(latest.viewerCompareInputB),
              ]);
              nextGpuFrame = frameA.frame;
              nextGpuCompareFrame = frameB.frame;
              payloadBytes = frameA.bytes + frameB.bytes;
              servedFromFrontendViewerCache = frameA.frontendCacheHit && frameB.frontendCacheHit;
              clientFrameMetrics = combineFrameMetrics(frameA.frame.metrics, frameB.frame.metrics);
            } else {
              const frameData = await requestFloatFrame(null, publishPartialFrame);
              nextGpuFrame = frameData.frame;
              payloadBytes = frameData.bytes;
              servedFromFrontendViewerCache = frameData.frontendCacheHit;
              clientFrameMetrics = frameData.frame.metrics ?? null;
            }
            if (nextGpuFrame && isCurrentRequest()) {
              await loadOcioGpuShader(nextGpuFrame, controller.signal);
              frontendTransport = servedFromFrontendViewerCache ? "browser-float-cache" : viewerFrameTransport(nextGpuFrame);
            }
          } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") throw error;
            if (!gpuFallbackLoggedRef.current) {
              gpuFallbackLoggedRef.current = true;
              addLog("error", `GPU float viewer fallback: ${error instanceof Error ? error.message : String(error)}`);
            }
            nextGpuFrame = null;
            nextGpuCompareFrame = null;
          }
        }
        if (!nextGpuFrame) {
          const cpuPreview = await requestCpuPreview();
          blob = cpuPreview.blob;
          compareBlob = cpuPreview.compareBlob;
          payloadBytes = blob.size + (compareBlob?.size ?? 0);
          if (useDisplayPreviewForPlayback) {
            frontendTransport = `display-preview-${playbackTransferMode}`;
          }
        }
      }
      const frontendMs = performance.now() - frontendStarted;
      if (!isCurrentRequest()) return false;
      const nextHistory = [...frontendTimingRef.current.history, frontendMs].slice(-30);
      frontendTimingRef.current = { lastMs: frontendMs, history: nextHistory };
      setFrontendFrameMs(frontendMs);
      setFrontendRequestTimings((currentTimings) =>
        [
          ...currentTimings,
          {
            type: "frontend_viewer_frame",
            node_id: latest.viewerNodeId,
            frame: frameNumber,
            viewer_input:
              latest.viewerCompareEnabled && latest.viewerCompareMode === "difference"
                ? latest.viewerCompareInputA
                : latest.viewerCompareEnabled && latest.viewerCompareMode === "wipe"
                  ? `${latest.viewerCompareInputA},${latest.viewerCompareInputB}`
                  : null,
            compare_input: latest.viewerCompareEnabled ? latest.viewerCompareInputB : null,
            compare_mode: latest.viewerCompareEnabled ? latest.viewerCompareMode : "none",
            channel: latest.viewerChannel,
            transport: frontendTransport,
            total_ms: Math.round(frontendMs * 100) / 100,
            backend_render_ms: 0,
            send_ms: 0,
            bytes: payloadBytes,
            frontend_cache_hit: servedFromFrontendViewerCache,
            ws_wait_ms: clientFrameMetrics?.ws_wait_ms ?? 0,
            receive_ms: clientFrameMetrics?.receive_ms ?? 0,
            tile_copy_ms: clientFrameMetrics?.tile_copy_ms ?? 0,
            browser_cache_hit_ms: clientFrameMetrics?.browser_cache_hit_ms ?? 0,
            timestamp: Date.now() / 1000,
          },
        ].slice(-80),
      );
      if (!isCurrentRequest()) return false;

      const previousUrl = latestRef.current.viewerUrl;
      if (previousUrl) URL.revokeObjectURL(previousUrl);
      const previousCompareUrl = latestRef.current.compareViewerUrl;
      if (previousCompareUrl) URL.revokeObjectURL(previousCompareUrl);
      lastCompletedAutoRefreshKeyRef.current = currentRenderKey(frameNumber);
      if (latestRef.current.frame !== frameNumber) setFrame(frameNumber);
      if (nextGpuFrame) {
        setViewerUrl(null);
        setCompareViewerUrl(null);
        setViewerGpuFrame(nextGpuFrame);
        setCompareViewerGpuFrame(nextGpuCompareFrame);
      } else if (blob) {
        const url = URL.createObjectURL(blob);
        const compareUrl = compareBlob ? URL.createObjectURL(compareBlob) : null;
        viewerGpuMetricsRef.current = null;
        setViewerGpuMetrics(null);
        setViewerGpuFrame(null);
        setCompareViewerGpuFrame(null);
        setViewerUrl(url);
        setCompareViewerUrl(compareUrl);
      }
      if (servedFromFrontendViewerCache && nextGpuFrame) {
        setCachedFrames(viewerReadyCachedFrames(frontendViewerCacheRef.current, latestRef.current, renderRevisionRef.current));
        refreshCacheStatusLabel();
      } else {
        await loadCacheStatus();
      }
      return true;
    },
    [addLog, currentRenderKey, loadCacheStatus, loadOcioGpuShader, refreshCacheStatusLabel, setFrame, setViewerUrl, syncGraphAndSettings],
  );

  const cancelIdlePrefetch = useCallback(() => {
    if (idlePrefetchTimerRef.current !== null) {
      window.clearTimeout(idlePrefetchTimerRef.current);
      idlePrefetchTimerRef.current = null;
    }
    idlePrefetchControllerRef.current?.abort();
    idlePrefetchControllerRef.current = null;
  }, []);

  const refreshViewer = useCallback(
    async (silent = false) => {
      const current = latestRef.current;
      if (!current.graph || !current.project) return;

      cancelIdlePrefetch();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setRendering(true);

      try {
        await renderViewerFrame(current.frame, controller);
        if (!silent) addLog("info", `Rendered ${current.viewerNodeId} at frame ${current.frame}.`);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        addLog("error", error instanceof Error ? error.message : String(error));
      } finally {
        if (abortRef.current === controller) {
          setRendering(false);
        }
      }
    },
    [addLog, cancelIdlePrefetch, renderViewerFrame, setRendering],
  );

  useEffect(() => {
    if (!showViewerPanel || !project || !graph || isPlaying || isRendering || cryptoPreviewEnabled) return;
    const viewerHasCompleteFrame = Boolean(viewerUrl || (viewerGpuFrame && !viewerGpuFrame.header.partial));
    if (viewerHasCompleteFrame) {
      firstVisibleFrameRequestRef.current = { key: "", attempts: 0 };
      return;
    }

    const key = currentRenderKey();
    const current = firstVisibleFrameRequestRef.current;
    const attempts = current.key === key ? current.attempts : 0;
    if (attempts >= 4) return;

    const handle = window.setTimeout(() => {
      const currentViewerComplete = Boolean(latestRef.current.viewerUrl || (viewerGpuFrame && !viewerGpuFrame.header.partial));
      if (currentViewerComplete || latestRef.current.isPlaying) return;
      firstVisibleFrameRequestRef.current = { key, attempts: attempts + 1 };
      void refreshViewer(true);
    }, attempts === 0 ? 80 : 400);
    return () => window.clearTimeout(handle);
  }, [
    cryptoPreviewEnabled,
    currentRenderKey,
    graph,
    isPlaying,
    isRendering,
    project,
    refreshViewer,
    showViewerPanel,
    viewerGpuFrame,
    viewerUrl,
  ]);

  const handleFrameChange = useCallback(
    (nextFrame: number) => {
      const current = latestRef.current;
      const settings = current.project?.settings;
      const normalized = settings ? clampFrame(nextFrame, settings.frame_start, settings.frame_end) : nextFrame;
      cancelIdlePrefetch();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setFrame(normalized);
      setRendering(true);
      void renderViewerFrame(normalized, controller)
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          addLog("error", error instanceof Error ? error.message : String(error));
        })
        .finally(() => {
          if (abortRef.current === controller) {
            setRendering(false);
          }
        });
    },
    [addLog, cancelIdlePrefetch, renderViewerFrame, setFrame, setRendering],
  );

  const runIdlePrefetch = useCallback(
    async (sessionId: number, anchorFrame: number, snapshot: typeof latestRef.current) => {
      if (!snapshot.graph || !snapshot.project || snapshot.cryptoPreviewEnabled || !isWebglFloatViewerSupported()) return;
      const settings = snapshot.project.settings;
      if (
        !settings.proxy_enabled &&
        !viewerReadyCachedFrames(frontendViewerCacheRef.current, snapshot, renderRevisionRef.current).includes(anchorFrame)
      ) {
        return;
      }
      const frames = idlePrefetchFrameOrder(anchorFrame, settings.frame_start, settings.frame_end);
      if (frames.length === 0) return;
      scheduleReadPreload(frames, snapshot);

      const controller = new AbortController();
      idlePrefetchControllerRef.current = controller;
      setPlaybackStatus(`warming viewer cache`);
      try {
        for (const frameToWarm of frames) {
          if (controller.signal.aborted || idlePrefetchSessionRef.current !== sessionId) break;
          setPlaybackStatus(`warming F${frameToWarm}`);
          if (snapshot.viewerCompareEnabled) {
            await Promise.all([
              requestCachedFloatFrame(frameToWarm, snapshot.viewerCompareInputA, controller.signal, snapshot),
              requestCachedFloatFrame(frameToWarm, snapshot.viewerCompareInputB, controller.signal, snapshot),
            ]);
          } else {
            await requestCachedFloatFrame(frameToWarm, null, controller.signal, snapshot);
          }
          await yieldToBrowser(controller.signal);
          setCachedFrames(viewerReadyCachedFrames(frontendViewerCacheRef.current, latestRef.current, renderRevisionRef.current));
          refreshCacheStatusLabel();
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          addLog("error", `Viewer cache warm failed: ${error instanceof Error ? error.message : String(error)}`);
        }
      } finally {
        if (idlePrefetchControllerRef.current === controller) {
          idlePrefetchControllerRef.current = null;
        }
        if (idlePrefetchSessionRef.current === sessionId) {
          setPlaybackStatus(null);
        }
      }
    },
    [addLog, refreshCacheStatusLabel, requestCachedFloatFrame, scheduleReadPreload],
  );

  useEffect(() => {
    cancelIdlePrefetch();
    if (!project || isPlaying || isRendering || cryptoPreviewEnabled || !project.settings.auto_refresh) return;
    const activeViewerFrameReady = Boolean(viewerUrl || (viewerGpuFrame && !viewerGpuFrame.header.partial));
    if (!activeViewerFrameReady) return;
    const sessionId = idlePrefetchSessionRef.current + 1;
    idlePrefetchSessionRef.current = sessionId;
    const snapshot = { ...latestRef.current };
    idlePrefetchTimerRef.current = window.setTimeout(() => {
      idlePrefetchTimerRef.current = null;
      void runIdlePrefetch(sessionId, snapshot.frame, snapshot);
    }, 5000);
    return () => {
      if (idlePrefetchSessionRef.current === sessionId) {
        cancelIdlePrefetch();
      }
    };
  }, [
    cancelIdlePrefetch,
    cryptoPreviewEnabled,
    frame,
    isPlaying,
    isRendering,
    project?.settings.auto_refresh,
    project?.settings.frame_start,
    project?.settings.frame_end,
    project?.settings.proxy_enabled,
    project?.settings.viewer_max_width,
    project?.settings.viewer_max_height,
    renderRevision,
    runIdlePrefetch,
    viewerChannel,
    viewerCompareEnabled,
    viewerCompareInputA,
    viewerCompareInputB,
    viewerGpuFrame,
    viewerUrl,
  ]);

  useEffect(() => {
    if (!project || isPlaying || cryptoPreviewEnabled || !project.settings.cache_enabled) return;
    if (!project.preferences.read_preload_enabled) return;
    const snapshot = { ...latestRef.current };
    const maxFrames = Math.max(1, Math.round(project.preferences.read_preload_max_frames ?? 6));
    const frames = readPreloadFrameOrder(frame, project.settings.frame_start, project.settings.frame_end, maxFrames);
    const handle = window.setTimeout(() => scheduleReadPreload(frames, snapshot), 250);
    return () => window.clearTimeout(handle);
  }, [
    cryptoPreviewEnabled,
    frame,
    isPlaying,
    project,
    project?.preferences.read_preload_enabled,
    project?.preferences.read_preload_max_frames,
    project?.settings.cache_enabled,
    project?.settings.frame_start,
    project?.settings.frame_end,
    renderRevision,
    scheduleReadPreload,
    viewerChannel,
    viewerCompareEnabled,
    viewerCompareInputA,
    viewerCompareInputB,
  ]);

  useEffect(() => {
    if (isPlaying) return;
    if (isRendering) return;
    if (!project?.settings.auto_refresh || !latestRef.current.graph) return;
    if (skipNextAutoRefreshRef.current) {
      skipNextAutoRefreshRef.current = false;
      return;
    }
    const scheduledKey = currentRenderKey();
    const viewerHasCompleteFrame = Boolean(viewerUrl || (viewerGpuFrame && !viewerGpuFrame.header.partial));
    if (lastCompletedAutoRefreshKeyRef.current === scheduledKey && viewerHasCompleteFrame) return;
    const handle = window.setTimeout(() => {
      const stillHasCompleteFrame = Boolean(latestRef.current.viewerUrl || (viewerGpuFrame && !viewerGpuFrame.header.partial));
      if (lastCompletedAutoRefreshKeyRef.current === scheduledKey && stillHasCompleteFrame) return;
      void refreshViewer(true);
    }, 120);
    return () => window.clearTimeout(handle);
  }, [
    frame,
    project?.settings.auto_refresh,
    project?.settings.viewer_display,
    project?.settings.viewer_view,
    project?.settings.proxy_enabled,
    project?.settings.viewer_max_width,
    project?.settings.viewer_max_height,
    project?.settings.tile_rendering_enabled,
    project?.settings.tile_height,
    project?.settings.tile_workers,
    viewerChannel,
    viewerCompareEnabled,
    viewerCompareInputA,
    viewerCompareInputB,
    viewerCompareMode,
    viewerFstop,
    viewerGain,
    viewerSaturation,
    viewerGpuFrame,
    viewerUrl,
    cryptoLayer,
    cryptoPreviewEnabled,
    cryptoSelection,
    currentRenderKey,
    isPlaying,
    isRendering,
    refreshViewer,
    renderRevision,
  ]);

  useEffect(() => {
    if (!isPlaying || !project) return;
    const frameStart = project.settings.frame_start;
    const frameEnd = project.settings.frame_end;
    const delay = Math.max(1000 / Math.max(project.settings.fps, 1), 1);
    const controller = new AbortController();
    let cancelled = false;
    cancelIdlePrefetch();
    abortRef.current?.abort();
    abortRef.current = controller;
    setRendering(true);
    setPlaybackStatus(`caching ${frameStart}-${frameEnd}`);
    addLog("info", `Playback caching ${frameStart}-${frameEnd}.`);

    const nextFrame = (current: number) => (current >= frameEnd ? frameStart : current + 1);
    const warmAhead = (frameToRender: number) => {
      const latest = latestRef.current;
      if (!latest.project || !latest.graph) return;
      const frames = playbackAheadFrameOrder(
        frameToRender,
        frameStart,
        frameEnd,
        Math.max(2, Math.min((latest.project.settings.render_workers ?? 4) * 2, 12)),
      ).filter((frameToWarm) => frameToWarm !== frameToRender);
      if (frames.length === 0) return;
      scheduleReadPreload([frameToRender, ...frames], latest);
      const display = latest.project.settings.viewer_display;
      const view = latest.project.settings.viewer_view;
      const channel = latest.viewerChannel;
      const inputs = latest.viewerCompareEnabled ? [latest.viewerCompareInputA, latest.viewerCompareInputB] : [null];
      for (const viewerInput of inputs) {
        void client
          .warmViewerFrames(latest.viewerNodeId, frames, { viewerInput, display, view, channel })
          .catch((error) => {
            if (!(error instanceof DOMException && error.name === "AbortError")) {
              addLog("error", `Backend warm failed: ${error instanceof Error ? error.message : String(error)}`);
            }
          });
      }

      const frontendFrameLimit = latest.project.settings.proxy_enabled
        ? Math.max(2, Math.min(latest.project.settings.viewer_tile_lanes ?? 3, 6))
        : 1;
      for (const viewerInput of inputs) {
        for (const frameToWarm of frames.slice(0, frontendFrameLimit)) {
          void requestCachedFloatFrame(frameToWarm, viewerInput, controller.signal, latest)
            .then(() => {
              setCachedFrames(viewerReadyCachedFrames(frontendViewerCacheRef.current, latestRef.current, renderRevisionRef.current));
              refreshCacheStatusLabel();
            })
            .catch((error) => {
              if (!(error instanceof DOMException && error.name === "AbortError")) {
                addLog("error", `Browser warm failed: ${error instanceof Error ? error.message : String(error)}`);
              }
            });
        }
      }
    };

    async function playLoop() {
      let frameToRender = nextFrame(latestRef.current.frame);
      try {
        while (!cancelled && !controller.signal.aborted) {
          const started = performance.now();
          setPlaybackStatus(`caching F${frameToRender}`);
          warmAhead(frameToRender);
          const rendered = await renderViewerFrame(frameToRender, controller);
          if (!rendered || cancelled || controller.signal.aborted) break;

          const elapsed = performance.now() - started;
          const wait = Math.max(delay - elapsed, 0);
          if (wait > 0) {
            await new Promise((resolve) => window.setTimeout(resolve, wait));
          }
          frameToRender = nextFrame(frameToRender);
        }
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          addLog("error", error instanceof Error ? error.message : String(error));
          setPlaying(false);
        }
      } finally {
        if (abortRef.current === controller) {
          setRendering(false);
        }
        setPlaybackStatus(null);
      }
    }

    void playLoop();
    return () => {
      cancelled = true;
      controller.abort();
      if (abortRef.current === controller) {
        setRendering(false);
      }
      setPlaybackStatus(null);
    };
  }, [
    addLog,
    cancelIdlePrefetch,
    isPlaying,
    project,
    refreshCacheStatusLabel,
    requestCachedFloatFrame,
    renderViewerFrame,
    scheduleReadPreload,
    setPlaying,
    setRendering,
  ]);

  useEffect(() => {
    if (!isRendering) return;
    const handle = window.setInterval(() => {
      void loadCacheStatus();
    }, 250);
    return () => window.clearInterval(handle);
  }, [isRendering, loadCacheStatus]);

  useEffect(() => {
    if (!nodePaletteOpen) return;
    window.setTimeout(() => {
      nodePaletteInputRef.current?.focus();
      nodePaletteInputRef.current?.select();
    }, 0);
  }, [nodePaletteOpen]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || isEditableTarget(event.target)) return;
      if (event.key === "Tab") {
        event.preventDefault();
        setNodePaletteOpen(true);
        setNodePaletteQuery("");
        setNodePaletteIndex(0);
        return;
      }
      if (/^[0-9]$/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        const result = setViewerInput(event.key);
        if (result.status === "assigned") {
          addLog("info", `Viewer input ${result.slot} set to ${result.nodeId}.`);
        } else if (result.status === "switched") {
          addLog("info", `Viewer switched to input ${result.slot}.`);
        } else {
          addLog("error", "No Viewer node is available.");
          return;
        }
        skipNextAutoRefreshRef.current = true;
        window.setTimeout(() => {
          void refreshViewer(true);
        }, 20);
        return;
      }
      if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        const result = deleteSelectedNode();
        if (result.status === "deleted") {
          addLog("info", `Deleted ${result.nodeId}.`);
        } else if (result.status === "protected-viewer") {
          addLog("error", "The last Viewer node cannot be deleted.");
        }
        return;
      }
      if (event.key.toLowerCase() === "s" && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
        event.preventDefault();
        setInspectorTab("root");
        return;
      }
      const hotkeys = project?.preferences.hotkeys;
      if (matchesHotkey(event, hotkeys?.toggle_disable ?? "d")) {
        event.preventDefault();
        if (!selectedNode) {
          addLog("error", "Select a node to disable.");
          return;
        }
        const disabled = !isNodeDisabledParam(selectedNode.params);
        updateNode({
          ...selectedNode,
          params: {
            ...selectedNode.params,
            disabled,
          },
        });
        addLog("info", `${disabled ? "Disabled" : "Enabled"} ${selectedNode.id}.`);
        return;
      }
      const actions: Array<[string, string]> = [
        [hotkeys?.add_read ?? "r", "Read"],
        [hotkeys?.add_write ?? "w", "Write"],
        [hotkeys?.add_merge ?? "m", "Merge"],
        [hotkeys?.add_shuffle ?? "s", "Shuffle"],
        [hotkeys?.add_group ?? "g", "Group"],
      ];
      if (matchesHotkey(event, hotkeys?.refresh_viewer ?? "u")) {
        event.preventDefault();
        void refreshViewer();
        return;
      }
      const action = actions.find(([shortcut]) => matchesHotkey(event, shortcut));
      if (action) {
        event.preventDefault();
        const type = action[1];
        addNode(type, { position: lastGraphPosition });
        addLog("info", `Added ${type} node.`);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    addLog,
    addNode,
    deleteSelectedNode,
    lastGraphPosition,
    project?.preferences.hotkeys,
    refreshViewer,
    selectedNode,
    setViewerInput,
    updateNode,
  ]);

  async function saveProject(pathOverride?: string | null) {
    const current = latestRef.current;
    if (!current.project || !current.graph) return;
    if (pathOverride === undefined && !current.project.settings.project_path) {
      await saveProjectToBrowserFile(undefined, true);
      return;
    }
    const targetPath =
      pathOverride === undefined
        ? current.project.settings.project_path
        : pathOverride;
    if (!targetPath) {
      addLog("info", "Save cancelled.");
      return;
    }
    if (!isBackendFilesystemPath(targetPath)) {
      await saveProjectToBrowserFile(targetPath, false);
      return;
    }
    try {
      await syncGraphAndSettings();
      const refreshed = latestRef.current;
      if (!refreshed.project || !refreshed.graph) return;
      const projectToSave = projectWithCurrentGraph(refreshed.project, refreshed.graph);
      const saved = await client.saveProject(targetPath, projectToSave);
      setProject(saved);
      addLog("info", `Project saved to ${saved.settings.project_path ?? targetPath}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function saveProjectAs() {
    const current = latestRef.current;
    if (!current.project) return;
    const suggested = current.project.settings.project_path ?? `${current.project.project_name || "opencomp_project"}.opencomp`;
    const path = window.prompt("Full backend path, or filename for browser download", suggested);
    if (!path) {
      addLog("info", "Save As cancelled.");
      return;
    }
    await saveProject(path);
  }

  async function saveProjectToBrowserFile(filename?: string | null, preferPicker = false) {
    const current = latestRef.current;
    if (!current.project || !current.graph) return;
    const fileName = ensureOpenCompExtension(filename || suggestedProjectFilename(current.project));
    const projectToSave = projectWithCurrentGraph(current.project, current.graph, true);
    const blob = new Blob([JSON.stringify(projectToSave, null, 2)], { type: "application/json" });
    const saved = await saveBlobWithBrowser(blob, fileName, "OpenComp project", "application/json", [".opencomp"], preferPicker);
    if (saved.kind === "cancelled") {
      addLog("info", "Browser save cancelled.");
      return;
    }
    addLog("info", saved.kind === "picker" ? `Saved browser file ${fileName}.` : `Downloaded ${fileName}.`);
  }

  function openProjectFromBrowser() {
    openProjectInputRef.current?.click();
  }

  async function importOpenCompFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as Project;
      const imported = await client.importProject({
        ...parsed,
        settings: { ...parsed.settings, project_path: null },
      });
      await adoptLoadedProject(imported, `Imported ${file.name}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function loadProjectFromPath() {
    const path = window.prompt("Open OpenComp script from backend path", project?.settings.project_path ?? "");
    if (!path) {
      addLog("info", "Open cancelled.");
      return;
    }
    try {
      const loaded = await client.loadProject(path);
      await adoptLoadedProject(loaded, `Loaded ${loaded.settings.project_path ?? path}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function adoptLoadedProject(loaded: Project, message: string) {
    cancelIdlePrefetch();
    const config = await loadColorConfig();
    const settings = {
      ...loaded.settings,
      viewer_display: loaded.settings.viewer_display ?? config.default_display,
      viewer_view: loaded.settings.viewer_view ?? config.default_view,
    };
    if (JSON.stringify(settings) !== JSON.stringify(loaded.settings)) {
      await client.putProjectSettings(settings);
    }
    resetFrontendViewerCache();
    setCachedFrames([]);
    setSelectedMetadata(null);
    setViewerMetadata(null);
    setProject({ ...loaded, settings });
    await loadCacheStatus();
    addLog("info", message);
  }

  async function exportNukeScript() {
    const current = latestRef.current;
    if (!current.project || !current.graph) return;
    try {
      await syncGraphAndSettings();
      const refreshed = latestRef.current;
      if (!refreshed.project || !refreshed.graph) return;
      const projectToExport = projectWithCurrentGraph(refreshed.project, refreshed.graph);
      const suggested = suggestedNukeFilename(projectToExport);
      const path = window.prompt("Full backend path, or filename for browser .nk export", suggested);
      if (!path) {
        addLog("info", "Nuke export cancelled.");
        return;
      }
      const target = ensureNukeExtension(path);
      if (isBackendFilesystemPath(target)) {
        const result = await client.exportNuke(target, projectToExport);
        addLog("info", `${result.message} ${result.path}`);
        return;
      }
      const blob = await client.exportNukeContent(target, projectToExport);
      const saved = await saveBlobWithBrowser(blob, target, "Nuke script", "text/plain", [".nk"], true);
      if (saved.kind === "cancelled") {
        addLog("info", "Nuke export cancelled.");
        return;
      }
      addLog("info", saved.kind === "picker" ? `Exported ${target}.` : `Downloaded ${target}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function clearCache() {
    try {
      cancelIdlePrefetch();
      await client.clearCache();
      resetFrontendViewerCache();
      setCachedFrames([]);
      refreshCacheStatusLabel();
      await loadCacheStatus();
      addLog("info", "Backend and browser viewer caches cleared.");
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function createNewProject() {
    try {
      cancelIdlePrefetch();
      const newProject = await client.newProject();
      const config = await loadColorConfig();
      const settings = {
        ...newProject.settings,
        viewer_display: newProject.settings.viewer_display ?? config.default_display,
        viewer_view: newProject.settings.viewer_view ?? config.default_view,
      };
      await client.putProjectSettings(settings);
      resetFrontendViewerCache();
      setProject({ ...newProject, settings });
      await loadCacheStatus();
      addLog("info", "New reference sequence project loaded.");
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function createScriptTab() {
    try {
      cancelIdlePrefetch();
      const created = await client.createScript(`Comp ${(project?.script_tabs.length ?? 0) + 1}`);
      resetFrontendViewerCache();
      setProject(created);
      addLog("info", `Created script tab ${created.script_tabs.find((tab) => tab.id === created.active_script_id)?.name}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function activateScriptTab(scriptId: string) {
    try {
      cancelIdlePrefetch();
      await syncGraphAndSettings();
      const activated = await client.setActiveScript(scriptId);
      resetFrontendViewerCache();
      setProject(activated);
      await loadCacheStatus();
      addLog("info", `Activated ${activated.script_tabs.find((tab) => tab.id === scriptId)?.name}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function savePreferences() {
    if (!project) return;
    try {
      await client.putPreferences(project.preferences);
      addLog("info", "Preferences saved.");
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  async function runPythonScript() {
    cancelIdlePrefetch();
    setRunningScript(true);
    setScriptOutput("Running...");
    try {
      await syncGraphAndSettings();
      const result = await client.runPython(scriptEditorCode);
      const nextFrame = clampFrame(frame, result.project.settings.frame_start, result.project.settings.frame_end);
      if (result.changed) {
        resetFrontendViewerCache();
      }
      setProject(result.project);
      setFrame(nextFrame);
      await loadCacheStatus();
      setScriptOutput(formatPythonScriptResult(result));
      addLog(
        result.success ? "info" : "error",
        result.success ? "Python script finished." : `Python script failed: ${result.error ?? "unknown error"}.`,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setScriptOutput(message);
      addLog("error", message);
    } finally {
      setRunningScript(false);
    }
  }

  async function renderSelectedWrite() {
    if (!selectedNode || selectedNode.type.toLowerCase() !== "write") return;
    setRendering(true);
    try {
      await syncGraphAndSettings();
      const result = await client.renderFrame(selectedNode.id, frame);
      await loadCacheStatus();
      addLog("info", `Rendered ${result.node_id} at frame ${result.frame}.`);
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    } finally {
      setRendering(false);
    }
  }

  function addNodeFromPalette(type: string) {
    addNode(type, { position: lastGraphPosition });
    addLog("info", `Added ${type} node.`);
    setNodePaletteOpen(false);
    setNodePaletteQuery("");
    setNodePaletteIndex(0);
  }

  async function pickCryptomatteAt(x: number, y: number, mode: "add" | "remove") {
    if (!cryptoLayer) {
      addLog("error", "No Cryptomatte layer is available on the viewed image.");
      return;
    }
    try {
      const pick = await client.cryptomattePick(viewerNodeId, frame, cryptoLayer, Math.round(x), Math.round(y));
      setCryptoSelection((current) => {
        if (mode === "remove") return current.filter((item) => item.id !== pick.id);
        if (current.some((item) => item.id === pick.id)) return current;
        return [...current, pick];
      });
      setCryptoPreviewEnabled(true);
      addLog(
        "info",
        `${mode === "remove" ? "Removed" : "Added"} Cryptomatte ${pick.name ?? pick.id} at ${pick.x},${pick.y}.`,
      );
    } catch (error) {
      addLog("error", error instanceof Error ? error.message : String(error));
    }
  }

  function changeCryptoLayer(layer: string) {
    setCryptoLayer(layer);
    setCryptoSelection([]);
    setCryptoPreviewEnabled(false);
  }

  const settings = project?.settings ?? null;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <Cable size={18} />
          <span>OpenComp Studio</span>
        </div>
        <button onClick={() => void createNewProject()}>
          <Plus size={16} />
          New
        </button>
        <button onClick={() => openProjectFromBrowser()}>
          <Upload size={16} />
          Open File
        </button>
        <input
          ref={openProjectInputRef}
          type="file"
          accept=".opencomp,application/json"
          onChange={(event) => void importOpenCompFile(event)}
          hidden
        />
        <button onClick={() => void loadProjectFromPath()}>
          <FolderOpen size={16} />
          Open Path
        </button>
        <button onClick={() => void saveProject()}>
          <Save size={16} />
          Save
        </button>
        <button onClick={() => void saveProjectAs()}>Save As</button>
        <button onClick={() => void saveProjectToBrowserFile(undefined, true)}>
          <Download size={16} />
          Download
        </button>
        <button onClick={() => void exportNukeScript()}>Export .nk</button>
        <button onClick={() => void refreshViewer()}>
          <Play size={16} />
          View
        </button>
        <button onClick={() => setShowScriptEditor(true)}>Script Editor</button>
        <button onClick={() => setShowPreferences((value) => !value)}>Preferences</button>
        <button onClick={() => setShowViewerPanel((value) => !value)}>{showViewerPanel ? "Hide Viewer" : "Show Viewer"}</button>
        <button onClick={() => setShowGraphPanel((value) => !value)}>{showGraphPanel ? "Hide Graph" : "Show Graph"}</button>
        <div className="script-menu">
          <input
            value={scriptPath}
            onChange={(event) => setScriptPath(event.target.value)}
            placeholder="menu script .py"
          />
          <button onClick={() => addLog("info", scriptPath ? `Registered script: ${scriptPath}` : "No script path set.")}>
            Add Script
          </button>
        </div>
        <div className="health">{backendStatus}</div>
      </header>

      <section className="script-tabs" aria-label="Script tabs">
        {(project?.script_tabs ?? []).map((tab) => (
          <button
            key={tab.id}
            className={tab.id === project?.active_script_id ? "active" : ""}
            onClick={() => void activateScriptTab(tab.id)}
          >
            {tab.name}
          </button>
        ))}
        <button onClick={() => void createScriptTab()}>+ Script</button>
        <span>{activeScriptName ? `active: ${activeScriptName}` : "no active script"}</span>
      </section>

      {showPreferences && project && (
        <div
          className="preferences-backdrop"
          role="presentation"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) setShowPreferences(false);
          }}
        >
          <section className="preferences-dialog" role="dialog" aria-modal="true" aria-label="Preferences">
            <div className="prefs-header">
              <strong>Preferences</strong>
              <div className="panel-actions">
                <button onClick={() => void savePreferences()}>Save Preferences</button>
                <button onClick={() => setShowPreferences(false)} title="Close preferences">
                  <X size={14} />
                </button>
              </div>
            </div>
            <div className="prefs-grid">
              <label>
                Autosave
                <input
                  type="number"
                  value={project.preferences.autosave_seconds}
                  onChange={(event) => updatePreferences({ autosave_seconds: Number(event.target.value) })}
                />
              </label>
              <label>
                Idle Autosave
                <input
                  type="number"
                  value={project.preferences.idle_autosave_seconds}
                  onChange={(event) => updatePreferences({ idle_autosave_seconds: Number(event.target.value) })}
                />
              </label>
              <label>
                Cache MB
                <input
                  type="number"
                  value={project.preferences.cache_memory_limit_mb}
                  onChange={(event) => updatePreferences({ cache_memory_limit_mb: Number(event.target.value) })}
                />
              </label>
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={project.preferences.read_preload_enabled ?? true}
                  onChange={(event) => updatePreferences({ read_preload_enabled: event.target.checked })}
                />
                Preload Reads
              </label>
              <label>
                Read Preload Frames
                <input
                  type="number"
                  min={1}
                  value={project.preferences.read_preload_max_frames ?? 6}
                  onChange={(event) => updatePreferences({ read_preload_max_frames: Number(event.target.value) })}
                />
              </label>
              <label>
                Playback Transfer
                <select
                  value={project.preferences.playback_transfer_mode}
                  onChange={(event) =>
                    updatePreferences({
                      playback_transfer_mode: event.target.value as Project["preferences"]["playback_transfer_mode"],
                    })
                  }
                >
                  <option value="hybrid-preview">GPU Float + Cache</option>
                  <option value="always-float">Always Float</option>
                  <option value="fast-display">Fast Display PNG</option>
                </select>
              </label>
              <label>
                Viewer Precision
                <select
                  value={project.preferences.viewer_transfer_precision ?? "float16"}
                  onChange={(event) =>
                    updatePreferences({
                      viewer_transfer_precision: event.target.value as Project["preferences"]["viewer_transfer_precision"],
                    })
                  }
                >
                  <option value="float32">Float 32</option>
                  <option value="float16">Half Float 16</option>
                  <option value="rgb10a2">10-bit Preview</option>
                  <option value="uint8">8-bit Preview</option>
                </select>
              </label>
              <label>
                Zoom Speed
                <input
                  type="number"
                  step="0.05"
                  value={project.preferences.viewer_zoom_speed}
                  onChange={(event) => updatePreferences({ viewer_zoom_speed: Number(event.target.value) })}
                />
              </label>
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={project.preferences.wheel_zoom_enabled}
                  onChange={(event) => updatePreferences({ wheel_zoom_enabled: event.target.checked })}
                />
                Wheel Zoom
              </label>
              <label className="toggle-label">
                <input
                  type="checkbox"
                  checked={project.preferences.auto_connect_new_nodes}
                  onChange={(event) => updatePreferences({ auto_connect_new_nodes: event.target.checked })}
                />
                Auto Connect
              </label>
              <label>
                Read Hotkey
                <input
                  value={project.preferences.hotkeys.add_read}
                  onChange={(event) =>
                    updatePreferences({ hotkeys: { ...project.preferences.hotkeys, add_read: event.target.value } })
                  }
                />
              </label>
              <label>
                Write Hotkey
                <input
                  value={project.preferences.hotkeys.add_write}
                  onChange={(event) =>
                    updatePreferences({ hotkeys: { ...project.preferences.hotkeys, add_write: event.target.value } })
                  }
                />
              </label>
              <label>
                Merge Hotkey
                <input
                  value={project.preferences.hotkeys.add_merge}
                  onChange={(event) =>
                    updatePreferences({ hotkeys: { ...project.preferences.hotkeys, add_merge: event.target.value } })
                  }
                />
              </label>
              <label>
                Group Hotkey
                <input
                  value={project.preferences.hotkeys.add_group}
                  onChange={(event) =>
                    updatePreferences({ hotkeys: { ...project.preferences.hotkeys, add_group: event.target.value } })
                  }
                />
              </label>
              <label>
                Disable Hotkey
                <input
                  value={project.preferences.hotkeys.toggle_disable ?? "d"}
                  onChange={(event) =>
                    updatePreferences({ hotkeys: { ...project.preferences.hotkeys, toggle_disable: event.target.value } })
                  }
                />
              </label>
              <label>
                Init Scripts
                <input
                  value={project.preferences.custom_init_scripts.join(";")}
                  onChange={(event) =>
                    updatePreferences({
                      custom_init_scripts: event.target.value
                        .split(";")
                        .map((item) => item.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="script_a.py;script_b.py"
                />
              </label>
            </div>
          </section>
        </div>
      )}

      <ScriptEditor
        open={showScriptEditor}
        code={scriptEditorCode}
        output={scriptOutput}
        isRunning={isRunningScript}
        onCodeChange={setScriptEditorCode}
        onRun={() => void runPythonScript()}
        onClose={() => setShowScriptEditor(false)}
      />

      {nodePaletteOpen && (
        <div
          className="node-palette-backdrop"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) {
              setNodePaletteOpen(false);
              setNodePaletteQuery("");
            }
          }}
        >
          <section className="node-palette" role="dialog" aria-label="Node search">
            <div className="node-palette-search">
              <Search size={16} />
              <input
                ref={nodePaletteInputRef}
                value={nodePaletteQuery}
                onChange={(event) => {
                  setNodePaletteQuery(event.target.value);
                  setNodePaletteIndex(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setNodePaletteOpen(false);
                    setNodePaletteQuery("");
                  }
                  if (event.key === "ArrowDown") {
                    event.preventDefault();
                    setNodePaletteIndex((index) => Math.min(index + 1, Math.max(paletteNodes.length - 1, 0)));
                  }
                  if (event.key === "ArrowUp") {
                    event.preventDefault();
                    setNodePaletteIndex((index) => Math.max(index - 1, 0));
                  }
                  if (event.key === "Enter" && paletteNodes[nodePaletteIndex]) {
                    event.preventDefault();
                    addNodeFromPalette(paletteNodes[nodePaletteIndex].type);
                  }
                }}
                placeholder="Search nodes"
              />
            </div>
            <div className="node-palette-list">
              {paletteNodes.map((item, index) => (
                <button
                  key={item.type}
                  className={index === nodePaletteIndex ? "active" : ""}
                  onMouseEnter={() => setNodePaletteIndex(index)}
                  onClick={() => addNodeFromPalette(item.type)}
                >
                  <span>{item.label}</span>
                  <small>{item.category}</small>
                </button>
              ))}
              {paletteNodes.length === 0 && <div className="node-palette-empty">No nodes found</div>}
            </div>
          </section>
        </div>
      )}

      <section className="workspace">
        <aside className="node-shelf" aria-label="Node tools">
          <div className="panel-title">Nodes</div>
          <button className="node-search-button" onClick={() => setNodePaletteOpen(true)}>
            <Search size={14} />
            Search
          </button>
          <div className="node-shelf-groups">
            {groupedNodeCatalog.map(([category, items]) => (
              <section key={category} className="node-shelf-group">
                <h3>{category}</h3>
                {items.map((item) => (
                  <button
                    key={item.type}
                    onClick={() => addNode(item.type, { position: lastGraphPosition })}
                    title={`${item.label}${item.inputs.length ? ` inputs: ${item.inputs.join(", ")}` : ""}`}
                  >
                    <Plus size={13} />
                    {item.label}
                  </button>
                ))}
              </section>
            ))}
          </div>
        </aside>

        <div className="center-stack">
          {showViewerPanel ? (
            <ViewerPanel
              imageUrl={viewerUrl}
              compareImageUrl={compareViewerUrl}
              gpuFrame={viewerGpuFrame}
              gpuCompareFrame={compareViewerGpuFrame}
              ocioGpuShader={ocioGpuShader}
              frame={frame}
              settings={settings}
              preferences={project?.preferences ?? null}
              colorConfig={colorConfig}
              metadata={viewerMetadata}
              selectedChannel={viewerChannel}
              availableChannels={availableViewerChannels}
              viewerGain={viewerGain}
              viewerSaturation={viewerSaturation}
              viewerFstop={viewerFstop}
              compareEnabled={viewerCompareEnabled}
              compareMode={viewerCompareMode}
              compareInputA={viewerCompareInputA}
              compareInputB={viewerCompareInputB}
              wipePosition={wipePosition}
              wipeAngle={wipeAngle}
              viewerTool={viewerTool}
              cryptomatteLayers={cryptomatteLayers}
              cryptoLayer={cryptoLayer}
              cryptoSelection={cryptoSelection}
              cryptoPreviewEnabled={cryptoPreviewEnabled}
              cacheStatus={cacheStatus}
              cachedFrames={cachedFrames}
              isPlaying={isPlaying}
              isRendering={isRendering}
              renderStatus={playbackStatus}
              onTogglePlayback={() => setPlaying(!isPlaying)}
              onFrameChange={handleFrameChange}
              onRefresh={() => void refreshViewer()}
              onDisplayChange={(display) => updateProjectSettings({ viewer_display: display, viewer_view: null }, false)}
              onViewChange={(view) => updateProjectSettings({ viewer_view: view }, false)}
              onProxyEnabledChange={(enabled) => updateProjectSettings({ proxy_enabled: enabled })}
              onProxySizeChange={(size) =>
                updateProjectSettings({
                  viewer_max_width: size.width ?? settings?.viewer_max_width ?? 1280,
                  viewer_max_height: size.height ?? settings?.viewer_max_height ?? 720,
                })
              }
              onChannelChange={setViewerChannel}
              onViewerProcessChange={(process) => {
                if (process.gain !== undefined) setViewerGain(process.gain);
                if (process.saturation !== undefined) setViewerSaturation(process.saturation);
                if (process.fstop !== undefined) setViewerFstop(process.fstop);
              }}
              onCompareEnabledChange={setViewerCompareEnabled}
              onCompareModeChange={setViewerCompareMode}
              onCompareInputAChange={setViewerCompareInputA}
              onCompareInputBChange={setViewerCompareInputB}
              onWipePositionChange={setWipePosition}
              onWipeAngleChange={setWipeAngle}
              onViewerToolChange={setViewerTool}
              onCryptoLayerChange={changeCryptoLayer}
              onCryptoPreviewChange={setCryptoPreviewEnabled}
              onCryptoClear={() => {
                setCryptoSelection([]);
              }}
              onCryptoPick={(x, y, mode) => void pickCryptomatteAt(x, y, mode)}
              onReloadOcio={() => void loadColorConfig(true)}
              onClearCache={() => void clearCache()}
              onGpuMetrics={handleViewerGpuMetrics}
              onClose={() => setShowViewerPanel(false)}
            />
          ) : (
            <button className="restore-panel" onClick={() => setShowViewerPanel(true)}>
              Show Viewer
            </button>
          )}

          {showGraphPanel ? (
            <section className="graph-region">
              <div className="panel-title">
                <span>Node Graph</span>
                <button onClick={() => setShowGraphPanel(false)} title="Close graph">
                  <X size={14} />
                </button>
              </div>
              <div className="graph-canvas-region">
                <CanvasNodeGraph
                  graph={graph}
                  selectedNodeId={selectedNodeId}
                  onSelect={(nodeId) => {
                    selectNode(nodeId);
                    if (nodeId) setInspectorTab("node");
                  }}
                  onMoveNode={moveNode}
                  onConnect={(from, to) => {
                    const result = connectNodes(from, to);
                    if (result.status === "connected") {
                      addLog("info", `Connected ${result.sourceNode} to ${result.targetNode}.${result.targetSocket}.`);
                    } else {
                      addLog("error", result.reason);
                    }
                  }}
                  onPointerWorldPosition={setLastGraphPosition}
                  activeNodeIds={activeRuntimeNodeIds}
                  nodeTimings={nodeTimings}
                />
              </div>
            </section>
          ) : (
            <button className="restore-panel" onClick={() => setShowGraphPanel(true)}>
              Show Graph
            </button>
          )}
        </div>

        <Inspector
          node={selectedNode}
          graph={graph}
          metadata={selectedMetadata}
          settings={settings}
          colorConfig={colorConfig}
          logs={logs}
          metricsStatus={metricsStatus}
          nodeTimings={nodeTimings}
          frontendFrameMs={frontendFrameMs}
          frontendRequestTimings={frontendRequestTimings}
          viewerGpuMetrics={viewerGpuMetrics}
          activeTab={inspectorTab}
          onTabChange={setInspectorTab}
          onChange={updateNode}
          onSettingsChange={updateProjectSettings}
          onRenderWrite={() => void renderSelectedWrite()}
        />
      </section>
    </main>
  );
}

const FRONTEND_VIEWER_CACHE_MIN_BYTES = 128 * 1024 * 1024;
const FRONTEND_VIEWER_CACHE_DEFAULT_BYTES = 10 * 1024 * 1024 * 1024;
const FRONTEND_VIEWER_CACHE_MAX_BYTES = 64 * 1024 * 1024 * 1024;

function viewerFloatCacheKey(
  graph: ProjectGraph | null,
  project: Project,
  renderRevision: number,
  viewerNodeId: string,
  frame: number,
  channel: string,
  viewerInput: string | null,
) {
  const settings = project.settings;
  return JSON.stringify({
    kind: "float-viewer",
    transportPrecision: project.preferences.viewer_transfer_precision ?? "float16",
    transportTiles: true,
    renderRevision,
    scriptId: project.active_script_id,
    viewerNodeId,
    frame,
    input: String(viewerInput ?? activeViewerInput(graph, viewerNodeId) ?? ""),
    channel,
    proxy: proxyCacheToken(project),
    ocioConfig: settings.ocio_config ?? "",
    workingColorspace: settings.working_colorspace,
  });
}

function transferModeForPrecision(
  precision: Project["preferences"]["viewer_transfer_precision"],
): NonNullable<Parameters<typeof client.viewerFloatFrameStream>[6]>["transferMode"] {
  if (precision === "float32") return "float32-rgba";
  if (precision === "rgb10a2") return "rgb10a2";
  if (precision === "uint8") return "uint8-rgba";
  return "float16-rgba";
}

function frontendCacheFrameContext(
  snapshot: { graph: ProjectGraph | null; project: Project | null; viewerNodeId: string; viewerChannel: string },
  renderRevision: number,
) {
  if (!snapshot.project) return null;
  return {
    renderRevision,
    scriptId: snapshot.project.active_script_id,
    viewerNodeId: snapshot.viewerNodeId,
    channel: snapshot.viewerChannel,
    proxy: proxyCacheToken(snapshot.project),
  };
}

function projectWithCurrentGraph(project: Project, graph: ProjectGraph, clearProjectPath = false): Project {
  const activeScriptId = project.active_script_id || project.script_tabs[0]?.id || "main";
  const scriptTabs =
    project.script_tabs.length > 0
      ? project.script_tabs.map((tab) => (tab.id === activeScriptId ? { ...tab, graph } : tab))
      : [{ id: activeScriptId, name: "Comp 1", graph, path: null, startup_scripts: [], kind: "comp" }];
  return {
    ...project,
    active_script_id: activeScriptId,
    graph,
    script_tabs: scriptTabs,
    settings: {
      ...project.settings,
      project_path: clearProjectPath ? null : project.settings.project_path,
    },
  };
}

function isBackendFilesystemPath(path: string) {
  const value = path.trim();
  return /^[A-Za-z]:[\\/]/.test(value) || value.startsWith("\\\\") || value.startsWith("/") || value.includes("/") || value.includes("\\");
}

function suggestedProjectFilename(project: Project) {
  return ensureOpenCompExtension((project.project_name || "opencomp_project").replace(/[^A-Za-z0-9_.-]+/g, "_"));
}

function ensureOpenCompExtension(filename: string) {
  const trimmed = filename.trim() || "opencomp_project.opencomp";
  return trimmed.toLowerCase().endsWith(".opencomp") ? trimmed : `${trimmed}.opencomp`;
}

function suggestedNukeFilename(project: Project) {
  const fromPath = project.settings.project_path?.replace(/\.opencomp$/i, ".nk");
  if (fromPath) return fromPath;
  return ensureNukeExtension((project.project_name || "opencomp_project").replace(/[^A-Za-z0-9_.-]+/g, "_"));
}

function ensureNukeExtension(filename: string) {
  const trimmed = filename.trim() || "opencomp_project.nk";
  return trimmed.toLowerCase().endsWith(".nk") ? trimmed : `${trimmed}.nk`;
}

async function saveBlobWithBrowser(
  blob: Blob,
  filename: string,
  description: string,
  mimeType: string,
  extensions: string[],
  preferPicker: boolean,
): Promise<{ kind: "picker" | "download" | "cancelled" }> {
  const savePicker = (window as WindowWithSavePicker).showSaveFilePicker;
  if (preferPicker && savePicker) {
    try {
      const handle = await savePicker({
        suggestedName: filename,
        types: [{ description, accept: { [mimeType]: extensions } }],
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return { kind: "picker" };
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return { kind: "cancelled" };
      }
    }
  }
  downloadBlob(blob, filename);
  return { kind: "download" };
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function proxyCacheToken(project: Project) {
  const settings = project.settings;
  return settings.proxy_enabled ? `${settings.viewer_max_width}x${settings.viewer_max_height}` : "full";
}

function viewerFrameTransport(frame: FloatViewerFrame) {
  return `webgl-${frame.header.dtype}${frame.header.tile_stream ? "-tiles" : ""}`;
}

function combineFrameMetrics(
  a: FloatViewerFrame["metrics"] | null | undefined,
  b: FloatViewerFrame["metrics"] | null | undefined,
) {
  if (!a) return b ?? null;
  if (!b) return a;
  return {
    ws_wait_ms: Math.max(a.ws_wait_ms, b.ws_wait_ms),
    receive_ms: a.receive_ms + b.receive_ms,
    tile_copy_ms: a.tile_copy_ms + b.tile_copy_ms,
    browser_cache_hit_ms: a.browser_cache_hit_ms + b.browser_cache_hit_ms,
    bytes: a.bytes + b.bytes,
  };
}

function activeViewerInput(graph: ProjectGraph | null, viewerNodeId: string) {
  const viewer = graph?.nodes[viewerNodeId];
  if (!viewer || viewer.type.toLowerCase() !== "viewer") return null;
  return String(viewer.params.active_input ?? "0");
}

function getFrontendViewerFrame(cache: FrontendViewerCacheState, key: string) {
  const entry = cache.entries.get(key);
  if (!entry) {
    cache.misses += 1;
    return null;
  }
  cache.hits += 1;
  cache.entries.delete(key);
  cache.entries.set(key, entry);
  return entry.frame;
}

function storeFrontendViewerFrame(
  cache: FrontendViewerCacheState,
  key: string,
  frame: FloatViewerFrame,
  maxBytes: number,
) {
  const existing = cache.entries.get(key);
  if (existing) {
    cache.bytes -= existing.bytes;
    cache.entries.delete(key);
  }
  const bytes = frame.header.byte_length || frame.pixels.byteLength;
  cache.entries.set(key, { frame, bytes });
  cache.bytes += bytes;

  while (cache.bytes > maxBytes && cache.entries.size > 1) {
    const oldestKey = cache.entries.keys().next().value as string | undefined;
    if (!oldestKey) break;
    const oldest = cache.entries.get(oldestKey);
    cache.entries.delete(oldestKey);
    cache.bytes -= oldest?.bytes ?? 0;
    cache.evictions += 1;
  }
}

function clearFrontendViewerCache(cache: FrontendViewerCacheState) {
  cache.entries.clear();
  cache.bytes = 0;
  cache.hits = 0;
  cache.misses = 0;
  cache.evictions = 0;
}

function frontendViewerCacheLimitBytes(project: Project | null) {
  const preferenceMb = project?.preferences.cache_memory_limit_mb ?? 1024;
  const targetBytes = Math.max(FRONTEND_VIEWER_CACHE_DEFAULT_BYTES, Math.round(preferenceMb) * 1024 * 1024);
  return Math.max(FRONTEND_VIEWER_CACHE_MIN_BYTES, Math.min(FRONTEND_VIEWER_CACHE_MAX_BYTES, targetBytes));
}

function frontendViewerCachedFrames(
  cache: FrontendViewerCacheState,
  context: ReturnType<typeof frontendCacheFrameContext>,
) {
  if (!context) return [];
  const frames = new Set<number>();
  for (const key of cache.entries.keys()) {
    try {
      const parsed = JSON.parse(key) as {
        kind?: string;
        renderRevision?: number;
        scriptId?: string;
        viewerNodeId?: string;
        frame?: number;
        channel?: string;
        proxy?: string;
      };
      if (
        parsed.kind === "float-viewer" &&
        parsed.renderRevision === context.renderRevision &&
        parsed.scriptId === context.scriptId &&
        parsed.viewerNodeId === context.viewerNodeId &&
        parsed.channel === context.channel &&
        parsed.proxy === context.proxy &&
        typeof parsed.frame === "number"
      ) {
        frames.add(parsed.frame);
      }
    } catch {
      continue;
    }
  }
  return [...frames].sort((left, right) => left - right);
}

function viewerReadyCachedFrames(
  cache: FrontendViewerCacheState,
  snapshot: { graph: ProjectGraph | null; project: Project | null; viewerNodeId: string; viewerChannel: string },
  renderRevision: number,
) {
  return frontendViewerCachedFrames(cache, frontendCacheFrameContext(snapshot, renderRevision));
}

function idlePrefetchFrameOrder(anchor: number, frameStart: number, frameEnd: number) {
  const start = Math.min(frameStart, frameEnd);
  const end = Math.max(frameStart, frameEnd);
  const center = clampFrame(anchor, start, end);
  const frames: number[] = [];
  for (let offset = 1; frames.length < end - start; offset += 1) {
    const forward = center + offset;
    const backward = center - offset;
    if (forward <= end) frames.push(forward);
    if (backward >= start) frames.push(backward);
    if (forward > end && backward < start) break;
  }
  return frames;
}

function readPreloadFrameOrder(anchor: number, frameStart: number, frameEnd: number, maxFrames: number) {
  const start = Math.min(frameStart, frameEnd);
  const end = Math.max(frameStart, frameEnd);
  const center = clampFrame(anchor, start, end);
  const frames = [center];
  for (let offset = 1; frames.length < maxFrames; offset += 1) {
    const forward = center + offset;
    const backward = center - offset;
    if (forward <= end) frames.push(forward);
    if (frames.length >= maxFrames) break;
    if (backward >= start) frames.push(backward);
    if (forward > end && backward < start) break;
  }
  return frames.slice(0, maxFrames);
}

function playbackAheadFrameOrder(anchor: number, frameStart: number, frameEnd: number, count: number) {
  const start = Math.min(frameStart, frameEnd);
  const end = Math.max(frameStart, frameEnd);
  const frames: number[] = [];
  let current = clampFrame(anchor, start, end);
  while (frames.length < count && end >= start) {
    current = current >= end ? start : current + 1;
    if (current === anchor && frames.length > 0) break;
    frames.push(current);
    if (end === start) break;
  }
  return frames;
}

function yieldToBrowser(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("The request was aborted.", "AbortError"));
      return;
    }
    const handle = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, 12);
    const onAbort = () => {
      window.clearTimeout(handle);
      signal.removeEventListener("abort", onAbort);
      reject(new DOMException("The request was aborted.", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms));
}

function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable;
}

function matchesHotkey(event: KeyboardEvent, shortcut: string) {
  const parts = shortcut
    .toLowerCase()
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) return false;

  const key = parts[parts.length - 1] === "space" ? " " : parts[parts.length - 1];
  const eventKey = event.key.toLowerCase();
  const wantsCtrl = parts.includes("ctrl") || parts.includes("control");
  const wantsMeta = parts.includes("meta") || parts.includes("cmd") || parts.includes("command");
  const wantsShift = parts.includes("shift");
  const wantsAlt = parts.includes("alt") || parts.includes("option");

  return (
    eventKey === key &&
    event.ctrlKey === wantsCtrl &&
    event.metaKey === wantsMeta &&
    event.shiftKey === wantsShift &&
    event.altKey === wantsAlt
  );
}

function isNodeDisabledParam(params: Record<string, unknown>) {
  const value = params.disabled ?? params.disable ?? false;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    return ["1", "true", "yes", "on", "disabled", "disable"].includes(value.trim().toLowerCase());
  }
  return Boolean(value);
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatFrameTimingLabel(timing: { lastMs: number | null; history: number[] }) {
  if (timing.lastMs === null) return "";
  const last = Math.round(timing.lastMs);
  if (timing.lastMs >= 1000 || timing.history.length < 2) return ` | frame ${last}ms`;
  const recent = timing.history.slice(-30);
  const average = recent.reduce((total, value) => total + value, 0) / recent.length;
  return ` | frame ${last}ms avg${recent.length} ${Math.round(average)}ms`;
}

function clampFrame(frame: number, start: number, end: number) {
  return Math.max(start, Math.min(end, frame));
}

function formatPythonScriptResult(result: PythonScriptResult) {
  const parts = [
    result.success ? "OK" : `ERROR: ${result.error ?? "unknown error"}`,
    result.changed ? "changed: yes" : "changed: no",
  ];
  if (result.stdout.trim()) parts.push(`stdout:\n${result.stdout.trimEnd()}`);
  if (result.stderr.trim()) parts.push(`stderr:\n${result.stderr.trimEnd()}`);
  if (result.traceback) parts.push(result.traceback.trimEnd());
  return parts.join("\n\n");
}
