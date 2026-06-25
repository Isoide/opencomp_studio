import type { CacheStatus } from "./api/client";

export function gpuSupportedNodeTypes(status: CacheStatus | null | undefined): string[] {
  return status?.gpu_runtime?.supported_node_types ?? [];
}

export function gpuRuntimeAvailable(status: CacheStatus | null | undefined): boolean {
  return Boolean(status?.gpu_runtime?.available);
}

export function foregroundRuntimeNodeIds(status: CacheStatus): string[] {
  return status.node_activity?.foreground_active_nodes ?? status.active_nodes ?? [];
}

export function backgroundRuntimeNodeIds(status: CacheStatus): string[] {
  return status.node_activity?.background_active_nodes ?? [];
}

export function lastRequestTiming(status: CacheStatus): CacheStatus["last_request_timing"] {
  return status.last_request_timing;
}

