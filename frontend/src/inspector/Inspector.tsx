import type { CacheStatus, ColorConfig, NodeMetadata, NodeModel, NodeTiming, ProjectGraph, ProjectSettings, RequestTiming } from "../api/client";
import type { LogEntry } from "../store/appStore";
import type { WebglViewerMetrics } from "../viewer/webglFloatViewer";

export type InspectorTab = "node" | "root" | "metrics" | "log";

type Props = {
  node: NodeModel | null;
  graph: ProjectGraph | null;
  metadata: NodeMetadata | null;
  settings: ProjectSettings | null;
  colorConfig: ColorConfig | null;
  logs: LogEntry[];
  metricsStatus: CacheStatus | null;
  nodeTimings: Record<string, NodeTiming>;
  frontendFrameMs: number | null;
  frontendRequestTimings: RequestTiming[];
  viewerGpuMetrics: WebglViewerMetrics | null;
  activeTab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  onChange: (node: NodeModel) => void;
  onSettingsChange: (settings: Partial<ProjectSettings>, affectsRender?: boolean) => void;
  onRenderWrite: () => void;
};

export function Inspector({
  node,
  graph,
  metadata,
  settings,
  colorConfig,
  logs,
  metricsStatus,
  nodeTimings,
  frontendFrameMs,
  frontendRequestTimings,
  viewerGpuMetrics,
  activeTab,
  onTabChange,
  onChange,
  onSettingsChange,
  onRenderWrite,
}: Props) {
  return (
    <section className="inspector">
      <div className="panel-title">Inspector</div>
      <div className="inspector-tabs" role="tablist" aria-label="Inspector tabs">
        <button className={activeTab === "node" ? "active" : ""} onClick={() => onTabChange("node")}>
          Node
        </button>
        <button className={activeTab === "root" ? "active" : ""} onClick={() => onTabChange("root")}>
          Root
        </button>
        <button className={activeTab === "metrics" ? "active" : ""} onClick={() => onTabChange("metrics")}>
          Metrics
        </button>
        <button className={activeTab === "log" ? "active" : ""} onClick={() => onTabChange("log")}>
          Log
        </button>
      </div>
      {activeTab === "node" &&
        (node ? (
          <NodeInspector node={node} graph={graph} metadata={metadata} onChange={onChange} onRenderWrite={onRenderWrite} />
        ) : (
          <div className="empty-copy">Select a node</div>
        ))}
      {activeTab === "root" && (
        <RootSettings settings={settings} colorConfig={colorConfig} onSettingsChange={onSettingsChange} />
      )}
      {activeTab === "metrics" && (
        <MetricsInspector
          metricsStatus={metricsStatus}
          nodeTimings={nodeTimings}
          frontendFrameMs={frontendFrameMs}
          frontendRequestTimings={frontendRequestTimings}
          viewerGpuMetrics={viewerGpuMetrics}
        />
      )}
      {activeTab === "log" && <InspectorLog logs={logs} />}
    </section>
  );
}

function NodeInspector({
  node,
  graph,
  metadata,
  onChange,
  onRenderWrite,
}: {
  node: NodeModel;
  graph: ProjectGraph | null;
  metadata: NodeMetadata | null;
  onChange: (node: NodeModel) => void;
  onRenderWrite: () => void;
}) {
  const params = Object.entries(node.params);
  const expressionSuggestions = buildExpressionSuggestions(graph);
  return (
    <>
      <div className="inspector-heading">{node.name || node.type}</div>
      <label>
        Name
        <input value={node.name ?? ""} onChange={(event) => onChange({ ...node, name: event.target.value })} />
      </label>
      {params.map(([key, value]) => (
        <label key={key}>
          {formatParamLabel(key)}
          <ParamInput
            node={node}
            paramKey={key}
            value={value}
            metadata={metadata}
            expressionSuggestions={expressionSuggestions}
            onChange={onChange}
          />
        </label>
      ))}
      {node.type.toLowerCase() === "write" && (
        <div className="inspector-actions">
          <button onClick={onRenderWrite}>Render Frame</button>
        </div>
      )}
      {metadata && (
        <details className="metadata-view" open={node.type.toLowerCase() === "read"}>
          <summary>
            Metadata | {metadata.width}x{metadata.height}
            {metadata.pixel_aspect !== 1 ? ` | PA ${metadata.pixel_aspect}` : ""} | {metadata.colorspace}
          </summary>
          <div className="channel-list">
            {metadata.channels.map((channel) => (
              <span key={channel}>{channel}</span>
            ))}
          </div>
          <div className="metadata-table">
            {Object.entries(metadata.metadata)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([key, value]) => (
                <div key={key}>
                  <span>{key}</span>
                  <code>{formatMetadataValue(value)}</code>
                </div>
              ))}
          </div>
          <details>
            <summary>Resolved Params</summary>
            <pre>{JSON.stringify(metadata.resolved_params, null, 2)}</pre>
          </details>
          <details>
            <summary>Bindable Outputs</summary>
            <pre>{JSON.stringify(metadata.bindable_outputs, null, 2)}</pre>
          </details>
          {!!Object.keys(metadata.expression_errors).length && (
            <details open>
              <summary>Expression Errors</summary>
              <pre>{JSON.stringify(metadata.expression_errors, null, 2)}</pre>
            </details>
          )}
        </details>
      )}
    </>
  );
}

function RootSettings({
  settings,
  colorConfig,
  onSettingsChange,
}: {
  settings: ProjectSettings | null;
  colorConfig: ColorConfig | null;
  onSettingsChange: (settings: Partial<ProjectSettings>, affectsRender?: boolean) => void;
}) {
  if (!settings) return <div className="empty-copy">No project settings loaded</div>;
  return (
    <div className="root-settings">
      <label>
        Start
        <input
          type="number"
          value={settings.frame_start}
          onChange={(event) => onSettingsChange({ frame_start: Number(event.target.value) })}
        />
      </label>
      <label>
        End
        <input
          type="number"
          value={settings.frame_end}
          onChange={(event) => onSettingsChange({ frame_end: Number(event.target.value) })}
        />
      </label>
      <label>
        FPS
        <input
          type="number"
          value={settings.fps}
          step="0.01"
          onChange={(event) => onSettingsChange({ fps: Number(event.target.value) }, false)}
        />
      </label>
      <label>
        Working
        <select
          value={settings.working_colorspace}
          onChange={(event) => onSettingsChange({ working_colorspace: event.target.value })}
        >
          {(colorConfig?.colorspaces ?? [settings.working_colorspace]).map((colorspace) => (
            <option key={colorspace} value={colorspace}>
              {colorspace}
            </option>
          ))}
        </select>
      </label>
      <label>
        OCIO
        <input
          list="ocio-configs"
          value={settings.ocio_config ?? ""}
          onChange={(event) => onSettingsChange({ ocio_config: event.target.value || null })}
          placeholder="auto, builtin, or .ocio"
        />
        <datalist id="ocio-configs">
          {(colorConfig?.builtin_configs ?? []).map((config) => (
            <option key={config.name} value={config.name}>
              {config.description}
            </option>
          ))}
        </datalist>
      </label>
      <label>
        Script Path
        <input
          value={settings.project_path ?? ""}
          onChange={(event) => onSettingsChange({ project_path: event.target.value || null }, false)}
          placeholder="project.opencomp"
        />
      </label>
      <label>
        Output
        <input
          value={settings.default_output_path}
          onChange={(event) => onSettingsChange({ default_output_path: event.target.value }, false)}
        />
      </label>
      <label className="toggle-label">
        <input
          type="checkbox"
          checked={settings.proxy_enabled}
          onChange={(event) => onSettingsChange({ proxy_enabled: event.target.checked })}
        />
        Proxy
      </label>
      <label>
        Proxy W
        <input
          type="number"
          value={settings.viewer_max_width}
          disabled={!settings.proxy_enabled}
          onChange={(event) => onSettingsChange({ viewer_max_width: Number(event.target.value) })}
        />
      </label>
      <label>
        Proxy H
        <input
          type="number"
          value={settings.viewer_max_height}
          disabled={!settings.proxy_enabled}
          onChange={(event) => onSettingsChange({ viewer_max_height: Number(event.target.value) })}
        />
      </label>
      <label className="toggle-label">
        <input
          type="checkbox"
          checked={settings.cache_enabled}
          onChange={(event) => onSettingsChange({ cache_enabled: event.target.checked })}
        />
        Cache
      </label>
      <label className="toggle-label">
        <input
          type="checkbox"
          checked={settings.auto_refresh}
          onChange={(event) => onSettingsChange({ auto_refresh: event.target.checked }, false)}
        />
        Auto
      </label>
      <label className="toggle-label">
        <input
          type="checkbox"
          checked={settings.tile_rendering_enabled}
          onChange={(event) => onSettingsChange({ tile_rendering_enabled: event.target.checked })}
        />
        Tiles
      </label>
      <label>
        Tile H
        <input
          type="number"
          value={settings.tile_height}
          min={1}
          disabled={!settings.tile_rendering_enabled}
          onChange={(event) => onSettingsChange({ tile_height: Number(event.target.value) })}
        />
      </label>
      <label>
        Tile Workers
        <input
          type="number"
          value={settings.tile_workers}
          min={1}
          disabled={!settings.tile_rendering_enabled}
          onChange={(event) => onSettingsChange({ tile_workers: Number(event.target.value) })}
        />
      </label>
      <label>
        Render Workers
        <input
          type="number"
          value={settings.render_workers}
          min={1}
          onChange={(event) => onSettingsChange({ render_workers: Number(event.target.value) })}
        />
      </label>
      <label>
        Read Workers
        <input
          type="number"
          value={settings.read_workers}
          min={1}
          onChange={(event) => onSettingsChange({ read_workers: Number(event.target.value) })}
        />
      </label>
      <label>
        Transfer Lanes
        <input
          type="number"
          value={settings.viewer_tile_lanes}
          min={1}
          max={8}
          onChange={(event) => onSettingsChange({ viewer_tile_lanes: Number(event.target.value) }, false)}
        />
      </label>
    </div>
  );
}

function InspectorLog({ logs }: { logs: LogEntry[] }) {
  return (
    <div className="inspector-log">
      {logs.map((entry) => (
        <div key={entry.id} className={entry.level}>
          {entry.message}
        </div>
      ))}
    </div>
  );
}

function MetricsInspector({
  metricsStatus,
  nodeTimings,
  frontendFrameMs,
  frontendRequestTimings,
  viewerGpuMetrics,
}: {
  metricsStatus: CacheStatus | null;
  nodeTimings: Record<string, NodeTiming>;
  frontendFrameMs: number | null;
  frontendRequestTimings: RequestTiming[];
  viewerGpuMetrics: WebglViewerMetrics | null;
}) {
  if (!metricsStatus) return <div className="empty-copy">No metrics yet</div>;
  const nodeRows = Object.entries(nodeTimings).sort(([, a], [, b]) => b.timestamp - a.timestamp);
  const phaseRows = [...(metricsStatus.phase_timings ?? [])].reverse().slice(0, 32);
  const requestRows = [...(metricsStatus.request_timings ?? [])].reverse().slice(0, 16);
  const frontendRows = [...frontendRequestTimings].reverse().slice(0, 16);
  const lastFrontendAverage = average(frontendRequestTimings.slice(-30).map((timing) => timing.total_ms));
  return (
    <div className="metrics-view">
      <MetricSummary
        title="Frame"
        rows={[
          ["frontend", frontendFrameMs === null ? "-" : `${Math.round(frontendFrameMs)}ms`],
          ["avg", lastFrontendAverage === null ? "-" : `${Math.round(lastFrontendAverage)}ms`],
          ["backend", metricsStatus.last_request_timing ? `${Math.round(metricsStatus.last_request_timing.total_ms)}ms` : "-"],
          ["transport", metricsStatus.last_request_timing?.transport ?? "-"],
        ]}
      />
      <MetricSummary
        title="Cache"
        rows={[
          ["nodes", `${metricsStatus.entries}`],
          ["float/final", `${metricsStatus.float_preview_entries}/${metricsStatus.preview_entries}`],
          ["hit", `${metricsStatus.hits}/${metricsStatus.float_preview_hits}/${metricsStatus.preview_hits}`],
          ["miss", `${metricsStatus.misses}/${metricsStatus.float_preview_misses}/${metricsStatus.preview_misses}`],
        ]}
      />
      <MetricSummary
        title="GPU Viewer"
        rows={[
          ["mode", viewerGpuMetrics ? viewerGpuMetrics.mode : "-"],
          ["ocio", viewerGpuMetrics ? (viewerGpuMetrics.ocio_gpu ? "gpu shader" : "fallback") : "-"],
          ["upload", viewerGpuMetrics ? `${Math.round(viewerGpuMetrics.upload_ms)}ms` : "-"],
          ["draw", viewerGpuMetrics ? `${Math.round(viewerGpuMetrics.draw_ms)}ms` : "-"],
        ]}
      />
      {viewerGpuMetrics?.fallback_reason && (
        <MetricTable
          title="GPU Fallback"
          rows={[["reason", viewerGpuMetrics.fallback_reason.slice(0, 120), viewerGpuMetrics.fallback_reason]]}
        />
      )}
      <MetricTable
        title="Backend Requests"
        rows={requestRows.map((timing) => [
          `F${timing.frame} ${timing.transport}`,
          `${Math.round(timing.total_ms)}ms`,
          [
            `backend ${Math.round(timing.backend_render_ms)}ms`,
            `eval ${Math.round(timing.node_eval_ms ?? timing.backend_render_ms)}ms`,
            timing.tile_native ? `native tiles ${Math.round(timing.tile_render_ms ?? 0)}ms` : "",
            `resize ${Math.round(timing.resize_ms ?? 0)}ms`,
            `encode ${Math.round(timing.tile_encode_ms ?? 0)}ms`,
            `write ${Math.round(timing.ws_write_ms ?? timing.send_ms)}ms`,
            timing.lane_count ? `lanes ${timing.lane_count}` : "",
          ]
            .filter(Boolean)
            .join(" | "),
        ])}
      />
      <MetricTable
        title="Frontend Requests"
        rows={frontendRows.map((timing) => [
          `F${timing.frame} ${timing.transport}${timing.frontend_cache_hit ? " hit" : ""}`,
          `${Math.round(timing.total_ms)}ms`,
          [
            formatBytes(timing.bytes),
            `wait ${Math.round(timing.ws_wait_ms ?? 0)}ms`,
            `recv ${Math.round(timing.receive_ms ?? 0)}ms`,
            `copy ${Math.round(timing.tile_copy_ms ?? 0)}ms`,
            `upload ${Math.round(timing.webgl_upload_ms ?? 0)}ms`,
            `draw ${Math.round(timing.webgl_draw_ms ?? 0)}ms`,
            timing.browser_cache_hit_ms ? `browser ${Math.round(timing.browser_cache_hit_ms)}ms` : "",
          ]
            .filter(Boolean)
            .join(" | "),
        ])}
      />
      <MetricTable
        title="Nodes"
        rows={nodeRows.map(([nodeId, timing]) => [
          nodeId,
          timing.cache_hit ? "cache" : `${Math.round(timing.duration_ms)}ms`,
          timing.type,
        ])}
      />
      <MetricTable
        title="Phases"
        rows={phaseRows.map((timing) => [
          `${timing.node_id} ${timing.phase}`,
          `${Math.round(timing.duration_ms)}ms`,
          formatMetricDetails(timing.details),
        ])}
      />
    </div>
  );
}

function MetricSummary({ title, rows }: { title: string; rows: Array<[string, string]> }) {
  return (
    <section className="metric-card">
      <h3>{title}</h3>
      <div className="metric-grid">
        {rows.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function MetricTable({ title, rows }: { title: string; rows: Array<[string, string, string]> }) {
  return (
    <section className="metric-card">
      <h3>{title}</h3>
      <div className="metric-table">
        {rows.length === 0 ? (
          <div className="empty-copy">No entries</div>
        ) : (
          rows.map(([label, value, detail], index) => (
            <div key={`${label}-${index}`}>
              <span>{label}</span>
              <strong>{value}</strong>
              <code>{detail}</code>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ParamInput({
  node,
  paramKey,
  value,
  metadata,
  expressionSuggestions,
  onChange,
}: {
  node: NodeModel;
  paramKey: string;
  value: unknown;
  metadata: NodeMetadata | null;
  expressionSuggestions: string[];
  onChange: (node: NodeModel) => void;
}) {
  const expression = node.param_expressions?.[paramKey] ?? null;
  const expressionEnabled = Boolean(expression?.enabled && expression?.source);
  const resolvedValue = metadata?.resolved_params?.[paramKey];
  const expressionError = metadata?.expression_errors?.[paramKey];
  const datalistId = `expr-${node.id}-${paramKey}`;
  const options = optionsFor(node.type, paramKey);
  const toggleExpression = () => {
    const next = { ...(node.param_expressions ?? {}) };
    if (expressionEnabled) {
      delete next[paramKey];
    } else {
      next[paramKey] = { source: `node("${node.id}").${paramKey}`, enabled: true, compiled_cache_key: null };
    }
    onChange({ ...node, param_expressions: next });
  };
  const updateExpression = (source: string) => {
    onChange({
      ...node,
      param_expressions: {
        ...(node.param_expressions ?? {}),
        [paramKey]: { source, enabled: true, compiled_cache_key: null },
      },
    });
  };
  if (expressionEnabled) {
    return (
      <div className="expression-param">
        <div className="expression-row">
          <button type="button" onClick={toggleExpression}>
            fx
          </button>
          <input list={datalistId} value={expression?.source ?? ""} onChange={(event) => updateExpression(event.target.value)} />
          <datalist id={datalistId}>
            {expressionSuggestions.map((suggestion) => (
              <option key={suggestion} value={suggestion} />
            ))}
          </datalist>
        </div>
        <div className="expression-meta">
          <span>{expressionError ? `error: ${expressionError}` : `resolved: ${formatMetadataValue(resolvedValue)}`}</span>
        </div>
      </div>
    );
  }
  if (options) {
    return (
      <div className="expression-param">
        <div className="expression-row">
          <button type="button" onClick={toggleExpression}>
            fx
          </button>
          <select
            value={String(value ?? "")}
            onChange={(event) => onChange({ ...node, params: { ...node.params, [paramKey]: event.target.value } })}
          >
            {options.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      </div>
    );
  }

  return (
    <div className="expression-param">
      <div className="expression-row">
        <button type="button" onClick={toggleExpression}>
          fx
        </button>
        <input
          type={typeof value === "number" ? "number" : typeof value === "boolean" ? "checkbox" : "text"}
          checked={typeof value === "boolean" ? value : undefined}
          value={typeof value === "boolean" ? undefined : formatInputValue(value)}
          step="0.01"
          onChange={(event) => {
            let next: unknown = event.target.value;
            if (typeof value === "number") next = Number(event.target.value);
            if (typeof value === "boolean") next = event.target.checked;
            if (Array.isArray(value) || (value && typeof value === "object")) {
              try {
                next = JSON.parse(event.target.value);
              } catch {
                next = event.target.value;
              }
            }
            onChange({ ...node, params: { ...node.params, [paramKey]: next } });
          }}
        />
      </div>
    </div>
  );
}

function optionsFor(type: string, paramKey: string) {
  const normalized = type.toLowerCase();
  const channelSetOptions = ["none", "rgba", "rgb", "alpha", "all", "r", "g", "b", "a"];
  const channelOptions = [
    "none",
    "rgba.red",
    "rgba.green",
    "rgba.blue",
    "rgba.alpha",
    "r",
    "g",
    "b",
    "a",
    "luma",
    "black",
    "white",
  ];
  if (normalized === "read" && paramKey === "localization_policy") {
    return ["from auto-localize path", "on", "on demand", "off"];
  }
  if (normalized === "read" && ["before", "after"].includes(paramKey)) {
    return ["hold", "loop", "bounce", "black"];
  }
  if (normalized === "read" && paramKey === "frame_mode") {
    return ["expression", "start at", "offset", "frame"];
  }
  if (normalized === "framerange" && paramKey === "mode") {
    return ["original", "hold", "black", "loop", "bounce"];
  }
  if (normalized === "retime" && paramKey === "filter") {
    return ["nearest", "linear"];
  }
  if (normalized === "read" && paramKey === "missing_frames") {
    return ["error", "black", "nearest frame"];
  }
  if (normalized === "read" && paramKey === "input_transform") {
    return ["default (linear)", "raw", "sRGB", "ACES2065-1", "ACEScg"];
  }
  if (normalized === "read" && paramKey === "edge_pixels") {
    return ["plate detect", "black", "hold", "repeat"];
  }
  if (normalized === "merge" && paramKey === "operation") {
    return [
      "over",
      "under",
      "atop",
      "in",
      "out",
      "plus",
      "minus",
      "from",
      "difference",
      "multiply",
      "screen",
      "max",
      "min",
      "average",
      "divide",
      "mask",
      "stencil",
      "xor",
      "matte",
      "copy",
    ];
  }
  if (normalized === "merge" && ["metadata_from", "range_from"].includes(paramKey)) {
    return ["b", "a", "all"];
  }
  if (normalized === "merge" && paramKey === "bbox") {
    return ["union", "intersection", "a", "b"];
  }
  if (normalized === "merge" && ["a_channels", "b_channels", "output", "also_merge", "mask"].includes(paramKey)) {
    return channelSetOptions;
  }
  if (normalized === "channelmerge" && paramKey === "operation") {
    return ["union", "plus", "minus", "from", "multiply", "divide", "max", "min", "absminus", "in", "out", "stencil", "screen", "xor"];
  }
  if (normalized === "channelmerge" && ["a_channel", "b_channel", "output", "mask"].includes(paramKey)) {
    return channelOptions;
  }
  if (["addchannels", "remove"].includes(normalized) && ["channels", "channels2", "channels3", "channels4"].includes(paramKey)) {
    return channelSetOptions;
  }
  if (normalized === "remove" && paramKey === "operation") {
    return ["remove", "keep"];
  }
  if (normalized === "write" && paramKey === "channels") {
    return ["rgb", "rgba", "all", "alpha"];
  }
  if (normalized === "write" && paramKey === "frame_mode") {
    return ["expression", "start at", "offset", "frame"];
  }
  if (normalized === "write" && paramKey === "file_type") {
    return ["exr", "png", "jpg"];
  }
  if (normalized === "write" && paramKey === "datatype") {
    return ["16 bit half", "32 bit float", "8 bit", "16 bit"];
  }
  if (normalized === "write" && paramKey === "compression") {
    return ["Zip (1 scanline)", "Zip (16 scanline)", "None", "PIZ", "DWAA"];
  }
  if (normalized === "write" && paramKey === "metadata") {
    return ["default metadata", "all", "none"];
  }
  if (normalized === "write" && paramKey === "missing_frames") {
    return ["error", "black", "checkerboard", "nearest frame"];
  }
  if (normalized === "write" && paramKey === "output_transform") {
    return ["default (linear)", "sRGB", "ACES2065-1", "ACEScg", "raw"];
  }
  if (normalized === "modifymetadata" && paramKey === "action") {
    return ["set", "remove"];
  }
  if (normalized === "copymetadata" && paramKey === "mode") {
    return ["all", "pattern"];
  }
  if (normalized === "cryptomatte" && paramKey === "output") {
    return ["alpha", "matte"];
  }
  if (normalized === "shuffle" && ["out_r", "out_g", "out_b", "out_a"].includes(paramKey)) {
    return [...channelOptions, "a.rgba.red", "a.rgba.green", "a.rgba.blue", "a.rgba.alpha", "b.rgba.red", "b.rgba.green", "b.rgba.blue", "b.rgba.alpha"];
  }
  if (normalized === "shuffle" && ["input_a", "input_b", "output_layer"].includes(paramKey)) {
    return ["none", "rgba", "rgb"];
  }
  if (normalized === "copy" && /^from\d$/.test(paramKey)) {
    return channelOptions;
  }
  if (normalized === "copy" && /^to\d$/.test(paramKey)) {
    return channelOptions.filter((option) => !["luma", "black", "white"].includes(option));
  }
  if (normalized === "copy" && ["channels", "mask"].includes(paramKey)) {
    return paramKey === "mask" ? channelOptions : channelSetOptions;
  }
  if (normalized === "copy" && paramKey === "metadata_from") {
    return ["b", "a", "all"];
  }
  if (["channels"].includes(paramKey)) {
    return channelSetOptions;
  }
  return null;
}

function formatParamLabel(key: string) {
  return key.replace(/_/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatInputValue(value: unknown) {
  if (Array.isArray(value) || (value && typeof value === "object")) {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}

function buildExpressionSuggestions(graph: ProjectGraph | null) {
  if (!graph) return ["frame", "fps"];
  const suggestions = new Set<string>(["frame", "fps"]);
  for (const node of Object.values(graph.nodes)) {
    for (const key of Object.keys(node.params ?? {})) {
      suggestions.add(`node("${node.id}").${key}`);
    }
  }
  return [...suggestions].sort((left, right) => left.localeCompare(right));
}

function average(values: number[]) {
  if (values.length === 0) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMetricDetails(value: Record<string, unknown>) {
  const entries = Object.entries(value);
  if (entries.length === 0) return "";
  return entries
    .slice(0, 4)
    .map(([key, detail]) => `${key}=${formatMetadataValue(detail)}`)
    .join(" ");
}

function formatMetadataValue(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
