import { useEffect, useMemo, useRef, useState } from "react";

import type { NodeModel, ProjectGraph } from "../api/client";
import type { SocketEndpoint } from "../store/appStore";

type Props = {
  graph: ProjectGraph | null;
  selectedNodeId: string | null;
  onSelect: (nodeId: string | null) => void;
  onMoveNode: (nodeId: string, position: [number, number]) => void;
  onConnect: (from: SocketEndpoint, to: SocketEndpoint) => void;
  onPointerWorldPosition: (position: [number, number]) => void;
  activeNodeIds: string[];
  nodeTimings: Record<string, { type: string; duration_ms: number; cache_hit: boolean; timestamp: number }>;
};

const NODE_W = 128;
const NODE_H = 52;
const MIN_SCALE = 0.25;
const MAX_SCALE = 2.5;

type Viewport = {
  x: number;
  y: number;
  scale: number;
};

export function CanvasNodeGraph({
  graph,
  selectedNodeId,
  onSelect,
  onMoveNode,
  onConnect,
  onPointerWorldPosition,
  activeNodeIds,
  nodeTimings,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [viewport, setViewport] = useState<Viewport>({ x: 64, y: 32, scale: 1 });
  const [dragging, setDragging] = useState<
    | { mode: "node"; id: string; dx: number; dy: number }
    | { mode: "pan"; startX: number; startY: number; viewport: Viewport }
    | { mode: "connection"; from: SocketEndpoint; x: number; y: number }
    | null
  >(null);
  const nodes = useMemo(() => Object.values(graph?.nodes ?? {}), [graph]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(1, rect.width * ratio);
    canvas.height = Math.max(1, rect.height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    draw(
      ctx,
      rect.width,
      rect.height,
      graph,
      selectedNodeId,
      viewport,
      new Set(activeNodeIds),
      nodeTimings,
      dragging?.mode === "connection" ? dragging : null,
    );
  }, [activeNodeIds, dragging, graph, nodeTimings, selectedNodeId, viewport]);

  const screenToWorld = (x: number, y: number): [number, number] => [
    (x - viewport.x) / viewport.scale,
    (y - viewport.y) / viewport.scale,
  ];

  const hitNode = (worldX: number, worldY: number) =>
    [...nodes].reverse().find((node) => {
      const [nx, ny] = node.position;
      return worldX >= nx && worldX <= nx + NODE_W && worldY >= ny && worldY <= ny + NODE_H;
    });

  const hitSocket = (worldX: number, worldY: number): SocketEndpoint | null => {
    const radius = 11 / viewport.scale;
    for (const node of [...nodes].reverse()) {
      const [outX, outY] = outputPoint(node);
      if (distance(worldX, worldY, outX, outY) <= radius) {
        return { nodeId: node.id, kind: "output", socket: "out" };
      }
      for (const socket of inputSockets(node)) {
        const [inX, inY] = inputPoint(node, socket);
        if (distance(worldX, worldY, inX, inY) <= radius) {
          return { nodeId: node.id, kind: "input", socket };
        }
      }
    }
    return null;
  };

  return (
    <div className="node-canvas-shell">
      <canvas
        ref={canvasRef}
        className="node-canvas"
        onPointerDown={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const x = event.clientX - bounds.left;
        const y = event.clientY - bounds.top;
        const [worldX, worldY] = screenToWorld(x, y);
        onPointerWorldPosition([worldX, worldY]);
        const socket = hitSocket(worldX, worldY);
        if (socket) {
          onSelect(socket.nodeId);
          setDragging({ mode: "connection", from: socket, x: worldX, y: worldY });
          event.currentTarget.setPointerCapture(event.pointerId);
          return;
        }
        const node = hitNode(worldX, worldY);
        if (!node) {
          onSelect(null);
          setDragging({ mode: "pan", startX: x, startY: y, viewport });
          event.currentTarget.setPointerCapture(event.pointerId);
          return;
        }
        onSelect(node.id);
        setDragging({ mode: "node", id: node.id, dx: worldX - node.position[0], dy: worldY - node.position[1] });
        event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const x = event.clientX - bounds.left;
        const y = event.clientY - bounds.top;
        const [worldX, worldY] = screenToWorld(x, y);
        onPointerWorldPosition([worldX, worldY]);
        if (!dragging) return;
        if (dragging.mode === "pan") {
          setViewport({
            ...dragging.viewport,
            x: dragging.viewport.x + x - dragging.startX,
            y: dragging.viewport.y + y - dragging.startY,
          });
          return;
        }
        if (dragging.mode === "connection") {
          setDragging({ ...dragging, x: worldX, y: worldY });
          return;
        }
        onMoveNode(dragging.id, [worldX - dragging.dx, worldY - dragging.dy]);
        }}
        onPointerUp={(event) => {
        if (dragging?.mode === "connection") {
          const bounds = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - bounds.left;
          const y = event.clientY - bounds.top;
          const [worldX, worldY] = screenToWorld(x, y);
          const socket = hitSocket(worldX, worldY);
          if (socket) {
            onConnect(dragging.from, socket);
          }
        }
        setDragging(null);
        event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onWheel={(event) => {
        event.preventDefault();
        const bounds = event.currentTarget.getBoundingClientRect();
        const screenX = event.clientX - bounds.left;
        const screenY = event.clientY - bounds.top;
        const [worldX, worldY] = screenToWorld(screenX, screenY);
        const delta = event.deltaY > 0 ? 0.9 : 1.1;
        const scale = clamp(viewport.scale * delta, MIN_SCALE, MAX_SCALE);
        setViewport({
          x: screenX - worldX * scale,
          y: screenY - worldY * scale,
          scale,
        });
        }}
      />
      <div className="graph-hud">{Math.round(viewport.scale * 100)}%</div>
    </div>
  );
}

function draw(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  graph: ProjectGraph | null,
  selectedNodeId: string | null,
  viewport: Viewport,
  activeNodeIds: Set<string>,
  nodeTimings: Record<string, { type: string; duration_ms: number; cache_hit: boolean; timestamp: number }>,
  connectionDrag: { from: SocketEndpoint; x: number; y: number } | null,
) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#181818";
  ctx.fillRect(0, 0, width, height);
  drawGrid(ctx, width, height, viewport);
  if (!graph) return;

  ctx.save();
  ctx.translate(viewport.x, viewport.y);
  ctx.scale(viewport.scale, viewport.scale);

  for (const edge of graph.edges) {
    const source = graph.nodes[edge.source_node];
    const target = graph.nodes[edge.target_node];
    if (!source || !target) continue;
    const [sx, sy] = outputPoint(source);
    const [tx, ty] = inputPoint(target, edge.target_socket);
    const viewerEdge = target.type.toLowerCase() === "viewer";
    const activeViewerEdge = viewerEdge && String(target.params.active_input ?? "0") === edge.target_socket;
    ctx.save();
    if (viewerEdge) {
      ctx.globalAlpha = activeViewerEdge ? 0.64 : 0.28;
      ctx.strokeStyle = activeViewerEdge ? "#77c8b4" : "#8a846f";
      ctx.lineWidth = (activeViewerEdge ? 1.6 : 1) / viewport.scale;
      ctx.setLineDash([6 / viewport.scale, 6 / viewport.scale]);
    } else {
      ctx.strokeStyle = "#9c8f5a";
      ctx.lineWidth = 2 / viewport.scale;
      ctx.setLineDash([]);
    }
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.bezierCurveTo(sx, sy + 56, tx, ty - 56, tx, ty);
    ctx.stroke();
    ctx.restore();

    if (viewerEdge) {
      ctx.fillStyle = activeViewerEdge ? "#b7fff0" : "#9f9585";
      ctx.font = "10px Segoe UI, sans-serif";
      ctx.fillText(edge.target_socket, tx - 3, ty - 8);
    }
  }

  if (connectionDrag) {
    const sourceNode = graph.nodes[connectionDrag.from.nodeId];
    if (sourceNode) {
      const [sx, sy] =
        connectionDrag.from.kind === "output"
          ? outputPoint(sourceNode)
          : inputPoint(sourceNode, connectionDrag.from.socket);
      drawConnectionCurve(ctx, sx, sy, connectionDrag.x, connectionDrag.y, {
        strokeStyle: "#77c8b4",
        lineWidth: 2 / viewport.scale,
        alpha: 0.8,
        dashed: true,
        scale: viewport.scale,
      });
    }
  }

  for (const node of Object.values(graph.nodes)) {
    const [x, y] = node.position;
    const selected = node.id === selectedNodeId;
    const active = activeNodeIds.has(node.id);
    const timing = nodeTimings[node.id];
    if (active) {
      ctx.save();
      ctx.shadowColor = "#77c8b4";
      ctx.shadowBlur = 22 / viewport.scale;
      ctx.strokeStyle = "#77c8b4";
      ctx.lineWidth = 4 / viewport.scale;
      roundRect(ctx, x - 4, y - 4, NODE_W + 8, NODE_H + 8, 10);
      ctx.stroke();
      ctx.restore();
    }
    ctx.fillStyle = selected ? "#2b4f4a" : "#242424";
    roundRect(ctx, x, y, NODE_W, NODE_H, 8);
    ctx.fill();
    ctx.strokeStyle = selected ? "#5ed0bb" : "#57534a";
    ctx.lineWidth = (selected ? 2 : 1) / viewport.scale;
    ctx.stroke();

    ctx.fillStyle = nodeColor(node.type);
    roundRect(ctx, x, y, NODE_W, 18, 8);
    ctx.fill();
    ctx.fillStyle = "#f6f1e5";
    ctx.font = "12px Segoe UI, sans-serif";
    ctx.fillText(node.name || node.type, x + 10, y + 14);
    ctx.fillStyle = "#cfc7b5";
    ctx.font = "11px Segoe UI, sans-serif";
    const detail = node.type.toLowerCase() === "viewer" ? `input ${String(node.params.active_input ?? "0")}` : node.id;
    ctx.fillText(detail, x + 10, y + 38);
    if (timing && !active) {
      const ageSeconds = Date.now() / 1000 - timing.timestamp;
      if (ageSeconds < 8) {
        ctx.fillStyle = timing.cache_hit ? "#77c8b4" : "#d8c58a";
        ctx.font = "10px Segoe UI, sans-serif";
        ctx.fillText(timing.cache_hit ? "cache" : `${Math.round(timing.duration_ms)}ms`, x + NODE_W - 46, y + 38);
      }
    }

    ctx.fillStyle = "#141414";
    if (node.type.toLowerCase() === "viewer") {
      drawViewerInputs(ctx, node, viewport);
    } else {
      const sockets = inputSockets(node);
      for (const socket of sockets) {
        const [socketX, socketY] = inputPoint(node, socket);
        drawSocket(ctx, socketX, socketY, "input");
        if (sockets.length > 1) {
          ctx.fillStyle = "#cfc7b5";
          ctx.font = `${9 / Math.max(viewport.scale, 0.75)}px Segoe UI, sans-serif`;
          ctx.fillText(socket, socketX - 5, socketY - 8);
        }
      }
    }
    drawSocket(ctx, x + NODE_W / 2, y + NODE_H, "output");
  }

  ctx.restore();
  ctx.fillStyle = "#a79e8d";
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.fillText(`${Math.round(viewport.scale * 100)}%`, 12, height - 12);
}

function outputPoint(node: { position: [number, number] }): [number, number] {
  return [node.position[0] + NODE_W / 2, node.position[1] + NODE_H];
}

function inputPoint(node: { position: [number, number]; type: string }, socket: string): [number, number] {
  const normalized = node.type.toLowerCase();
  if (normalized === "viewer") {
    const parsedSlot = Number.parseInt(socket, 10);
    const slot = Number.isFinite(parsedSlot) ? clamp(parsedSlot, 0, 9) : 0;
    const usableWidth = NODE_W - 20;
    return [node.position[0] + 10 + (usableWidth / 9) * slot, node.position[1]];
  }
  if (normalized === "merge" || normalized === "channelmerge") {
    const slots = ["a", "b", "mask"];
    const index = Math.max(0, slots.indexOf(socket));
    return [node.position[0] + 28 + index * 36, node.position[1]];
  }
  if (normalized === "copy" || normalized === "shuffle" || normalized === "comparemetadata" || normalized === "copymetadata") {
    const index = socket === "b" ? 1 : 0;
    return [node.position[0] + 46 + index * 36, node.position[1]];
  }
  return [node.position[0] + NODE_W / 2, node.position[1]];
}

function inputSockets(node: NodeModel): string[] {
  const normalized = node.type.toLowerCase();
  if (normalized === "viewer") {
    return Array.from({ length: 10 }, (_, index) => String(index));
  }
  if (normalized === "read" || normalized === "constant") return [];
  if (normalized === "merge" || normalized === "channelmerge") return ["a", "b", "mask"];
  if (normalized === "copy" || normalized === "shuffle" || normalized === "comparemetadata" || normalized === "copymetadata") return ["a", "b"];
  return [defaultInputSocket(node.type)];
}

function defaultInputSocket(type: string) {
  const normalized = type.toLowerCase();
  if (normalized === "merge" || normalized === "channelmerge") return "a";
  if (normalized === "copy" || normalized === "shuffle" || normalized === "comparemetadata" || normalized === "copymetadata") return "b";
  return "in";
}

function drawConnectionCurve(
  ctx: CanvasRenderingContext2D,
  sx: number,
  sy: number,
  tx: number,
  ty: number,
  options: {
    strokeStyle: string;
    lineWidth: number;
    alpha: number;
    dashed: boolean;
    scale: number;
  },
) {
  ctx.save();
  ctx.globalAlpha = options.alpha;
  ctx.strokeStyle = options.strokeStyle;
  ctx.lineWidth = options.lineWidth;
  ctx.setLineDash(options.dashed ? [6 / options.scale, 6 / options.scale] : []);
  const direction = sy <= ty ? 1 : -1;
  const handle = Math.max(48, Math.abs(ty - sy) * 0.42);
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.bezierCurveTo(sx, sy + handle * direction, tx, ty - handle * direction, tx, ty);
  ctx.stroke();
  ctx.restore();
}

function drawSocket(ctx: CanvasRenderingContext2D, x: number, y: number, kind: "input" | "output") {
  ctx.save();
  ctx.fillStyle = kind === "output" ? "#d2c5a0" : "#141414";
  ctx.strokeStyle = kind === "output" ? "#eee2bd" : "#6f6a60";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(x, y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawViewerInputs(
  ctx: CanvasRenderingContext2D,
  node: { position: [number, number]; params: Record<string, unknown> },
  viewport: Viewport,
) {
  const [x, y] = node.position;
  const activeInput = String(node.params.active_input ?? "0");
  for (let index = 0; index < 10; index += 1) {
    const slot = String(index);
    const [slotX] = inputPoint({ position: node.position, type: "viewer" }, slot);
    drawSocket(ctx, slotX, y, "input");
    if (slot === activeInput) {
      ctx.fillStyle = "#77c8b4";
      ctx.beginPath();
      ctx.arc(slotX, y, 3.2, 0, Math.PI * 2);
      ctx.fill();
    }
    if (slot === "0" || slot === activeInput || slot === "9") {
      ctx.fillStyle = "#cfc7b5";
      ctx.font = `${9 / Math.max(viewport.scale, 0.75)}px Segoe UI, sans-serif`;
      ctx.fillText(slot, slotX - 2.5, y - 8);
    }
  }
}

function drawGrid(ctx: CanvasRenderingContext2D, width: number, height: number, viewport: Viewport) {
  ctx.strokeStyle = "#252525";
  ctx.lineWidth = 1;
  const grid = 24 * viewport.scale;
  const offsetX = viewport.x % grid;
  const offsetY = viewport.y % grid;
  for (let x = offsetX; x < width; x += grid) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = offsetY; y < height; y += grid) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function nodeColor(type: string) {
  const normalized = type.toLowerCase();
  if (normalized === "read") return "#6d5d2d";
  if (normalized === "viewer") return "#2d5c67";
  if (normalized === "write") return "#694046";
  if (normalized === "grade" || normalized === "colorspace") return "#51446d";
  if (normalized === "merge" || normalized === "channelmerge") return "#49613a";
  if (["shuffle", "copy", "addchannels", "remove", "premult", "unpremult"].includes(normalized)) return "#5f3f55";
  if (normalized.includes("metadata") || normalized === "addtimecode") return "#4f5540";
  return "#4b4b4b";
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function distance(ax: number, ay: number, bx: number, by: number) {
  return Math.hypot(ax - bx, ay - by);
}
