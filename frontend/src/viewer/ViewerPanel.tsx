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
import {
  viewerFrameRenderKey,
  viewerFrameDimensions,
  viewerFrameFormatKey,
  viewerFramePixelAspect,
  viewerFrameTileProgress,
} from "./viewerFrame";
import {
  nodeMetadataDataWindow,
  nodeMetadataFormatBox,
  nodeMetadataPixelAspect,
  nodeMetadataScale,
  nodeMetadataUsesProxy,
  type SourceSize,
} from "./viewerMetadata";
import {
  drawCheckerboard,
  drawDraftGeometry,
  drawResolutionBadge,
  drawViewerImage,
  drawWipeGuide,
  drawWipeImage,
  resizeCanvas,
} from "./viewerCanvas";
import {
  clamp,
  compactCacheStatus,
  displaySize,
  DraftPoint,
  fitViewerTransform,
  isNearWipeLine,
  normalizeViewerRoi,
  oneToOneViewerTransform,
  RoiDragMode,
  roiHitTest,
  screenToImage,
  updateViewerRoiFromDrag,
  ViewerRoi,
  ViewerTransform,
  wipePositionFromPointer,
  zoomViewerTransform,
} from "./viewerGeometry";
import {
  formatCompactValue,
  formatPixelValue,
  PixelSample,
  sampleFloatPixel,
} from "./viewerPixels";
import { WebglFloatViewerRenderer, type WebglViewerMetrics } from "./webglFloatViewer";

type ViewerTool = "pan" | "crypto-add" | "crypto-remove" | "point" | "spline" | "roi";
type ViewerCompareMode = "wipe" | "difference";
type ViewerProfilePreset = "speed" | "quality" | "custom";

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
  viewerRoi: ViewerRoi | null;
  cryptomatteLayers: CryptomatteLayer[];
  cryptoLayer: string;
  cryptoSelection: CryptomattePick[];
  cryptoPreviewEnabled: boolean;
  cacheStatus: string;
  cachedFrames: number[];
  isPlaying: boolean;
  isRendering: boolean;
  renderStatus: string | null;
  renderError: string | null;
  viewerProfilePreset: ViewerProfilePreset;
  onTogglePlayback: () => void;
  onFrameChange: (frame: number) => void;
  onRefresh: () => void;
  onApplyViewerProfile: (profile: Exclude<ViewerProfilePreset, "custom">) => void;
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
  onViewerRoiChange: (roi: ViewerRoi | null) => void;
  onCryptoLayerChange: (layer: string) => void;
  onCryptoPreviewChange: (enabled: boolean) => void;
  onCryptoClear: () => void;
  onCryptoPick: (x: number, y: number, mode: "add" | "remove") => void;
  onReloadOcio: () => void;
  onClearCache: () => void;
  onGpuMetrics: (metrics: WebglViewerMetrics | null) => void;
  onClose: () => void;
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
  viewerRoi,
  cryptomatteLayers,
  cryptoLayer,
  cryptoSelection,
  cryptoPreviewEnabled,
  cacheStatus,
  cachedFrames,
  isPlaying,
  isRendering,
  renderStatus,
  renderError,
  viewerProfilePreset,
  onTogglePlayback,
  onFrameChange,
  onRefresh,
  onApplyViewerProfile,
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
  onViewerRoiChange,
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
  const roiDragRef = useRef<{ mode: RoiDragMode; startPoint: DraftPoint; startRoi: ViewerRoi | null } | null>(null);
  const [transform, setTransform] = useState<ViewerTransform>({ x: 0, y: 0, scale: 1 });
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [bitmapRevision, setBitmapRevision] = useState(0);
  const [draftPoints, setDraftPoints] = useState<DraftPoint[]>([]);
  const [showBbox, setShowBbox] = useState(true);
  const [gpuStatus, setGpuStatus] = useState("GPU pending");
  const [pixelSample, setPixelSample] = useState<PixelSample | null>(null);
  const [cacheHudCollapsed, setCacheHudCollapsed] = useState(true);
  const [viewerHudCollapsed, setViewerHudCollapsed] = useState(true);
  const [draftRoi, setDraftRoi] = useState<ViewerRoi | null>(null);
  const frameSize = viewerFrameDimensions(gpuFrame);
  const tileProgress = viewerFrameTileProgress(gpuFrame);
  const pixelAspect = viewerFramePixelAspect(gpuFrame) ?? nodeMetadataPixelAspect(metadata);
  const frameStart = settings?.frame_start ?? 1001;
  const frameEnd = settings?.frame_end ?? 1010;
  const viewerInputSlots = useMemo(() => Array.from({ length: 10 }, (_, index) => String(index)), []);
  const activeSource = useMemo<SourceSize | null>(() => {
    if (frameSize) return frameSize;
    if (imageSize.width > 0 && imageSize.height > 0) return imageSize;
    return null;
  }, [frameSize, imageSize.height, imageSize.width]);
  const activeDisplaySize = useMemo(
    () => (activeSource ? displaySize(activeSource, pixelAspect) : null),
    [activeSource, pixelAspect],
  );
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
    if (viewerRoi) parts.push(`ROI ${viewerRoi.width}x${viewerRoi.height}+${viewerRoi.x}+${viewerRoi.y}`);
    if (tileProgress) parts.push(tileProgress);
    if (nodeMetadataUsesProxy(metadata, activeSource)) {
      parts.push(`Proxy ${activeSource.width}x${activeSource.height}`);
    }
    return parts.join(" | ");
  }, [
    activeSource,
    frame,
    gpuFrame,
    gpuStatus,
    metadata,
    pixelAspect,
    selectedChannel,
    tileProgress,
    transform.scale,
    viewerRoi,
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
    pendingFitRef.current = false;
    setTransform(fitViewerTransform({ width: rect.width, height: rect.height }, source, pixelAspect));
  }, [activeSource, pixelAspect]);

  const setOneToOne = useCallback(() => {
    const canvas = canvasRef.current;
    const source = activeSource;
    if (!canvas || !source) return;
    const rect = canvas.getBoundingClientRect();
    setTransform(oneToOneViewerTransform({ width: rect.width, height: rect.height }, source, pixelAspect));
  }, [activeSource, pixelAspect]);

  const zoomBy = useCallback(
    (factor: number) => {
      const canvas = canvasRef.current;
      if (!canvas || !activeSource) return;
      const rect = canvas.getBoundingClientRect();
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      setTransform((current) => zoomViewerTransform(current, { x: centerX, y: centerY }, factor));
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
      const point = screenToImage(mouseX, mouseY, transform, pixelAspect, source);
      if (!point) {
        setPixelSample(null);
        return;
      }
      setPixelSample(sampleFloatPixel(gpuFrame, Math.floor(point.x), Math.floor(point.y)));
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

    const displayWidth = activeDisplaySize?.width ?? 0;
    const displayHeight = activeDisplaySize?.height ?? 0;
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
        const recoveryKey = viewerFrameRenderKey(gpuFrame, gpuCompareFrame);
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
    drawRoiOverlay(ctx, draftRoi ?? viewerRoi, transform, pixelAspect, viewerTool === "roi");
    drawDraftGeometry(ctx, draftPoints, transform, pixelAspect);
    drawResolutionBadge(ctx, `${activeSource.width}x${activeSource.height}`, transform, displayWidth, displayHeight, rect.width, rect.height);
  }, [
    activeSource,
    activeDisplaySize,
    compareEnabled,
    compareMode,
    draftRoi,
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
    viewerRoi,
    viewerTool,
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
    const formatKey = viewerFrameFormatKey(gpuFrame, pixelAspect);
    const shouldFit = imageFormatRef.current !== formatKey;
    imageFormatRef.current = formatKey;
    setImageSize((current) =>
      current.width === frameSize?.width && current.height === frameSize?.height
        ? current
        : { width: frameSize?.width ?? current.width, height: frameSize?.height ?? current.height },
    );
    if (shouldFit) {
      pendingFitRef.current = true;
      window.requestAnimationFrame(() => fitImage());
    }
  }, [fitImage, frameSize, gpuFrame, pixelAspect]);

  useEffect(() => {
    setPixelSample(null);
  }, [gpuFrame, selectedChannel]);

  useEffect(() => {
    if (!compareImageUrl) {
      compareImageRef.current = null;
      return;
    }
    const image = new Image();
    image.onload = () => {
      compareImageRef.current = image;
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
          <div className="viewer-profile-controls">
            <button className={viewerProfilePreset === "speed" ? "active" : ""} onClick={() => onApplyViewerProfile("speed")}>
              Speed
            </button>
            <button className={viewerProfilePreset === "quality" ? "active" : ""} onClick={() => onApplyViewerProfile("quality")}>
              Quality
            </button>
            <span>{viewerProfilePreset === "custom" ? "Custom" : viewerProfilePreset === "speed" ? "1280x720 proxy" : "Full res"}</span>
          </div>
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
          <button className={viewerTool === "roi" || viewerRoi ? "active" : ""} onClick={() => onViewerToolChange("roi")}>
            ROI
          </button>
          <button disabled={!viewerRoi} onClick={() => onViewerRoiChange(null)}>
            Clear ROI
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
            if (viewerTool === "roi" && activeSource) {
              const point = screenToImage(mouseX, mouseY, transform, pixelAspect, activeSource);
              if (!point) return;
              const hit = roiHitTest(mouseX, mouseY, viewerRoi, transform, pixelAspect);
              roiDragRef.current = {
                mode: hit?.mode ?? "draw",
                startPoint: point,
                startRoi: viewerRoi,
              };
              if (!hit) {
                const nextRoi = normalizeViewerRoi(point, point, activeSource);
                setDraftRoi(nextRoi);
                onViewerRoiChange(nextRoi);
              }
              event.currentTarget.setPointerCapture(event.pointerId);
              return;
            }
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
            if (roiDragRef.current && activeSource) {
              const point = screenToImage(mouseX, mouseY, transform, pixelAspect, activeSource);
              if (!point) return;
              const nextRoi = updateViewerRoiFromDrag(roiDragRef.current, point, activeSource);
              setDraftRoi(nextRoi);
              onViewerRoiChange(nextRoi);
              return;
            }
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
            roiDragRef.current = null;
            setDraftRoi(null);
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
        {!imageUrl && !gpuFrame && !renderError && <div className="empty-viewer">No frame</div>}
        {renderError && <div className="viewer-error-overlay">{renderError}</div>}
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
        <div
          className={renderError ? "render-state error" : isRendering || renderStatus ? "render-state active" : "render-state"}
          title={renderError ?? undefined}
        >
          {renderError ? "Error" : renderStatus ?? (isRendering ? "rendering" : "ready")}
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

function drawBboxOverlay(
  ctx: CanvasRenderingContext2D,
  metadata: NodeMetadata,
  source: SourceSize,
  transform: ViewerTransform,
  pixelAspect: number,
) {
  const formatBox = nodeMetadataFormatBox(metadata);
  const dataWindow = nodeMetadataDataWindow(metadata);
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
  const scale = nodeMetadataScale(metadata, source);
  const x = transform.x + box.x * scale.x * pixelAspect * transform.scale;
  const y = transform.y + box.y * scale.y * transform.scale;
  const width = box.width * scale.x * pixelAspect * transform.scale;
  const height = box.height * scale.y * transform.scale;
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
  const scale = nodeMetadataScale(metadata, source);
  const stripY = transform.y + source.height * transform.scale + 7;
  const formatX0 = transform.x + formatBox.x * scale.x * pixelAspect * transform.scale;
  const formatX1 = formatX0 + formatBox.width * scale.x * pixelAspect * transform.scale;
  const dataX0 = transform.x + dataWindow.x * scale.x * pixelAspect * transform.scale;
  const dataX1 = dataX0 + dataWindow.width * scale.x * pixelAspect * transform.scale;
  const formatY0 = transform.y + formatBox.y * scale.y * transform.scale;
  const formatY1 = formatY0 + formatBox.height * scale.y * transform.scale;
  const dataY0 = transform.y + dataWindow.y * scale.y * transform.scale;
  const dataY1 = dataY0 + dataWindow.height * scale.y * transform.scale;

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

function drawRoiOverlay(
  ctx: CanvasRenderingContext2D,
  roi: ViewerRoi | null,
  transform: ViewerTransform,
  pixelAspect: number,
  highlighted: boolean,
) {
  if (!roi) return;
  const x = transform.x + roi.x * pixelAspect * transform.scale;
  const y = transform.y + roi.y * transform.scale;
  const width = roi.width * pixelAspect * transform.scale;
  const height = roi.height * transform.scale;
  const handle = 6;
  ctx.save();
  ctx.fillStyle = highlighted ? "rgba(71, 191, 123, 0.16)" : "rgba(71, 191, 123, 0.1)";
  ctx.strokeStyle = highlighted ? "#8ef2b0" : "#47bf7b";
  ctx.lineWidth = highlighted ? 2 : 1.5;
  ctx.setLineDash([6, 4]);
  ctx.fillRect(x, y, width, height);
  ctx.strokeRect(x, y, width, height);
  ctx.setLineDash([]);
  ctx.fillStyle = "#d9ffe6";
  for (const [hx, hy] of roiHandlePositions(x, y, width, height)) {
    ctx.fillRect(hx - handle / 2, hy - handle / 2, handle, handle);
    ctx.strokeRect(hx - handle / 2, hy - handle / 2, handle, handle);
  }
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
  ctx.fillStyle = "#d9ffe6";
  ctx.fillText(`ROI ${roi.width}x${roi.height} @ ${roi.x},${roi.y}`, x + 8, Math.max(14, y - 8));
  ctx.restore();
}

function roiHandlePositions(x: number, y: number, width: number, height: number) {
  const xMid = x + width * 0.5;
  const yMid = y + height * 0.5;
  return [
    [x, y],
    [xMid, y],
    [x + width, y],
    [x, yMid],
    [x + width, yMid],
    [x, y + height],
    [xMid, y + height],
    [x + width, y + height],
  ] as const;
}
