import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, X } from "lucide-react";

import type {
  ColorConfig,
  CryptomatteLayer,
  CryptomattePick,
  FloatViewerFrame,
  NodeMetadata,
  OcioGpuShader,
  ProjectPreferences,
  ProjectSettings,
} from "../api/client";
import { WebglFloatViewerRenderer, type WebglViewerMetrics } from "./webglFloatViewer";

type ViewerTool = "pan" | "crypto-add" | "crypto-remove" | "point" | "spline";
type ViewerCompareMode = "wipe" | "difference";
type DraftPoint = { x: number; y: number };
type SourceSize = { width: number; height: number };
type PixelSample = {
  x: number;
  y: number;
  rgba: [number, number, number, number];
  swatch: string;
  hsv: { h: number; s: number; v: number };
  luma: number;
};

type Props = {
  imageUrl: string | null;
  compareImageUrl: string | null;
  gpuFrame: FloatViewerFrame | null;
  gpuCompareFrame: FloatViewerFrame | null;
  ocioGpuShader: OcioGpuShader | null;
  frame: number;
  settings: ProjectSettings | null;
  preferences: ProjectPreferences | null;
  colorConfig: ColorConfig | null;
  metadata: NodeMetadata | null;
  selectedChannel: string;
  availableChannels: string[];
  viewerGain: number;
  viewerSaturation: number;
  viewerFstop: number;
  compareEnabled: boolean;
  compareMode: ViewerCompareMode;
  compareInputA: string;
  compareInputB: string;
  wipePosition: number;
  wipeAngle: number;
  viewerTool: ViewerTool;
  cryptomatteLayers: CryptomatteLayer[];
  cryptoLayer: string;
  cryptoSelection: CryptomattePick[];
  cryptoPreviewEnabled: boolean;
  cacheStatus: string;
  cachedFrames: number[];
  isPlaying: boolean;
  isRendering: boolean;
  renderStatus: string | null;
  onTogglePlayback: () => void;
  onFrameChange: (frame: number) => void;
  onRefresh: () => void;
  onDisplayChange: (display: string | null) => void;
  onViewChange: (view: string | null) => void;
  onProxyEnabledChange: (enabled: boolean) => void;
  onProxySizeChange: (size: { width?: number; height?: number }) => void;
  onChannelChange: (channel: string) => void;
  onViewerProcessChange: (process: { gain?: number; saturation?: number; fstop?: number }) => void;
  onCompareEnabledChange: (enabled: boolean) => void;
  onCompareModeChange: (mode: ViewerCompareMode) => void;
  onCompareInputAChange: (input: string) => void;
  onCompareInputBChange: (input: string) => void;
  onWipePositionChange: (position: number) => void;
  onWipeAngleChange: (angle: number) => void;
  onViewerToolChange: (tool: ViewerTool) => void;
  onCryptoLayerChange: (layer: string) => void;
  onCryptoPreviewChange: (enabled: boolean) => void;
  onCryptoClear: () => void;
  onCryptoPick: (x: number, y: number, mode: "add" | "remove") => void;
  onReloadOcio: () => void;
  onClearCache: () => void;
  onGpuMetrics: (metrics: WebglViewerMetrics | null) => void;
  onClose: () => void;
};

type ViewerTransform = {
  x: number;
  y: number;
  scale: number;
};

export function ViewerPanel({
  imageUrl,
  compareImageUrl,
  gpuFrame,
  gpuCompareFrame,
  ocioGpuShader,
  frame,
  settings,
  preferences,
  colorConfig,
  metadata,
  selectedChannel,
  availableChannels,
  viewerGain,
  viewerSaturation,
  viewerFstop,
  compareEnabled,
  compareMode,
  compareInputA,
  compareInputB,
  wipePosition,
  wipeAngle,
  viewerTool,
  cryptomatteLayers,
  cryptoLayer,
  cryptoSelection,
  cryptoPreviewEnabled,
  cacheStatus,
  cachedFrames,
  isPlaying,
  isRendering,
  renderStatus,
  onTogglePlayback,
  onFrameChange,
  onRefresh,
  onDisplayChange,
  onViewChange,
  onProxyEnabledChange,
  onProxySizeChange,
  onChannelChange,
  onViewerProcessChange,
  onCompareEnabledChange,
  onCompareModeChange,
  onCompareInputAChange,
  onCompareInputBChange,
  onWipePositionChange,
  onWipeAngleChange,
  onViewerToolChange,
  onCryptoLayerChange,
  onCryptoPreviewChange,
  onCryptoClear,
  onCryptoPick,
  onReloadOcio,
  onClearCache,
  onGpuMetrics,
  onClose,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const bitmapCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const gpuCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const webglRendererRef = useRef<WebglFloatViewerRenderer | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const compareImageRef = useRef<HTMLImageElement | null>(null);
  const imageFormatRef = useRef<string | null>(null);
  const pendingFitRef = useRef(false);
  const gpuMetricsLabelRef = useRef("");
  const gpuRecoveryRef = useRef<{ key: string; attempts: number }>({ key: "", attempts: 0 });
  const dragRef = useRef<{ x: number; y: number; transform: ViewerTransform } | null>(null);
  const wipeDragRef = useRef(false);
  const [transform, setTransform] = useState<ViewerTransform>({ x: 0, y: 0, scale: 1 });
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [compareImageSize, setCompareImageSize] = useState({ width: 0, height: 0 });
  const [bitmapRevision, setBitmapRevision] = useState(0);
  const [draftPoints, setDraftPoints] = useState<DraftPoint[]>([]);
  const [showBbox, setShowBbox] = useState(true);
  const [gpuStatus, setGpuStatus] = useState("GPU pending");
  const [pixelSample, setPixelSample] = useState<PixelSample | null>(null);
  const [cacheHudCollapsed, setCacheHudCollapsed] = useState(true);
  const [viewerHudCollapsed, setViewerHudCollapsed] = useState(true);
  const pixelAspect =
    gpuFrame?.header.pixel_aspect && gpuFrame.header.pixel_aspect > 0
      ? gpuFrame.header.pixel_aspect
      : metadata?.pixel_aspect && metadata.pixel_aspect > 0
        ? metadata.pixel_aspect
        : 1;
  const frameStart = settings?.frame_start ?? 1001;
  const frameEnd = settings?.frame_end ?? 1010;
  const viewerInputSlots = useMemo(() => Array.from({ length: 10 }, (_, index) => String(index)), []);
  const activeSource = useMemo<SourceSize | null>(() => {
    if (gpuFrame) return { width: gpuFrame.header.width, height: gpuFrame.header.height };
    if (imageSize.width > 0 && imageSize.height > 0) return imageSize;
    return null;
  }, [gpuFrame?.header.height, gpuFrame?.header.width, imageSize.height, imageSize.width]);
  const cacheHudText = useMemo(() => compactCacheStatus(cacheStatus), [cacheStatus]);
  const viewerHudText = useMemo(() => {
    if (!activeSource) return "";
    const parts = [
      gpuFrame ? gpuStatus : "CPU PNG",
      `${Math.round(transform.scale * 100)}%`,
      selectedChannel.toUpperCase(),
      `${activeSource.width}x${activeSource.height}`,
    ];
    if (pixelAspect !== 1) parts.push(`PA ${pixelAspect.toFixed(3)}`);
    parts.push(`F${frame}`);
    if (gpuFrame?.header.partial) {
      parts.push(`tiles ${gpuFrame.header.tiles_received ?? 0}/${gpuFrame.header.tile_count ?? 0}`);
    }
    if (metadata && activeSource.width < metadata.width) {
      parts.push(`Proxy ${activeSource.width}x${activeSource.height}`);
    }
    return parts.join(" | ");
  }, [
    activeSource,
    frame,
    gpuFrame,
    gpuFrame?.header.partial,
    gpuFrame?.header.tile_count,
    gpuFrame?.header.tiles_received,
    gpuStatus,
    metadata,
    pixelAspect,
    selectedChannel,
    transform.scale,
  ]);
  const compactViewerHudText = useMemo(() => {
    const kind = gpuFrame ? (gpuStatus.toLowerCase().includes("fallback") ? "GPU fallback" : "GPU") : "CPU";
    return `${kind} | F${frame} | ${Math.round(transform.scale * 100)}%`;
  }, [frame, gpuFrame, gpuStatus, transform.scale]);

  useEffect(() => {
    setCacheHudCollapsed(true);
    setViewerHudCollapsed(true);
  }, []);

  const fitImage = useCallback(() => {
    const canvas = canvasRef.current;
    const source = activeSource;
    if (!canvas || !source) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) {
      pendingFitRef.current = true;
      return;
    }
    const displayWidth = source.width * pixelAspect;
    const displayHeight = source.height;
    const scale = Math.min(rect.width / displayWidth, rect.height / displayHeight) * 0.94;
    const nextScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
    pendingFitRef.current = false;
    setTransform({
      scale: nextScale,
      x: (rect.width - displayWidth * nextScale) / 2,
      y: (rect.height - displayHeight * nextScale) / 2,
    });
  }, [activeSource, pixelAspect]);

  const setOneToOne = useCallback(() => {
    const canvas = canvasRef.current;
    const source = activeSource;
    if (!canvas || !source) return;
    const rect = canvas.getBoundingClientRect();
    const displayWidth = source.width * pixelAspect;
    setTransform({
      scale: 1,
      x: (rect.width - displayWidth) / 2,
      y: (rect.height - source.height) / 2,
    });
  }, [activeSource, pixelAspect]);

  const zoomBy = useCallback(
    (factor: number) => {
      const canvas = canvasRef.current;
      if (!canvas || !activeSource) return;
      const rect = canvas.getBoundingClientRect();
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      setTransform((current) => {
        const worldX = (centerX - current.x) / current.scale;
        const worldY = (centerY - current.y) / current.scale;
        const scale = clamp(current.scale * factor, 0.05, 16);
        return {
          scale,
          x: centerX - worldX * scale,
          y: centerY - worldY * scale,
        };
      });
    },
    [activeSource],
  );

  const updatePixelSample = useCallback(
    (mouseX: number, mouseY: number) => {
      const source = activeSource;
      if (!source || !gpuFrame) {
        setPixelSample(null);
        return;
      }
      const x = Math.floor((mouseX - transform.x) / transform.scale / pixelAspect);
      const y = Math.floor((mouseY - transform.y) / transform.scale);
      if (x < 0 || y < 0 || x >= source.width || y >= source.height) {
        setPixelSample(null);
        return;
      }
      setPixelSample(sampleFloatPixel(gpuFrame, x, y));
    },
    [activeSource, gpuFrame, pixelAspect, transform],
  );

  const draw = useCallback(() => {
    const overlayCanvas = canvasRef.current;
    if (!overlayCanvas || !activeSource) return;
    const ctx = overlayCanvas.getContext("2d");
    if (!ctx) return;

    const ratio = window.devicePixelRatio || 1;
    const rect = overlayCanvas.getBoundingClientRect();
    resizeCanvas(overlayCanvas, rect, ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const displayWidth = activeSource.width * pixelAspect;
    const displayHeight = activeSource.height;
    const gpuCanvas = gpuCanvasRef.current;
    const bitmapCanvas = bitmapCanvasRef.current;
    if (gpuFrame && gpuCanvas) {
      try {
        resizeCanvas(gpuCanvas, rect, ratio);
        if (!webglRendererRef.current) {
          webglRendererRef.current = new WebglFloatViewerRenderer(gpuCanvas);
        }
        const metrics = webglRendererRef.current.render({
          frameA: gpuFrame,
          frameB: gpuCompareFrame,
          ocioShader: ocioGpuShader,
          viewerProcess: {
            gain: viewerGain,
            saturation: viewerSaturation,
            fstop: viewerFstop,
          },
          compareMode: compareEnabled ? compareMode : "none",
          wipePosition,
          wipeAngle,
          transform,
          canvasCssSize: { width: rect.width, height: rect.height },
          pixelRatio: ratio,
          pixelAspect,
        });
        const nextLabel = metrics.ocio_gpu
          ? `GPU OCIO ${Math.round(metrics.upload_ms)}+${Math.round(metrics.draw_ms)}ms`
          : `GPU fallback ${Math.round(metrics.upload_ms)}+${Math.round(metrics.draw_ms)}ms`;
        gpuRecoveryRef.current = { key: "", attempts: 0 };
        if (gpuMetricsLabelRef.current !== nextLabel) {
          gpuMetricsLabelRef.current = nextLabel;
          setGpuStatus(nextLabel);
          onGpuMetrics(metrics);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        webglRendererRef.current?.dispose();
        webglRendererRef.current = null;
        const recoveryKey = gpuRenderKey(gpuFrame, gpuCompareFrame);
        const currentRecovery =
          gpuRecoveryRef.current.key === recoveryKey ? gpuRecoveryRef.current : { key: recoveryKey, attempts: 0 };
        if (currentRecovery.attempts < 2) {
          gpuRecoveryRef.current = { key: recoveryKey, attempts: currentRecovery.attempts + 1 };
          const retryLabel = `GPU recovering ${gpuRecoveryRef.current.attempts}/2`;
          if (gpuMetricsLabelRef.current !== retryLabel) {
            gpuMetricsLabelRef.current = retryLabel;
            setGpuStatus(retryLabel);
            onGpuMetrics(null);
          }
          window.requestAnimationFrame(() => draw());
          return;
        }
        const nextLabel = `GPU error: ${message}`;
        if (gpuMetricsLabelRef.current !== nextLabel) {
          gpuMetricsLabelRef.current = nextLabel;
          setGpuStatus(nextLabel);
          onGpuMetrics(null);
        }
      }
    } else if (bitmapCanvas && imageRef.current) {
      resizeCanvas(bitmapCanvas, rect, ratio);
      const bitmapCtx = bitmapCanvas.getContext("2d");
      if (bitmapCtx) {
        bitmapCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
        bitmapCtx.clearRect(0, 0, rect.width, rect.height);
        drawCheckerboard(bitmapCtx, rect.width, rect.height);
        bitmapCtx.imageSmoothingEnabled = transform.scale < 4;
        drawViewerImage(bitmapCtx, imageRef.current, transform, displayWidth, displayHeight);
        if (compareEnabled && compareMode === "wipe" && compareImageRef.current) {
          drawWipeImage(
            bitmapCtx,
            compareImageRef.current,
            transform,
            displayWidth,
            displayHeight,
            clamp(wipePosition, 0, 1),
            wipeAngle,
            Math.max(rect.width, rect.height),
          );
        }
      }
      if (gpuMetricsLabelRef.current) {
        gpuMetricsLabelRef.current = "";
        onGpuMetrics(null);
      }
    } else if (bitmapCanvas) {
      resizeCanvas(bitmapCanvas, rect, ratio);
      const bitmapCtx = bitmapCanvas.getContext("2d");
      if (bitmapCtx) {
        bitmapCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
        bitmapCtx.clearRect(0, 0, rect.width, rect.height);
      }
      if (gpuMetricsLabelRef.current) {
        gpuMetricsLabelRef.current = "";
        onGpuMetrics(null);
      }
    }

    ctx.strokeStyle = "#2f2f2f";
    ctx.lineWidth = 1;
    ctx.strokeRect(
      transform.x,
      transform.y,
      displayWidth * transform.scale,
      displayHeight * transform.scale,
    );
    if (gpuFrame && compareEnabled && compareMode === "wipe") {
      drawWipeGuide(ctx, transform, displayWidth, displayHeight, clamp(wipePosition, 0, 1), wipeAngle, Math.max(rect.width, rect.height));
    }
    if (showBbox && metadata) {
      drawBboxOverlay(ctx, metadata, activeSource, transform, pixelAspect);
    }
    drawDraftGeometry(ctx, draftPoints, transform, pixelAspect);
    drawResolutionBadge(ctx, `${activeSource.width}x${activeSource.height}`, transform, displayWidth, displayHeight, rect.width, rect.height);
  }, [
    activeSource,
    compareEnabled,
    compareImageSize,
    compareMode,
    draftPoints,
    gpuCompareFrame,
    gpuFrame,
    metadata,
    ocioGpuShader,
    onGpuMetrics,
    pixelAspect,
    showBbox,
    transform,
    viewerFstop,
    viewerGain,
    viewerSaturation,
    wipeAngle,
    wipePosition,
  ]);

  useEffect(() => {
    if (!imageUrl) {
      imageRef.current = null;
      if (!gpuFrame) {
        imageFormatRef.current = null;
        setImageSize({ width: 0, height: 0 });
      }
      return;
    }
    const image = new Image();
    image.onload = () => {
      const formatKey = `${image.naturalWidth}x${image.naturalHeight}@${pixelAspect}`;
      const shouldFit = imageFormatRef.current !== formatKey;
      imageRef.current = image;
      imageFormatRef.current = formatKey;
      setImageSize({ width: image.naturalWidth, height: image.naturalHeight });
      setBitmapRevision((revision) => revision + 1);
      if (shouldFit) {
        pendingFitRef.current = true;
        window.requestAnimationFrame(() => fitImage());
      }
    };
    image.src = imageUrl;
  }, [fitImage, gpuFrame, imageUrl, pixelAspect]);

  useEffect(() => {
    if (!gpuFrame) return;
    const formatKey = `${gpuFrame.header.width}x${gpuFrame.header.height}@${pixelAspect}`;
    const shouldFit = imageFormatRef.current !== formatKey;
    imageFormatRef.current = formatKey;
    setImageSize((current) =>
      current.width === gpuFrame.header.width && current.height === gpuFrame.header.height
        ? current
        : { width: gpuFrame.header.width, height: gpuFrame.header.height },
    );
    if (shouldFit) {
      pendingFitRef.current = true;
      window.requestAnimationFrame(() => fitImage());
    }
  }, [fitImage, gpuFrame, pixelAspect]);

  useEffect(() => {
    setPixelSample(null);
  }, [gpuFrame, selectedChannel]);

  useEffect(() => {
    if (!compareImageUrl) {
      compareImageRef.current = null;
      setCompareImageSize({ width: 0, height: 0 });
      return;
    }
    const image = new Image();
    image.onload = () => {
      compareImageRef.current = image;
      setCompareImageSize({ width: image.naturalWidth, height: image.naturalHeight });
      setBitmapRevision((revision) => revision + 1);
    };
    image.src = compareImageUrl;
  }, [compareImageUrl]);

  useEffect(() => {
    draw();
  }, [bitmapRevision, draw, imageSize]);

  useEffect(() => {
    const handleResize = () => draw();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [draw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const observer = new ResizeObserver(() => {
      if (pendingFitRef.current && activeSource) {
        fitImage();
        return;
      }
      draw();
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [activeSource, draw, fitImage]);

  useEffect(() => {
    const gpuCanvas = gpuCanvasRef.current;
    if (!gpuCanvas) return undefined;
    const handleContextLost = (event: Event) => {
      event.preventDefault();
      webglRendererRef.current?.dispose();
      webglRendererRef.current = null;
      gpuRecoveryRef.current = { key: "", attempts: 0 };
      gpuMetricsLabelRef.current = "GPU context lost";
      setGpuStatus("GPU context lost");
      onGpuMetrics(null);
    };
    const handleContextRestored = () => {
      webglRendererRef.current = null;
      gpuRecoveryRef.current = { key: "", attempts: 0 };
      gpuMetricsLabelRef.current = "GPU restored";
      setGpuStatus("GPU restored");
      window.requestAnimationFrame(() => draw());
    };
    gpuCanvas.addEventListener("webglcontextlost", handleContextLost);
    gpuCanvas.addEventListener("webglcontextrestored", handleContextRestored);
    return () => {
      gpuCanvas.removeEventListener("webglcontextlost", handleContextLost);
      gpuCanvas.removeEventListener("webglcontextrestored", handleContextRestored);
    };
  }, [draw, onGpuMetrics]);

  useEffect(() => {
    return () => {
      webglRendererRef.current?.dispose();
      webglRendererRef.current = null;
    };
  }, []);

  return (
    <section className="viewer-panel">
      <div className="panel-title">
        <span>Viewer</span>
        <div className="panel-actions">
          <button onClick={fitImage}>Fit</button>
          <button onClick={setOneToOne}>1:1</button>
          <button onClick={() => zoomBy(0.8)} title="Zoom out">
            -
          </button>
          <button onClick={() => zoomBy(1.25)} title="Zoom in">
            +
          </button>
          <button onClick={onClearCache}>Clear Cache</button>
          <button onClick={onReloadOcio}>Reload OCIO</button>
          <button onClick={onRefresh}>Refresh</button>
          <button onClick={onClose} title="Close viewer">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="viewer-controls">
        <label>
          Display
          <select
            value={settings?.viewer_display ?? ""}
            onChange={(event) => onDisplayChange(event.target.value || null)}
          >
            <option value="">Default</option>
            {(colorConfig?.displays ?? []).map((display) => (
              <option key={display} value={display}>
                {display}
              </option>
            ))}
          </select>
        </label>
        <label>
          View
          <select value={settings?.viewer_view ?? ""} onChange={(event) => onViewChange(event.target.value || null)}>
            <option value="">Default</option>
            {(colorConfig?.views ?? []).map((view) => (
              <option key={view} value={view}>
                {view}
              </option>
            ))}
          </select>
        </label>
        <label>
          Channel
          <select value={selectedChannel} onChange={(event) => onChannelChange(event.target.value)}>
            {availableChannels.map((channel) => (
              <option key={channel} value={channel}>
                {channel}
              </option>
            ))}
          </select>
        </label>
        <div className="viewer-toolbox">
          <button onClick={fitImage}>Fit</button>
          <button onClick={setOneToOne}>1:1</button>
          <button onClick={() => zoomBy(0.8)} title="Zoom out">
            -
          </button>
          <button onClick={() => zoomBy(1.25)} title="Zoom in">
            +
          </button>
        </div>
        <div className="viewer-proxy-controls">
          <label className="toggle-label compact-toggle">
            <input
              type="checkbox"
              checked={settings?.proxy_enabled ?? false}
              onChange={(event) => onProxyEnabledChange(event.target.checked)}
            />
            Proxy
          </label>
          <input
            type="number"
            min={1}
            value={settings?.viewer_max_width ?? 1280}
            disabled={!settings?.proxy_enabled}
            title="Proxy max width"
            aria-label="Proxy max width"
            onChange={(event) => onProxySizeChange({ width: Math.max(1, Number(event.target.value) || 1) })}
          />
          <input
            type="number"
            min={1}
            value={settings?.viewer_max_height ?? 720}
            disabled={!settings?.proxy_enabled}
            title="Proxy max height"
            aria-label="Proxy max height"
            onChange={(event) => onProxySizeChange({ height: Math.max(1, Number(event.target.value) || 1) })}
          />
          <button onClick={() => onProxyEnabledChange(false)}>Full Res</button>
        </div>
      </div>
      <div className="viewer-tool-strip">
        <div className="segmented-tools" aria-label="Viewer tools">
          <button className={viewerTool === "pan" ? "active" : ""} onClick={() => onViewerToolChange("pan")}>
            Pan
          </button>
          <button
            className={viewerTool === "crypto-add" ? "active" : ""}
            disabled={cryptomatteLayers.length === 0}
            onClick={() => onViewerToolChange("crypto-add")}
          >
            Pick +
          </button>
          <button
            className={viewerTool === "crypto-remove" ? "active" : ""}
            disabled={cryptomatteLayers.length === 0}
            onClick={() => onViewerToolChange("crypto-remove")}
          >
            Pick -
          </button>
          <button className={viewerTool === "point" ? "active" : ""} onClick={() => onViewerToolChange("point")}>
            Point
          </button>
          <button className={viewerTool === "spline" ? "active" : ""} onClick={() => onViewerToolChange("spline")}>
            Spline
          </button>
          <button className={showBbox ? "active" : ""} onClick={() => setShowBbox((value) => !value)}>
            BBox
          </button>
        </div>
        <div className="viewer-process-controls">
          <label>
            f/
            <input
              type="number"
              step="0.25"
              value={viewerFstop}
              onChange={(event) => onViewerProcessChange({ fstop: Number(event.target.value) || 0 })}
            />
          </label>
          <label>
            Gain
            <input
              type="number"
              min="0"
              step="0.05"
              value={viewerGain}
              onChange={(event) => onViewerProcessChange({ gain: Math.max(0, Number(event.target.value) || 0) })}
            />
          </label>
          <label>
            Sat
            <input
              type="number"
              min="0"
              step="0.05"
              value={viewerSaturation}
              onChange={(event) => onViewerProcessChange({ saturation: Math.max(0, Number(event.target.value) || 0) })}
            />
          </label>
          <button onClick={() => onViewerProcessChange({ fstop: 0, gain: 1, saturation: 1 })}>Reset</button>
        </div>
        <div className="viewer-compare-controls">
          <label className="toggle-label compact-toggle">
            <input
              type="checkbox"
              checked={compareEnabled}
              onChange={(event) => onCompareEnabledChange(event.target.checked)}
            />
            Compare
          </label>
          <select value={compareMode} onChange={(event) => onCompareModeChange(event.target.value as ViewerCompareMode)}>
            <option value="wipe">wipe</option>
            <option value="difference">difference</option>
          </select>
          <label>
            A
            <select value={compareInputA} onChange={(event) => onCompareInputAChange(event.target.value)}>
              {viewerInputSlots.map((slot) => (
                <option key={slot} value={slot}>
                  {slot}
                </option>
              ))}
            </select>
          </label>
          <label>
            B
            <select value={compareInputB} onChange={(event) => onCompareInputBChange(event.target.value)}>
              {viewerInputSlots.map((slot) => (
                <option key={slot} value={slot}>
                  {slot}
                </option>
              ))}
            </select>
          </label>
          {compareMode === "wipe" && (
            <>
              <input
                type="range"
                min="0"
                max="1"
                step="0.001"
                value={wipePosition}
                onChange={(event) => onWipePositionChange(Number(event.target.value))}
                aria-label="Wipe position"
              />
              <input
                type="number"
                min="-180"
                max="180"
                step="1"
                value={wipeAngle}
                onChange={(event) => onWipeAngleChange(clamp(Number(event.target.value) || 0, -180, 180))}
                aria-label="Wipe angle"
              />
            </>
          )}
        </div>
        {cryptomatteLayers.length > 0 && (
          <div className="crypto-controls">
            <select value={cryptoLayer} onChange={(event) => onCryptoLayerChange(event.target.value)}>
              {cryptomatteLayers.map((layer) => (
                <option key={layer.key} value={layer.name}>
                  {layer.name} ({layer.manifest_count})
                </option>
              ))}
            </select>
            <label className="toggle-label compact-toggle">
              <input
                type="checkbox"
                checked={cryptoPreviewEnabled}
                onChange={(event) => onCryptoPreviewChange(event.target.checked)}
              />
              ID Preview
            </label>
            <button onClick={onCryptoClear}>Clear</button>
          </div>
        )}
      </div>
      {cryptoSelection.length > 0 && (
        <div className="crypto-selection">
          {cryptoSelection.map((selection) => (
            <span key={selection.id} title={selection.id}>
              {selection.name ?? selection.id}
            </span>
          ))}
        </div>
      )}
      <div className="viewer-frame">
        <canvas
          ref={bitmapCanvasRef}
          className="viewer-canvas viewer-base-canvas"
          hidden={Boolean(gpuFrame)}
          aria-hidden="true"
        />
        <canvas
          ref={gpuCanvasRef}
          className="viewer-canvas viewer-base-canvas"
          hidden={!gpuFrame}
          aria-hidden="true"
        />
        <canvas
          ref={canvasRef}
          className="viewer-canvas viewer-overlay-canvas"
          onPointerDown={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const mouseX = event.clientX - rect.left;
            const mouseY = event.clientY - rect.top;
            updatePixelSample(mouseX, mouseY);
            if (
              compareEnabled &&
              compareMode === "wipe" &&
              viewerTool === "pan" &&
              activeSource &&
              isNearWipeLine(mouseX, mouseY, transform, activeSource, pixelAspect, wipePosition, wipeAngle)
            ) {
              wipeDragRef.current = true;
              onWipePositionChange(wipePositionFromPointer(mouseX, transform, activeSource, pixelAspect));
              event.currentTarget.setPointerCapture(event.pointerId);
              return;
            }
            if (viewerTool !== "pan") {
              const point = screenToImage(mouseX, mouseY, transform, pixelAspect, imageSize);
              if (!point) return;
              if (viewerTool === "crypto-add" || viewerTool === "crypto-remove") {
                onCryptoPick(point.x, point.y, viewerTool === "crypto-remove" ? "remove" : "add");
                return;
              }
              setDraftPoints((current) => (viewerTool === "point" ? [...current, point] : [...current, point]));
              return;
            }
            dragRef.current = {
              x: mouseX,
              y: mouseY,
              transform,
            };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const mouseX = event.clientX - rect.left;
            const mouseY = event.clientY - rect.top;
            updatePixelSample(mouseX, mouseY);
            if (wipeDragRef.current && activeSource) {
              onWipePositionChange(wipePositionFromPointer(mouseX, transform, activeSource, pixelAspect));
              return;
            }
            if (!dragRef.current) return;
            setTransform({
              ...dragRef.current.transform,
              x: dragRef.current.transform.x + mouseX - dragRef.current.x,
              y: dragRef.current.transform.y + mouseY - dragRef.current.y,
            });
          }}
          onPointerLeave={() => {
            setPixelSample(null);
          }}
          onPointerUp={(event) => {
            dragRef.current = null;
            wipeDragRef.current = false;
            if (event.currentTarget.hasPointerCapture(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId);
            }
          }}
          onDoubleClick={fitImage}
          onWheel={(event) => {
            event.preventDefault();
            if (preferences && !preferences.wheel_zoom_enabled) return;
            const rect = event.currentTarget.getBoundingClientRect();
            const mouseX = event.clientX - rect.left;
            const mouseY = event.clientY - rect.top;
            updatePixelSample(mouseX, mouseY);
            const worldX = (mouseX - transform.x) / transform.scale;
            const worldY = (mouseY - transform.y) / transform.scale;
            const zoomStep = clamp(preferences?.viewer_zoom_speed ?? 1.1, 1.01, 2);
            const scale = clamp(transform.scale * (event.deltaY > 0 ? 1 / zoomStep : zoomStep), 0.05, 16);
            setTransform({
              scale,
              x: mouseX - worldX * scale,
              y: mouseY - worldY * scale,
            });
          }}
        />
        <button
          type="button"
          className={cacheHudCollapsed ? "cache-pill viewer-cache-hud viewer-hud-toggle collapsed" : "cache-pill viewer-cache-hud viewer-hud-toggle"}
          title={cacheStatus}
          aria-label={cacheHudCollapsed ? "Expand cache status" : "Collapse cache status"}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            setCacheHudCollapsed((value) => !value);
          }}
        >
          {cacheHudCollapsed ? cacheHudText : cacheStatus}
        </button>
        {!imageUrl && !gpuFrame && <div className="empty-viewer">No frame</div>}
        {activeSource && (
          <button
            type="button"
            className={viewerHudCollapsed ? "viewer-hud viewer-hud-toggle collapsed" : "viewer-hud viewer-hud-toggle"}
            title={viewerHudText}
            aria-label={viewerHudCollapsed ? "Expand viewer status" : "Collapse viewer status"}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              setViewerHudCollapsed((value) => !value);
            }}
          >
            {viewerHudCollapsed ? compactViewerHudText : viewerHudText}
          </button>
        )}
        {pixelSample && <PixelReadout sample={pixelSample} />}
      </div>
      <div className="viewer-transport">
        <button className="play-button" onClick={onTogglePlayback}>
          {isPlaying ? <Pause size={16} /> : <Play size={16} />}
          {isPlaying ? "Pause" : "Play"}
        </button>
        <label>
          Frame
          <input
            type="number"
            value={frame}
            onChange={(event) => onFrameChange(clamp(Math.round(Number(event.target.value)), frameStart, frameEnd))}
          />
        </label>
        <FrameRulerSlider
          frame={frame}
          frameStart={frameStart}
          frameEnd={frameEnd}
          cachedFrames={cachedFrames}
          onFrameChange={onFrameChange}
        />
        <div className={isRendering || renderStatus ? "render-state active" : "render-state"}>
          {renderStatus ?? (isRendering ? "rendering" : "ready")}
        </div>
      </div>
    </section>
  );
}

type FrameRulerSliderProps = {
  frame: number;
  frameStart: number;
  frameEnd: number;
  cachedFrames: number[];
  onFrameChange: (frame: number) => void;
};

function FrameRulerSlider({ frame, frameStart, frameEnd, cachedFrames, onFrameChange }: FrameRulerSliderProps) {
  const rulerRef = useRef<HTMLDivElement | null>(null);
  const lastSentFrameRef = useRef(frame);
  const start = Math.min(frameStart, frameEnd);
  const end = Math.max(frameStart, frameEnd);
  const range = Math.max(1, end - start);
  const currentFrame = clamp(Math.round(frame), start, end);
  const currentLeft = ((currentFrame - start) / range) * 100;

  useEffect(() => {
    lastSentFrameRef.current = currentFrame;
  }, [currentFrame]);

  const tickStep = useMemo(() => {
    const count = end - start + 1;
    if (count <= 90) return 1;
    if (count <= 180) return 2;
    if (count <= 360) return 5;
    return Math.ceil(count / 90);
  }, [end, start]);

  const ticks = useMemo(() => {
    const values: number[] = [];
    for (let value = start; value <= end; value += tickStep) {
      values.push(value);
    }
    if (values[values.length - 1] !== end) values.push(end);
    return values;
  }, [end, start, tickStep]);

  const cachedSpans = useMemo(() => {
    const filtered = [...new Set(cachedFrames)]
      .filter((value) => value >= start && value <= end)
      .sort((a, b) => a - b);
    const spans: Array<{ start: number; end: number }> = [];
    for (const value of filtered) {
      const current = spans[spans.length - 1];
      if (current && value <= current.end + 1) current.end = value;
      else spans.push({ start: value, end: value });
    }
    return spans;
  }, [cachedFrames, end, start]);

  const emitFrame = useCallback(
    (nextFrame: number) => {
      const normalized = clamp(Math.round(nextFrame), start, end);
      if (lastSentFrameRef.current === normalized) return;
      lastSentFrameRef.current = normalized;
      onFrameChange(normalized);
    },
    [end, onFrameChange, start],
  );

  const frameFromClientX = useCallback(
    (clientX: number) => {
      const ruler = rulerRef.current;
      if (!ruler) return currentFrame;
      const rect = ruler.getBoundingClientRect();
      const ratio = clamp((clientX - rect.left) / Math.max(rect.width, 1), 0, 1);
      return start + ratio * (end - start);
    },
    [currentFrame, end, start],
  );

  return (
    <div className="frame-ruler-wrap">
      <div
        ref={rulerRef}
        className="frame-ruler"
        role="slider"
        tabIndex={0}
        aria-valuemin={start}
        aria-valuemax={end}
        aria-valuenow={currentFrame}
        aria-label="Frame"
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          emitFrame(frameFromClientX(event.clientX));
        }}
        onPointerMove={(event) => {
          if (event.buttons !== 1) return;
          emitFrame(frameFromClientX(event.clientX));
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") {
            event.preventDefault();
            emitFrame(currentFrame - 1);
          } else if (event.key === "ArrowRight") {
            event.preventDefault();
            emitFrame(currentFrame + 1);
          } else if (event.key === "Home") {
            event.preventDefault();
            emitFrame(start);
          } else if (event.key === "End") {
            event.preventDefault();
            emitFrame(end);
          }
        }}
      >
        <div className="frame-ruler-line" />
        <div className="frame-cache-lane">
          {cachedSpans.map((span) => {
            const left = ((span.start - start) / range) * 100;
            const right = ((span.end - start) / range) * 100;
            return (
              <span
                key={`${span.start}-${span.end}`}
                className="frame-cache-span"
                style={{ left: `${left}%`, width: `${Math.max(1.4, right - left)}%` }}
              />
            );
          })}
        </div>
        <div className="frame-ticks">
          {ticks.map((value) => {
            const left = ((value - start) / range) * 100;
            const major = value === start || value === end || (value - start) % (tickStep * 5) === 0;
            return (
              <span
                key={value}
                className={major ? "frame-tick major" : "frame-tick"}
                style={{ left: `${left}%` }}
              />
            );
          })}
        </div>
        <div className="frame-current-label" style={{ left: `${currentLeft}%` }}>
          {currentFrame}
        </div>
        <div className="frame-handle" style={{ left: `${currentLeft}%` }}>
          <span />
        </div>
        <div className="frame-bound start">{start}</div>
        <div className="frame-bound end">{end}</div>
      </div>
    </div>
  );
}

function compactCacheStatus(status: string) {
  const match = status.match(/^cache:\s*([^|]+)/i);
  return match ? `cache ${match[1].trim()}` : "cache";
}

function drawCheckerboard(ctx: CanvasRenderingContext2D, width: number, height: number) {
  const size = 16;
  for (let y = 0; y < height; y += size) {
    for (let x = 0; x < width; x += size) {
      const light = (x / size + y / size) % 2 === 0;
      ctx.fillStyle = light ? "#161616" : "#101010";
      ctx.fillRect(x, y, size, size);
    }
  }
}

function drawResolutionBadge(
  ctx: CanvasRenderingContext2D,
  label: string,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  canvasWidth: number,
  canvasHeight: number,
) {
  const imageRight = transform.x + displayWidth * transform.scale;
  const imageBottom = transform.y + displayHeight * transform.scale;
  if (imageRight < 0 || imageBottom < 0 || transform.x > canvasWidth || transform.y > canvasHeight) return;

  ctx.save();
  ctx.font = '10px ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace';
  ctx.textBaseline = "middle";
  const width = Math.ceil(ctx.measureText(label).width) + 9;
  const height = 14;
  const outsideX = imageRight + 4;
  const insideX = imageRight - width - 4;
  const x = outsideX + width <= canvasWidth - 4 ? outsideX : insideX;
  const y = imageBottom + 2 + height <= canvasHeight - 4 ? imageBottom + 2 : imageBottom - height - 4;
  const clampedX = clamp(x, 4, Math.max(4, canvasWidth - width - 4));
  const clampedY = clamp(y, 4, Math.max(4, canvasHeight - height - 4));

  ctx.fillStyle = "rgba(12, 12, 12, 0.72)";
  ctx.strokeStyle = "#8a8a8a";
  ctx.lineWidth = 1;
  ctx.fillRect(clampedX, clampedY, width, height);
  ctx.strokeRect(clampedX + 0.5, clampedY + 0.5, width - 1, height - 1);
  ctx.fillStyle = "#d6d6d6";
  ctx.fillText(label, clampedX + 5, clampedY + height / 2 + 0.5);
  ctx.restore();
}

function resizeCanvas(canvas: HTMLCanvasElement, rect: DOMRect, ratio: number) {
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
}

function gpuRenderKey(frameA: FloatViewerFrame, frameB: FloatViewerFrame | null) {
  const a = frameA.header;
  const b = frameB?.header;
  return [
    a.node_id,
    a.viewer_input ?? "",
    a.frame,
    a.channel,
    a.dtype,
    `${a.width}x${a.height}`,
    a.byte_length,
    b?.node_id ?? "",
    b?.viewer_input ?? "",
    b?.frame ?? "",
    b?.channel ?? "",
    b?.dtype ?? "",
    b ? `${b.width}x${b.height}` : "",
    b?.byte_length ?? "",
  ].join("|");
}

function PixelReadout({ sample }: { sample: PixelSample }) {
  return (
    <div className="pixel-readout">
      <span className="pixel-coord">
        x={sample.x} y={sample.y}
      </span>
      <span className="pixel-channel red">{formatPixelValue(sample.rgba[0])}</span>
      <span className="pixel-channel green">{formatPixelValue(sample.rgba[1])}</span>
      <span className="pixel-channel blue">{formatPixelValue(sample.rgba[2])}</span>
      <span className="pixel-channel alpha">{formatPixelValue(sample.rgba[3])}</span>
      <span className="pixel-swatch" style={{ background: sample.swatch }} />
      <span>H:{Math.round(sample.hsv.h)}</span>
      <span>S:{formatCompactValue(sample.hsv.s)}</span>
      <span>V:{formatCompactValue(sample.hsv.v)}</span>
      <span>L:{formatPixelValue(sample.luma)}</span>
    </div>
  );
}

function sampleFloatPixel(frame: FloatViewerFrame, x: number, y: number): PixelSample | null {
  const { width, height, source_width: sourceWidth, source_height: sourceHeight } = frame.header;
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  const rgba = sampleFrameRgba(frame, y * width + x);
  if (!rgba) return null;
  const [r, g, b, a] = rgba.map(finitePixelValue) as [number, number, number, number];
  return {
    x: mapProxyCoordinateToSource(x, width, sourceWidth),
    y: mapProxyCoordinateToSource(y, height, sourceHeight),
    rgba: [r, g, b, a],
    swatch: linearRgbToCss([r, g, b]),
    hsv: rgbToHsv(r, g, b),
    luma: r * 0.2126 + g * 0.7152 + b * 0.0722,
  };
}

function sampleFrameRgba(frame: FloatViewerFrame, pixelIndex: number): [number, number, number, number] | null {
  if (frame.header.dtype === "rgb10a2") {
    if (pixelIndex >= frame.pixels.length) return null;
    const packed = frame.pixels[pixelIndex] ?? 0;
    return [
      (packed & 0x3ff) / 1023,
      ((packed >>> 10) & 0x3ff) / 1023,
      ((packed >>> 20) & 0x3ff) / 1023,
      ((packed >>> 30) & 0x03) / 3,
    ];
  }
  const index = pixelIndex * 4;
  if (index + 3 >= frame.pixels.length) return null;
  if (frame.header.dtype === "float16") {
    return [
      halfToFloat(frame.pixels[index]),
      halfToFloat(frame.pixels[index + 1]),
      halfToFloat(frame.pixels[index + 2]),
      halfToFloat(frame.pixels[index + 3]),
    ];
  }
  if (frame.header.dtype === "uint8") {
    return [
      frame.pixels[index] / 255,
      frame.pixels[index + 1] / 255,
      frame.pixels[index + 2] / 255,
      frame.pixels[index + 3] / 255,
    ];
  }
  return [frame.pixels[index], frame.pixels[index + 1], frame.pixels[index + 2], frame.pixels[index + 3]];
}

function halfToFloat(value: number) {
  const sign = (value & 0x8000) ? -1 : 1;
  const exponent = (value >> 10) & 0x1f;
  const fraction = value & 0x03ff;
  if (exponent === 0) {
    return sign * 2 ** -14 * (fraction / 1024);
  }
  if (exponent === 0x1f) {
    return fraction ? 0 : sign * Infinity;
  }
  return sign * 2 ** (exponent - 15) * (1 + fraction / 1024);
}

function mapProxyCoordinateToSource(value: number, proxySize: number, sourceSize: number) {
  const targetSize = Math.max(1, sourceSize || proxySize);
  if (proxySize <= 1) return 0;
  return clamp(Math.round(((value + 0.5) * targetSize) / proxySize - 0.5), 0, targetSize - 1);
}

function finitePixelValue(value: number) {
  return Number.isFinite(value) ? value : 0;
}

function linearRgbToCss(rgb: [number, number, number]) {
  const channels = rgb.map((value) => Math.round(linearToSrgb(clamp(value, 0, 1)) * 255));
  return `rgb(${channels[0]} ${channels[1]} ${channels[2]})`;
}

function linearToSrgb(value: number) {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055;
}

function rgbToHsv(r: number, g: number, b: number) {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let h = 0;
  if (delta > 1e-8) {
    if (max === r) h = ((g - b) / delta) % 6;
    else if (max === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return {
    h,
    s: Math.abs(max) > 1e-8 ? delta / Math.abs(max) : 0,
    v: max,
  };
}

function formatPixelValue(value: number) {
  return Number.isFinite(value) ? value.toFixed(5) : "0.00000";
}

function formatCompactValue(value: number) {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function drawViewerImage(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
) {
  ctx.drawImage(image, transform.x, transform.y, displayWidth * transform.scale, displayHeight * transform.scale);
}

function drawWipeImage(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  position: number,
  angle: number,
  canvasExtent: number,
) {
  const width = displayWidth * transform.scale;
  const height = displayHeight * transform.scale;
  const pointX = transform.x + width * position;
  const pointY = transform.y + height * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const normalX = Math.cos(angleRad);
  const normalY = Math.sin(angleRad);
  const tangentX = -normalY;
  const tangentY = normalX;
  const far = Math.max(width, height, canvasExtent) * 3;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.lineTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX - tangentX * far + normalX * far, pointY - tangentY * far + normalY * far);
  ctx.lineTo(pointX + tangentX * far + normalX * far, pointY + tangentY * far + normalY * far);
  ctx.closePath();
  ctx.clip();
  drawViewerImage(ctx, image, transform, displayWidth, displayHeight);
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = "#f29b18";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.stroke();
  ctx.restore();
}

function drawWipeGuide(
  ctx: CanvasRenderingContext2D,
  transform: ViewerTransform,
  displayWidth: number,
  displayHeight: number,
  position: number,
  angle: number,
  canvasExtent: number,
) {
  const width = displayWidth * transform.scale;
  const height = displayHeight * transform.scale;
  const pointX = transform.x + width * position;
  const pointY = transform.y + height * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const tangentX = -Math.sin(angleRad);
  const tangentY = Math.cos(angleRad);
  const far = Math.max(width, height, canvasExtent) * 3;
  ctx.save();
  ctx.strokeStyle = "#f29b18";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(pointX - tangentX * far, pointY - tangentY * far);
  ctx.lineTo(pointX + tangentX * far, pointY + tangentY * far);
  ctx.stroke();
  ctx.restore();
}

function drawBboxOverlay(
  ctx: CanvasRenderingContext2D,
  metadata: NodeMetadata,
  source: SourceSize,
  transform: ViewerTransform,
  pixelAspect: number,
) {
  const formatBox = metadata.format_bbox ?? { x: 0, y: 0, width: metadata.width, height: metadata.height };
  const dataWindow = metadata.data_window ?? formatBox;
  drawSourceBox(ctx, formatBox, metadata, source, transform, pixelAspect, "#6f6f6f", []);
  if (!sameBox(formatBox, dataWindow)) {
    drawSourceBox(ctx, dataWindow, metadata, source, transform, pixelAspect, "#f29b18", [5, 4]);
    drawBboxStrip(ctx, formatBox, dataWindow, metadata, source, transform, pixelAspect);
  }
}

function drawSourceBox(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; width: number; height: number },
  metadata: NodeMetadata,
  source: SourceSize,
  transform: ViewerTransform,
  pixelAspect: number,
  color: string,
  dash: number[],
) {
  const sx = source.width / Math.max(metadata.width, 1);
  const sy = source.height / Math.max(metadata.height, 1);
  const x = transform.x + box.x * sx * pixelAspect * transform.scale;
  const y = transform.y + box.y * sy * transform.scale;
  const width = box.width * sx * pixelAspect * transform.scale;
  const height = box.height * sy * transform.scale;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash(dash);
  ctx.strokeRect(x, y, width, height);
  ctx.restore();
}

function drawBboxStrip(
  ctx: CanvasRenderingContext2D,
  formatBox: { x: number; y: number; width: number; height: number },
  dataWindow: { x: number; y: number; width: number; height: number },
  metadata: NodeMetadata,
  source: SourceSize,
  transform: ViewerTransform,
  pixelAspect: number,
) {
  const sx = source.width / Math.max(metadata.width, 1);
  const sy = source.height / Math.max(metadata.height, 1);
  const stripY = transform.y + source.height * transform.scale + 7;
  const formatX0 = transform.x + formatBox.x * sx * pixelAspect * transform.scale;
  const formatX1 = formatX0 + formatBox.width * sx * pixelAspect * transform.scale;
  const dataX0 = transform.x + dataWindow.x * sx * pixelAspect * transform.scale;
  const dataX1 = dataX0 + dataWindow.width * sx * pixelAspect * transform.scale;
  const formatY0 = transform.y + formatBox.y * sy * transform.scale;
  const formatY1 = formatY0 + formatBox.height * sy * transform.scale;
  const dataY0 = transform.y + dataWindow.y * sy * transform.scale;
  const dataY1 = dataY0 + dataWindow.height * sy * transform.scale;

  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = "#6f6f6f";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(formatX0, stripY);
  ctx.lineTo(formatX1, stripY);
  ctx.stroke();
  ctx.strokeStyle = "#f29b18";
  ctx.beginPath();
  ctx.moveTo(dataX0, stripY + 4);
  ctx.lineTo(dataX1, stripY + 4);
  ctx.stroke();
  ctx.strokeStyle = "#f29b18";
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(transform.x - 8, dataY0);
  ctx.lineTo(transform.x - 8, dataY1);
  ctx.stroke();
  ctx.strokeStyle = "#6f6f6f";
  ctx.beginPath();
  ctx.moveTo(transform.x - 12, formatY0);
  ctx.lineTo(transform.x - 12, formatY1);
  ctx.stroke();
  ctx.restore();
}

function sameBox(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
) {
  return a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}

function screenToImage(
  mouseX: number,
  mouseY: number,
  transform: ViewerTransform,
  pixelAspect: number,
  imageSize: { width: number; height: number },
): DraftPoint | null {
  if (imageSize.width <= 0 || imageSize.height <= 0) return null;
  const x = (mouseX - transform.x) / transform.scale / pixelAspect;
  const y = (mouseY - transform.y) / transform.scale;
  if (x < 0 || y < 0 || x >= imageSize.width || y >= imageSize.height) return null;
  return { x, y };
}

function isNearWipeLine(
  mouseX: number,
  mouseY: number,
  transform: ViewerTransform,
  source: SourceSize,
  pixelAspect: number,
  position: number,
  angle: number,
) {
  const width = source.width * pixelAspect * transform.scale;
  const height = source.height * transform.scale;
  const pointX = transform.x + width * clamp(position, 0, 1);
  const pointY = transform.y + height * 0.5;
  const angleRad = (angle * Math.PI) / 180;
  const normalX = Math.cos(angleRad);
  const normalY = Math.sin(angleRad);
  const distance = Math.abs((mouseX - pointX) * normalX + (mouseY - pointY) * normalY);
  return distance <= 10;
}

function wipePositionFromPointer(mouseX: number, transform: ViewerTransform, source: SourceSize, pixelAspect: number) {
  const width = source.width * pixelAspect * transform.scale;
  return clamp((mouseX - transform.x) / Math.max(width, 1), 0, 1);
}

function drawDraftGeometry(
  ctx: CanvasRenderingContext2D,
  points: DraftPoint[],
  transform: ViewerTransform,
  pixelAspect: number,
) {
  if (points.length === 0) return;
  ctx.save();
  ctx.strokeStyle = "#77c8b4";
  ctx.fillStyle = "#ffd15c";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = transform.x + point.x * pixelAspect * transform.scale;
    const y = transform.y + point.y * transform.scale;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  if (points.length > 1) ctx.stroke();
  for (const point of points) {
    const x = transform.x + point.x * pixelAspect * transform.scale;
    const y = transform.y + point.y * transform.scale;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
