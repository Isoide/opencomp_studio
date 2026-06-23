import { create } from "zustand";

import type {
  ColorConfig,
  EdgeModel,
  NodeModel,
  Project,
  ProjectGraph,
  ProjectPreferences,
  ProjectSettings,
} from "../api/client";

type LogLevel = "info" | "error";
type AddNodeOptions = {
  position?: [number, number];
};

type ViewerInputResult =
  | { status: "assigned"; slot: string; nodeId: string }
  | { status: "switched"; slot: string }
  | { status: "missing-viewer"; slot: string };

type DeleteNodeResult =
  | { status: "deleted"; nodeId: string }
  | { status: "protected-viewer"; nodeId: string }
  | { status: "nothing-selected" };

export type SocketEndpoint = {
  nodeId: string;
  kind: "input" | "output";
  socket: string;
};

type ConnectNodesResult =
  | { status: "connected"; sourceNode: string; targetNode: string; targetSocket: string }
  | { status: "invalid"; reason: string };

export type LogEntry = {
  id: string;
  level: LogLevel;
  message: string;
};

type AppState = {
  backendStatus: string;
  project: Project | null;
  graph: ProjectGraph | null;
  selectedNodeId: string | null;
  frame: number;
  viewerUrl: string | null;
  logs: LogEntry[];
  scriptPath: string;
  colorConfig: ColorConfig | null;
  renderRevision: number;
  isPlaying: boolean;
  isRendering: boolean;
  setBackendStatus: (status: string) => void;
  setProject: (project: Project) => void;
  setGraph: (graph: ProjectGraph) => void;
  setColorConfig: (colorConfig: ColorConfig) => void;
  selectNode: (nodeId: string | null) => void;
  setFrame: (frame: number) => void;
  setViewerUrl: (url: string | null) => void;
  setPlaying: (isPlaying: boolean) => void;
  setRendering: (isRendering: boolean) => void;
  updateProjectSettings: (settings: Partial<ProjectSettings>, affectsRender?: boolean) => void;
  updatePreferences: (preferences: Partial<ProjectPreferences>) => void;
  updateNode: (node: NodeModel) => void;
  moveNode: (nodeId: string, position: [number, number]) => void;
  addNode: (type: string, options?: AddNodeOptions) => void;
  connectNodes: (from: SocketEndpoint, to: SocketEndpoint) => ConnectNodesResult;
  setViewerInput: (slot: string) => ViewerInputResult;
  deleteSelectedNode: () => DeleteNodeResult;
  setScriptPath: (path: string) => void;
  addLog: (level: LogLevel, message: string) => void;
};

export const useAppStore = create<AppState>((set, get) => ({
  backendStatus: "checking",
  project: null,
  graph: null,
  selectedNodeId: null,
  frame: 1001,
  viewerUrl: null,
  logs: [],
  scriptPath: "",
  colorConfig: null,
  renderRevision: 0,
  isPlaying: false,
  isRendering: false,
  setBackendStatus: (backendStatus) => set({ backendStatus }),
  setProject: (project) =>
    set({
      project,
      graph: project.graph,
      frame: project.settings.frame_start,
      renderRevision: get().renderRevision + 1,
    }),
  setGraph: (graph) => set({ graph }),
  setColorConfig: (colorConfig) => set({ colorConfig }),
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
  setFrame: (frame) => set({ frame }),
  setViewerUrl: (viewerUrl) => set({ viewerUrl }),
  setPlaying: (isPlaying) => set({ isPlaying }),
  setRendering: (isRendering) => set({ isRendering }),
  updateProjectSettings: (settings, affectsRender = true) => {
    const project = get().project;
    if (!project) return;
    set({
      project: { ...project, settings: { ...project.settings, ...settings } },
      renderRevision: affectsRender ? get().renderRevision + 1 : get().renderRevision,
    });
  },
  updatePreferences: (preferences) => {
    const project = get().project;
    if (!project) return;
    const affectsViewer = Object.prototype.hasOwnProperty.call(preferences, "viewer_transfer_precision");
    set({
      project: { ...project, preferences: { ...project.preferences, ...preferences } },
      renderRevision: affectsViewer ? get().renderRevision + 1 : get().renderRevision,
    });
  },
  updateNode: (node) => {
    const graph = get().graph;
    if (!graph) return;
    set({
      graph: { ...graph, nodes: { ...graph.nodes, [node.id]: node } },
      renderRevision: get().renderRevision + 1,
    });
  },
  moveNode: (nodeId, position) => {
    const graph = get().graph;
    if (!graph) return;
    const node = graph.nodes[nodeId];
    if (!node) return;
    set({ graph: { ...graph, nodes: { ...graph.nodes, [nodeId]: { ...node, position } } } });
  },
  addNode: (type, options = {}) => {
    const graph = get().graph;
    if (!graph) return;
    const nodeNumber = Object.values(graph.nodes).filter((node) => node.type === type).length + 1;
    const id = `${type}${nodeNumber}`;
    const selectedNodeId = get().selectedNodeId;
    const selectedNode = selectedNodeId ? graph.nodes[selectedNodeId] : null;
    const viewer = getViewer(graph);
    const shouldAutoConnect = get().project?.preferences.auto_connect_new_nodes ?? true;
    const desiredPosition: [number, number] = selectedNode
      ? [selectedNode.position[0], selectedNode.position[1] + 140]
      : options.position ?? [120 + nodeNumber * 24, 140 + nodeNumber * 88];
    const position = findOpenPosition(graph, desiredPosition);
    const node: NodeModel = {
      id,
      type,
      name: type,
      position,
      params: defaultParamsFor(type),
      param_expressions: {},
      inputs: {},
      outputs: { out: "ImageFrame" },
    };
    let edges: EdgeModel[] = [...graph.edges];
    const inputSocket = inputSocketFor(type);
    if (selectedNode && selectedNode.type.toLowerCase() !== "viewer" && inputSocket && shouldAutoConnect) {
      const downstreamFlowEdges = edges.filter((edge) => edge.source_node === selectedNode.id && !isViewerEdge(edge, graph));
      const downstreamIds = new Set(downstreamFlowEdges.map((edge) => edge.id));
      edges = edges.map((edge) =>
        downstreamIds.has(edge.id)
          ? {
              ...edge,
              source_node: id,
              source_socket: "out",
            }
          : edge,
      );
      edges.push({
        id: makeEdgeId(selectedNode.id, id, inputSocket),
        source_node: selectedNode.id,
        source_socket: "out",
        target_node: id,
        target_socket: inputSocket,
      });

      if (viewer) {
        const activeViewerInput = String(viewer.params.active_input ?? "0");
        edges = edges.map((edge) =>
          edge.target_node === viewer.id &&
          edge.target_socket === activeViewerInput &&
          edge.source_node === selectedNode.id
            ? {
                ...edge,
                id: makeViewerEdgeId(viewer.id, activeViewerInput, id),
                source_node: id,
              }
            : edge,
        );
      }
    }
    set({
      graph: { nodes: { ...graph.nodes, [id]: node }, edges },
      selectedNodeId: id,
      renderRevision: get().renderRevision + 1,
    });
  },
  connectNodes: (from, to) => {
    const graph = get().graph;
    if (!graph) return { status: "invalid", reason: "No graph is loaded." };
    if (from.kind === to.kind) return { status: "invalid", reason: "Connect an output to an input." };

    const source = from.kind === "output" ? from : to;
    const target = from.kind === "input" ? from : to;
    const sourceNode = graph.nodes[source.nodeId];
    const targetNode = graph.nodes[target.nodeId];
    if (!sourceNode || !targetNode) return { status: "invalid", reason: "Connection target is missing." };
    if (source.nodeId === target.nodeId) return { status: "invalid", reason: "A node cannot connect to itself." };

    const edgeId =
      targetNode.type.toLowerCase() === "viewer"
        ? makeViewerEdgeId(target.nodeId, target.socket, source.nodeId)
        : makeEdgeId(source.nodeId, target.nodeId, target.socket);
    const edges = [
      ...graph.edges.filter((edge) => !(edge.target_node === target.nodeId && edge.target_socket === target.socket)),
      {
        id: edgeId,
        source_node: source.nodeId,
        source_socket: source.socket,
        target_node: target.nodeId,
        target_socket: target.socket,
      },
    ];

    set({
      graph: { ...graph, edges },
      selectedNodeId: target.nodeId,
      renderRevision: get().renderRevision + 1,
    });
    return {
      status: "connected",
      sourceNode: source.nodeId,
      targetNode: target.nodeId,
      targetSocket: target.socket,
    };
  },
  setViewerInput: (slot) => {
    const graph = get().graph;
    if (!graph) return { status: "missing-viewer", slot };
    const viewer = getViewer(graph);
    if (!viewer) return { status: "missing-viewer", slot };

    const selectedNodeId = get().selectedNodeId;
    const selectedNode = selectedNodeId ? graph.nodes[selectedNodeId] : null;
    const viewerNode: NodeModel = { ...viewer, params: { ...viewer.params, active_input: slot } };
    let edges = [...graph.edges];
    let result: ViewerInputResult = { status: "switched", slot };

    if (selectedNode && selectedNode.id !== viewer.id) {
      edges = edges.filter((edge) => !(edge.target_node === viewer.id && edge.target_socket === slot));
      edges.push({
        id: makeViewerEdgeId(viewer.id, slot, selectedNode.id),
        source_node: selectedNode.id,
        source_socket: "out",
        target_node: viewer.id,
        target_socket: slot,
      });
      result = { status: "assigned", slot, nodeId: selectedNode.id };
    }

    set({
      graph: { nodes: { ...graph.nodes, [viewer.id]: viewerNode }, edges },
      renderRevision: get().renderRevision + 1,
    });
    return result;
  },
  deleteSelectedNode: () => {
    const graph = get().graph;
    const selectedNodeId = get().selectedNodeId;
    if (!graph || !selectedNodeId || !graph.nodes[selectedNodeId]) return { status: "nothing-selected" };
    const selectedNode = graph.nodes[selectedNodeId];
    const viewerNodes = Object.values(graph.nodes).filter((node) => node.type.toLowerCase() === "viewer");
    if (selectedNode.type.toLowerCase() === "viewer" && viewerNodes.length <= 1) {
      return { status: "protected-viewer", nodeId: selectedNode.id };
    }

    const incomingFlowEdges = graph.edges.filter((edge) => edge.target_node === selectedNode.id);
    const outgoingFlowEdges = graph.edges.filter((edge) => edge.source_node === selectedNode.id && !isViewerEdge(edge, graph));
    const outgoingViewerEdges = graph.edges.filter((edge) => edge.source_node === selectedNode.id && isViewerEdge(edge, graph));
    let edges = graph.edges.filter((edge) => edge.source_node !== selectedNode.id && edge.target_node !== selectedNode.id);

    if (incomingFlowEdges.length === 1) {
      const incoming = incomingFlowEdges[0];
      for (const outgoing of outgoingFlowEdges) {
        if (incoming.source_node === outgoing.target_node) continue;
        edges.push({
          id: makeEdgeId(incoming.source_node, outgoing.target_node, outgoing.target_socket),
          source_node: incoming.source_node,
          source_socket: incoming.source_socket,
          target_node: outgoing.target_node,
          target_socket: outgoing.target_socket,
        });
      }
      for (const viewerEdge of outgoingViewerEdges) {
        edges = edges.filter(
          (edge) => !(edge.target_node === viewerEdge.target_node && edge.target_socket === viewerEdge.target_socket),
        );
        edges.push({
          id: makeViewerEdgeId(viewerEdge.target_node, viewerEdge.target_socket, incoming.source_node),
          source_node: incoming.source_node,
          source_socket: incoming.source_socket,
          target_node: viewerEdge.target_node,
          target_socket: viewerEdge.target_socket,
        });
      }
    }

    const { [selectedNode.id]: _deletedNode, ...nodes } = graph.nodes;
    set({
      graph: { nodes, edges },
      selectedNodeId: null,
      renderRevision: get().renderRevision + 1,
    });
    return { status: "deleted", nodeId: selectedNode.id };
  },
  setScriptPath: (scriptPath) => set({ scriptPath }),
  addLog: (level, message) =>
    set((state) => ({
      logs: [{ id: crypto.randomUUID(), level, message }, ...state.logs].slice(0, 80),
    })),
}));

function defaultParamsFor(type: string): Record<string, unknown> {
  switch (type) {
    case "Read":
      return {
        path: "E:\\Windows-Shortcuts\\Downloads\\opencomp_studio_codex_docs\\LAL_101_101_0010_####.exr",
        colorspace: "ACES2065-1",
        localization_policy: "from auto-localize path",
        proxy: "",
        proxy_format: "root.proxy_format",
        frame_start: 1001,
        frame_end: 1010,
        before: "hold",
        after: "hold",
        frame_mode: "expression",
        frame: "frame",
        missing_frames: "error",
        input_transform: "default (linear)",
        premultiplied: false,
        raw_data: false,
        auto_alpha: false,
        edge_pixels: "plate detect",
      };
    case "Constant":
      return { width: 1920, height: 1080, r: 0, g: 0, b: 0, a: 1, colorspace: "ACES2065-1" };
    case "Group":
      return { label: "Group", inputs: 1, outputs: 1 };
    case "Grade":
      return { gain: 1, offset: 0, gamma: 1 };
    case "Exposure":
      return { stops: 0 };
    case "Saturation":
      return { saturation: 1 };
    case "Invert":
      return { channels: "rgb" };
    case "Clamp":
      return { min: 0, max: 1, channels: "rgba" };
    case "Colorspace":
      return { src: "ACES2065-1", dst: "ACEScg" };
    case "Blur":
      return { size: 2, channels: "rgba" };
    case "Crop":
      return { extent: "size", x: 0, y: 0, width: 1920, height: 1080, reformat: false, black_outside: true };
    case "Shuffle":
      return { input_b: "rgba", input_a: "none", output_layer: "rgba", out_r: "r", out_g: "g", out_b: "b", out_a: "a" };
    case "Copy":
      return {
        from0: "rgba.alpha",
        to0: "rgba.alpha",
        from1: "none",
        to1: "none",
        from2: "none",
        to2: "none",
        from3: "none",
        to3: "none",
        channels: "none",
        metadata_from: "b",
        mask: "none",
        mix: 1,
      };
    case "ChannelMerge":
      return { a_channel: "rgba.alpha", operation: "union", b_channel: "rgba.alpha", output: "rgba.alpha", mask: "none", mix: 1 };
    case "AddChannels":
      return { channels: "none", channels2: "none", channels3: "none", channels4: "none", color: 0 };
    case "Remove":
      return { operation: "remove", channels: "all", channels2: "none", channels3: "none", channels4: "none" };
    case "Premult":
      return {};
    case "Unpremult":
      return { threshold: 0.000001 };
    case "Cryptomatte":
      return { layer: "", matte_list: "", output: "alpha" };
    case "ModifyMetadata":
      return { action: "set", key: "user/comment", value: "" };
    case "ViewMetadata":
      return {};
    case "CompareMetadata":
      return {};
    case "CopyMetadata":
      return { mode: "all", pattern: "*", prefix: "" };
    case "AddTimeCode":
      return { start_frame: 1001, fps: 24, metadata_key: "input/timecode" };
    case "Reformat":
      return {
        width: 1920,
        height: 1080,
        resize: "distort",
        centered: true,
        preserve_bbox: false,
        pixel_aspect: 1,
        filter: "bilinear",
        black_outside: true,
      };
    case "Scale":
      return { scale: 1 };
    case "Transform":
      return {
        translate_x: 0,
        translate_y: 0,
        scale: 1,
        scale_x: 1,
        scale_y: 1,
        rotate: 0,
        center_x: 960,
        center_y: 540,
        filter: "bilinear",
        clamp: false,
        black_outside: true,
      };
    case "FrameHold":
      return { first_frame: 1001, increment: 0 };
    case "FrameRange":
      return { frame_start: 1001, frame_end: 1010, mode: "hold" };
    case "Retime":
      return { speed: 1, reverse: false, filter: "linear", src_start: 1001, src_end: 1010, warp_points: [] };
    case "ColorCorrect":
      return { saturation: 1, contrast: 1, gamma: 1, gain: 1, offset: 0, mix: 1, clamp: false };
    case "HueCorrect":
      return {
        hue_shift_points: [],
        sat_points: [],
        lum_points: [],
        red_gain_points: [],
        green_gain_points: [],
        blue_gain_points: [],
        red_suppress_points: [],
        green_suppress_points: [],
        blue_suppress_points: [],
        sat_threshold: 0,
        mix: 1,
      };
    case "Merge":
      return {
        operation: "over",
        bbox: "union",
        metadata_from: "b",
        range_from: "b",
        a_channels: "rgba",
        b_channels: "rgba",
        output: "rgba",
        also_merge: "none",
        mask: "none",
        mix: 1,
      };
    case "Write":
      return {
        channels: "rgb",
        path: "renders/output.####.exr",
        proxy: "",
        frame_mode: "expression",
        views: "main",
        file_type: "exr",
        datatype: "16 bit half",
        compression: "Zip (1 scanline)",
        metadata: "default metadata",
        create_directories: true,
        render_order: 1,
        frame_start: 1001,
        frame_end: 1010,
        limit_to_range: false,
        read_file: false,
        missing_frames: "error",
        output_transform: "default (linear)",
        overwrite: true,
      };
    case "Viewer":
      return { active_input: "0" };
    default:
      return {};
  }
}

function inputSocketFor(type: string) {
  const normalized = type.toLowerCase();
  if (normalized === "read" || normalized === "constant" || normalized === "viewer") return null;
  if (normalized === "merge" || normalized === "channelmerge") return "a";
  if (normalized === "copy" || normalized === "shuffle" || normalized === "comparemetadata" || normalized === "copymetadata") return "b";
  if (normalized === "group") return "in";
  return "in";
}

function getViewer(graph: ProjectGraph) {
  return Object.values(graph.nodes).find((node) => node.type.toLowerCase() === "viewer") ?? null;
}

function isViewerEdge(edge: EdgeModel, graph: ProjectGraph) {
  return graph.nodes[edge.target_node]?.type.toLowerCase() === "viewer";
}

function makeEdgeId(sourceNode: string, targetNode: string, targetSocket: string) {
  return `e-${sourceNode}-${targetNode}-${targetSocket}`;
}

function makeViewerEdgeId(viewerNode: string, slot: string, sourceNode: string) {
  return `viewer-${viewerNode}-${slot}-${sourceNode}`;
}

function findOpenPosition(graph: ProjectGraph, desiredPosition: [number, number]): [number, number] {
  let position: [number, number] = [desiredPosition[0], desiredPosition[1]];
  let attempts = 0;
  while (Object.values(graph.nodes).some((node) => overlapsNode(position, node.position)) && attempts < 24) {
    position = [desiredPosition[0], desiredPosition[1] + (attempts + 1) * 84];
    attempts += 1;
  }
  return position;
}

function overlapsNode(position: [number, number], nodePosition: [number, number]) {
  const nodeWidth = 128;
  const nodeHeight = 52;
  const gap = 12;
  return (
    position[0] < nodePosition[0] + nodeWidth + gap &&
    position[0] + nodeWidth + gap > nodePosition[0] &&
    position[1] < nodePosition[1] + nodeHeight + gap &&
    position[1] + nodeHeight + gap > nodePosition[1]
  );
}
