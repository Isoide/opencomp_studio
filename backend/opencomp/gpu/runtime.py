"""Vulkan-backed compute runtime and diagnostics for OpenComp.

This module owns optional Vulkan initialization, shader toolchain discovery,
GPU frame caching, and native compute dispatch for supported node spans. When
host support is incomplete, it reports structured fallback diagnostics instead
of assuming GPU execution is always present.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from opencomp.core.optional_dependencies import import_optional
from opencomp.core.models import ImageFrame, Node, ProjectGraph, ProjectSettings
from opencomp.gpu.toolchain import compiler_warning as _compiler_warning, discover_compiler
from opencomp.io.preview import preview_resize_dimensions
from opencomp.nodes import NODE_DEFINITION_REGISTRY
from opencomp.nodes.base import EvaluationContext, NodeEvaluationError

vk = import_optional("vulkan")


REQUIRED_SHADER_SPECS: dict[str, dict[str, str]] = {
    "grade.comp.glsl": {"stage": "comp", "entry": "main"},
    "colorcorrect.comp.glsl": {"stage": "comp", "entry": "main"},
    "resize.comp.glsl": {"stage": "comp", "entry": "main"},
}
def _api_version_value(major: int, minor: int, patch: int = 0) -> int:
    if vk is not None and hasattr(vk, "VK_MAKE_API_VERSION"):
        return int(vk.VK_MAKE_API_VERSION(0, major, minor, patch))
    return (major << 22) | (minor << 12) | patch


def _api_version_string(value: int) -> str:
    major = (int(value) >> 22) & 0x3FF
    minor = (int(value) >> 12) & 0x3FF
    patch = int(value) & 0xFFF
    return f"{major}.{minor}.{patch}"


def _decode_extension_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").rstrip("\x00")
    return str(value).rstrip("\x00")


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_manifest_path(base_dir: Path, value: object) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return base_dir
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else base_dir / candidate


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


@dataclass(slots=True)
class GpuFrame:
    width: int
    height: int
    colorspace: str
    format_name: str
    frame: int
    storage_key: str
    image: ImageFrame
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpanExecutionMetrics:
    execution_backend: str
    gpu_upload_ms: float
    gpu_dispatch_ms: float
    gpu_download_ms: float
    gpu_resize_ms: float
    gpu_cache_hit: bool
    simulated: bool
    span_nodes: tuple[str, ...]
    kernel_mode: str = "cpu_fallback"

    def as_dict(self) -> dict[str, Any]:
        return {
            "execution_backend": self.execution_backend,
            "gpu_upload_ms": round(self.gpu_upload_ms, 2),
            "gpu_dispatch_ms": round(self.gpu_dispatch_ms, 2),
            "gpu_download_ms": round(self.gpu_download_ms, 2),
            "gpu_resize_ms": round(self.gpu_resize_ms, 2),
            "gpu_cache_hit": self.gpu_cache_hit,
            "gpu_simulated": self.simulated,
            "gpu_span_nodes": list(self.span_nodes),
            "gpu_kernel_mode": self.kernel_mode,
        }


@dataclass(slots=True)
class NativeVulkanContext:
    instance: object
    physical_device: object
    device: object
    queue: object
    command_pool: object
    queue_family_index: int
    device_name: str
    vendor_id: int
    device_type: int
    api_version: int
    driver_version: int
    memory_heaps_mb: list[int]
    device_extensions: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_name": self.device_name,
            "vendor_id": self.vendor_id,
            "device_type": self.device_type,
            "api_version": _api_version_string(self.api_version),
            "driver_version": self.driver_version,
            "queue_family_index": self.queue_family_index,
            "memory_heaps_mb": self.memory_heaps_mb,
            "device_extensions": self.device_extensions,
        }


@dataclass(slots=True)
class NativeComputeAssets:
    descriptor_set_layout: object
    pipeline_layout: object
    pipelines: dict[str, object]
    push_constant_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "pipelines": sorted(self.pipelines.keys()),
            "push_constant_size": self.push_constant_size,
        }


@dataclass(slots=True)
class NativeBuffer:
    buffer: object
    memory: object
    size: int


@dataclass(frozen=True, slots=True)
class ShaderToolchainStatus:
    compiler_name: str | None
    compiler_path: str | None
    source_dir: str
    compiled_dir: str
    manifest_path: str
    ready: bool
    compiled_shaders: list[dict[str, Any]]
    warning: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "compiler_name": self.compiler_name,
            "compiler_path": self.compiler_path,
            "source_dir": self.source_dir,
            "compiled_dir": self.compiled_dir,
            "manifest_path": self.manifest_path,
            "ready": self.ready,
            "compiled_shaders": self.compiled_shaders,
            "warning": self.warning,
            "error": self.error,
        }


class VulkanRuntime:
    def __init__(self, settings: ProjectSettings) -> None:
        self.settings = settings
        self.bindings_available = vk is not None
        self.simulated = os.environ.get("OPENCOMP_VULKAN_SIMULATE", "").strip().lower() in {"1", "true", "yes", "on"}
        self.enabled = settings.execution_backend in {"auto", "vulkan"}
        self.supported_node_types = {"grade", "colorcorrect", "transform", "scale", "reformat"}
        self.native_shader_by_node_type = {
            "grade": "grade.comp.glsl",
            "colorcorrect": "colorcorrect.comp.glsl",
            "scale": "resize.comp.glsl",
        }
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, GpuFrame] = OrderedDict()
        self._cache_bytes = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self.shader_source_dir = Path(__file__).with_name("shaders") / "src"
        self.shader_compiled_dir = Path(__file__).with_name("shaders") / "compiled"
        self.shader_manifest_path = self.shader_compiled_dir / "manifest.json"
        self.shader_toolchain = self._shader_toolchain_status()
        self._shader_modules: dict[str, object] = {}
        self.shader_modules_loaded = False
        self.native_compute_assets: NativeComputeAssets | None = None
        self.compute_pipelines_loaded = False
        self.native_kernels_bound = False
        self.native_context: NativeVulkanContext | None = None
        self.initialization_error: str | None = None
        if self.bindings_available and not self.simulated:
            try:
                self.native_context = self._initialize_native_context()
                if self.shader_toolchain.ready:
                    self._load_shader_modules()
                    self._prepare_compute_assets()
            except Exception as exc:  # pragma: no cover - depends on host Vulkan state
                self.initialization_error = str(exc)
        elif not self.bindings_available:
            self.initialization_error = "Python Vulkan bindings are not installed."
        self.available = self.simulated or self.native_context is not None
        if self.available and self.initialization_error is None:
            self.initialization_error = None

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            return

    def wants_vulkan(self) -> bool:
        return self.settings.execution_backend == "vulkan" or (self.settings.execution_backend == "auto" and self.available)

    def supports_node_type(self, node_type: str) -> bool:
        return node_type.strip().lower() in self.supported_node_types

    @property
    def native_execution_ready(self) -> bool:
        return (
            self.native_context is not None
            and self.shader_toolchain.ready
            and self.shader_modules_loaded
            and self.compute_pipelines_loaded
        )

    def cache_snapshot(self) -> dict[str, Any]:
        with self._lock:
            warning = None
            if self.enabled and not self.available:
                warning = self.initialization_error or "Vulkan execution is unavailable on this host. CPU fallback remains active."
            elif self.shader_toolchain.warning:
                warning = self.shader_toolchain.warning
            return {
                "enabled": self.enabled,
                "available": self.available,
                "bindings_available": self.bindings_available,
                "simulated": self.simulated,
                "native_execution_ready": self.native_execution_ready,
                "shader_modules_loaded": self.shader_modules_loaded,
                "compute_pipelines_loaded": self.compute_pipelines_loaded,
                "native_kernels_bound": self.native_kernels_bound,
                "initialization_error": self.initialization_error,
                "entries": len(self._cache),
                "memory_bytes": self._cache_bytes,
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "supported_node_types": sorted(self.supported_node_types),
                "native_context": self.native_context.as_dict() if self.native_context is not None else None,
                "native_compute_assets": self.native_compute_assets.as_dict() if self.native_compute_assets is not None else None,
                "shader_toolchain": self.shader_toolchain.as_dict(),
                "warning": warning,
            }

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_bytes = 0
            self._cache_hits = 0
            self._cache_misses = 0

    def close(self) -> None:
        with self._lock:
            context = self.native_context
            self.native_context = None
            shader_modules = list(self._shader_modules.values())
            self._shader_modules.clear()
            self.shader_modules_loaded = False
            compute_assets = self.native_compute_assets
            self.native_compute_assets = None
            self.compute_pipelines_loaded = False
            self.native_kernels_bound = False
        if context is None or vk is None:
            return
        if compute_assets is not None:
            for pipeline in compute_assets.pipelines.values():
                try:
                    vk.vkDestroyPipeline(context.device, pipeline, None)
                except Exception:
                    pass
            try:
                vk.vkDestroyPipelineLayout(context.device, compute_assets.pipeline_layout, None)
            except Exception:
                pass
            try:
                vk.vkDestroyDescriptorSetLayout(context.device, compute_assets.descriptor_set_layout, None)
            except Exception:
                pass
        for shader_module in shader_modules:
            try:
                vk.vkDestroyShaderModule(context.device, shader_module, None)
            except Exception:
                pass
        try:
            vk.vkDestroyCommandPool(context.device, context.command_pool, None)
        except Exception:
            pass
        try:
            vk.vkDestroyDevice(context.device, None)
        except Exception:
            pass
        try:
            vk.vkDestroyInstance(context.instance, None)
        except Exception:
            pass

    def execute_span(
        self,
        graph: ProjectGraph,
        nodes: list[Node],
        source_image: ImageFrame,
        context: EvaluationContext,
        cache_key: str | None = None,
        preview_max_width: int | None = None,
        preview_max_height: int | None = None,
    ) -> tuple[ImageFrame, SpanExecutionMetrics]:
        if not self.available or not self.enabled:
            raise RuntimeError("Vulkan runtime is not available.")
        if self._can_execute_natively(nodes):
            return self._execute_native_span(
                nodes,
                source_image,
                cache_key,
                preview_max_width=preview_max_width,
                preview_max_height=preview_max_height,
            )

        upload_started = time.perf_counter()
        gpu_frame, cache_hit = self._upload_frame(source_image, context.frame, cache_key)
        upload_ms = (time.perf_counter() - upload_started) * 1000.0

        dispatch_started = time.perf_counter()
        current = gpu_frame.image
        for node in nodes:
            definition = NODE_DEFINITION_REGISTRY.get(node.type.lower())
            if definition is None:
                raise NodeEvaluationError(node.id, f"Unsupported Vulkan span node type '{node.type}'.")
            current = definition.operation.evaluate(node, {"in": current}, context)
        dispatch_ms = (time.perf_counter() - dispatch_started) * 1000.0

        download_started = time.perf_counter()
        result = self._download_frame(current, nodes[-1].id, cache_key)
        download_ms = (time.perf_counter() - download_started) * 1000.0
        kernel_mode = "native_compute" if self.native_execution_ready and self.native_kernels_bound else "cpu_fallback"

        return result, SpanExecutionMetrics(
            execution_backend="vulkan",
            gpu_upload_ms=upload_ms,
            gpu_dispatch_ms=dispatch_ms,
            gpu_download_ms=download_ms,
            gpu_resize_ms=0.0,
            gpu_cache_hit=cache_hit,
            simulated=kernel_mode != "native_compute",
            span_nodes=tuple(node.id for node in nodes),
            kernel_mode=kernel_mode,
        )

    def _can_execute_natively(self, nodes: list[Node]) -> bool:
        if not (self.native_execution_ready and self.native_kernels_bound):
            return False
        return bool(nodes) and all(node.type.strip().lower() in self.native_shader_by_node_type for node in nodes)

    def _initialize_native_context(self) -> NativeVulkanContext:
        if vk is None:
            raise RuntimeError("Python Vulkan bindings could not be imported.")
        app_info = vk.VkApplicationInfo(
            sType=vk.VK_STRUCTURE_TYPE_APPLICATION_INFO,
            pApplicationName="OpenComp Studio",
            applicationVersion=1,
            pEngineName="OpenComp",
            engineVersion=1,
            apiVersion=_api_version_value(1, 1),
        )
        instance_info = vk.VkInstanceCreateInfo(
            sType=vk.VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
            pApplicationInfo=app_info,
        )
        instance = vk.vkCreateInstance(instance_info, None)
        try:
            physical_devices = list(vk.vkEnumeratePhysicalDevices(instance))
            if not physical_devices:
                raise RuntimeError("No Vulkan physical devices were found.")
            physical_device = self._pick_physical_device(physical_devices)
            props = vk.vkGetPhysicalDeviceProperties(physical_device)
            queue_family_index = self._pick_compute_queue_family(physical_device)
            priorities = [1.0]
            queue_info = vk.VkDeviceQueueCreateInfo(
                sType=vk.VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                queueFamilyIndex=queue_family_index,
                queueCount=1,
                pQueuePriorities=priorities,
            )
            device_info = vk.VkDeviceCreateInfo(
                sType=vk.VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                queueCreateInfoCount=1,
                pQueueCreateInfos=[queue_info],
            )
            device = vk.vkCreateDevice(physical_device, device_info, None)
            try:
                queue = vk.vkGetDeviceQueue(device, queue_family_index, 0)
                command_pool_info = vk.VkCommandPoolCreateInfo(
                    sType=vk.VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                    queueFamilyIndex=queue_family_index,
                    flags=getattr(vk, "VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT", 0),
                )
                command_pool = vk.vkCreateCommandPool(device, command_pool_info, None)
            except Exception:
                vk.vkDestroyDevice(device, None)
                raise
            memory_props = vk.vkGetPhysicalDeviceMemoryProperties(physical_device)
            memory_heaps_mb = []
            for index in range(int(memory_props.memoryHeapCount)):
                heap = memory_props.memoryHeaps[index]
                memory_heaps_mb.append(int(heap.size // (1024 * 1024)))
            extensions = [
                _decode_extension_name(item.extensionName)
                for item in vk.vkEnumerateDeviceExtensionProperties(physical_device, None)
            ]
            return NativeVulkanContext(
                instance=instance,
                physical_device=physical_device,
                device=device,
                queue=queue,
                command_pool=command_pool,
                queue_family_index=queue_family_index,
                device_name=_decode_extension_name(props.deviceName),
                vendor_id=int(props.vendorID),
                device_type=int(props.deviceType),
                api_version=int(props.apiVersion),
                driver_version=int(props.driverVersion),
                memory_heaps_mb=memory_heaps_mb,
                device_extensions=extensions,
            )
        except Exception:
            vk.vkDestroyInstance(instance, None)
            raise

    def _pick_physical_device(self, devices: list[object]) -> object:
        if vk is None:
            return devices[0]
        ranked: list[tuple[int, object]] = []
        discrete = int(getattr(vk, "VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", 2))
        integrated = int(getattr(vk, "VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU", 1))
        for device in devices:
            props = vk.vkGetPhysicalDeviceProperties(device)
            score = 0
            if int(props.deviceType) == discrete:
                score += 100
            elif int(props.deviceType) == integrated:
                score += 50
            score += (int(props.apiVersion) >> 12) & 0x3FF
            ranked.append((score, device))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _pick_compute_queue_family(self, physical_device: object) -> int:
        if vk is None:
            return 0
        queue_families = list(vk.vkGetPhysicalDeviceQueueFamilyProperties(physical_device))
        compute_flag = int(getattr(vk, "VK_QUEUE_COMPUTE_BIT", 0x00000002))
        graphics_flag = int(getattr(vk, "VK_QUEUE_GRAPHICS_BIT", 0x00000001))
        compute_only: list[int] = []
        fallback: list[int] = []
        for index, family in enumerate(queue_families):
            flags = int(family.queueFlags)
            if flags & compute_flag:
                if flags & graphics_flag:
                    fallback.append(index)
                else:
                    compute_only.append(index)
        if compute_only:
            return compute_only[0]
        if fallback:
            return fallback[0]
        raise RuntimeError("No Vulkan compute-capable queue family was found.")

    def _shader_toolchain_status(self) -> ShaderToolchainStatus:
        compiler_name, compiler_path = self._detect_shader_compiler()
        compiled_shaders: list[dict[str, Any]] = []
        error = None
        warning = _compiler_warning(compiler_path)
        ready = False
        diagnostics_root = Path(__file__).resolve().parents[2]
        if self.shader_manifest_path.exists():
            try:
                manifest = json.loads(self.shader_manifest_path.read_text(encoding="utf-8"))
                required = {name: dict(spec) for name, spec in REQUIRED_SHADER_SPECS.items()}
                for item in manifest.get("shaders", []):
                    source_path = _resolve_manifest_path(self.shader_source_dir, item.get("source"))
                    output_path = _resolve_manifest_path(self.shader_compiled_dir, item.get("output"))
                    source_hash_expected = item.get("source_sha256")
                    source_hash_actual = _source_sha256(source_path) if source_path.exists() and source_path.is_file() else None
                    source_key = source_path.name
                    exists = output_path.exists()
                    compiled_shaders.append(
                        {
                            "source": _display_path(source_path, diagnostics_root),
                            "output": _display_path(output_path, diagnostics_root),
                            "entry": item.get("entry", "main"),
                            "stage": item.get("stage", "compute"),
                            "exists": exists,
                            "source_sha256": source_hash_expected,
                            "source_sha256_matches": source_hash_expected is None or source_hash_expected == source_hash_actual,
                        }
                    )
                    spec = required.get(source_key)
                    if spec is not None:
                        spec["found"] = "true"
                        spec["exists"] = "true" if exists else "false"
                        spec["hash_matches"] = "true" if source_hash_expected is None or source_hash_expected == source_hash_actual else "false"
                        spec["entry_matches"] = "true" if item.get("entry", "main") == spec["entry"] else "false"
                        spec["stage_matches"] = "true" if item.get("stage", "compute") == spec["stage"] else "false"
                missing = [name for name, spec in required.items() if spec.get("found") != "true"]
                invalid = [
                    name
                    for name, spec in required.items()
                    if spec.get("found") == "true"
                    and (
                        spec.get("exists") != "true"
                        or spec.get("hash_matches") != "true"
                        or spec.get("entry_matches") != "true"
                        or spec.get("stage_matches") != "true"
                    )
                ]
                ready = not missing and not invalid
                if missing or invalid:
                    details = []
                    if missing:
                        details.append(f"missing required shaders: {', '.join(sorted(missing))}")
                    if invalid:
                        details.append(f"invalid shader entries: {', '.join(sorted(invalid))}")
                    error = "; ".join(details)
            except Exception as exc:
                error = f"Failed to read shader manifest: {exc}"
        return ShaderToolchainStatus(
            compiler_name=compiler_name,
            compiler_path=compiler_path,
            source_dir=_display_path(self.shader_source_dir, diagnostics_root),
            compiled_dir=_display_path(self.shader_compiled_dir, diagnostics_root),
            manifest_path=_display_path(self.shader_manifest_path, diagnostics_root),
            ready=ready,
            compiled_shaders=compiled_shaders,
            warning=warning,
            error=error,
        )

    def _detect_shader_compiler(self) -> tuple[str | None, str | None]:
        compiler = discover_compiler()
        if compiler is None:
            return None, None
        return Path(compiler.path).name, compiler.path

    def _load_shader_modules(self) -> None:
        if self.native_context is None or vk is None:
            return
        modules: dict[str, object] = {}
        try:
            for shader_name in REQUIRED_SHADER_SPECS:
                output_path = self.shader_compiled_dir / shader_name.replace(".glsl", ".spv")
                if not output_path.exists():
                    raise FileNotFoundError(f"Compiled shader is missing: {output_path}")
                binary = output_path.read_bytes()
                words = array.array("I")
                words.frombytes(binary)
                create_info = vk.VkShaderModuleCreateInfo(
                    sType=vk.VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                    codeSize=len(binary),
                    pCode=words,
                )
                modules[shader_name] = vk.vkCreateShaderModule(self.native_context.device, create_info, None)
        except Exception:
            for module in modules.values():
                try:
                    vk.vkDestroyShaderModule(self.native_context.device, module, None)
                except Exception:
                    pass
            raise
        with self._lock:
            self._shader_modules = modules
            self.shader_modules_loaded = True

    def _prepare_compute_assets(self) -> None:
        if self.native_context is None or vk is None:
            return
        compute_flag = int(getattr(vk, "VK_SHADER_STAGE_COMPUTE_BIT", 0x00000020))
        storage_buffer = int(getattr(vk, "VK_DESCRIPTOR_TYPE_STORAGE_BUFFER", 7))
        descriptor_layout = None
        pipeline_layout = None
        pipelines: dict[str, object] = {}
        push_constant_size = 48
        try:
            bindings = [
                vk.VkDescriptorSetLayoutBinding(binding=0, descriptorType=storage_buffer, descriptorCount=1, stageFlags=compute_flag),
                vk.VkDescriptorSetLayoutBinding(binding=1, descriptorType=storage_buffer, descriptorCount=1, stageFlags=compute_flag),
            ]
            descriptor_info = vk.VkDescriptorSetLayoutCreateInfo(
                sType=vk.VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
                bindingCount=len(bindings),
                pBindings=bindings,
            )
            descriptor_layout = vk.vkCreateDescriptorSetLayout(self.native_context.device, descriptor_info, None)
            push_range = vk.VkPushConstantRange(stageFlags=compute_flag, offset=0, size=push_constant_size)
            pipeline_layout_info = vk.VkPipelineLayoutCreateInfo(
                sType=vk.VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
                setLayoutCount=1,
                pSetLayouts=[descriptor_layout],
                pushConstantRangeCount=1,
                pPushConstantRanges=[push_range],
            )
            pipeline_layout = vk.vkCreatePipelineLayout(self.native_context.device, pipeline_layout_info, None)
            for shader_name, module in self._shader_modules.items():
                stage_info = vk.VkPipelineShaderStageCreateInfo(
                    sType=vk.VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                    stage=compute_flag,
                    module=module,
                    pName="main",
                )
                pipeline_info = vk.VkComputePipelineCreateInfo(
                    sType=vk.VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
                    stage=stage_info,
                    layout=pipeline_layout,
                )
                result = vk.vkCreateComputePipelines(self.native_context.device, None, 1, [pipeline_info], None)
                pipelines[shader_name] = result[0]
        except Exception:
            for pipeline in pipelines.values():
                try:
                    vk.vkDestroyPipeline(self.native_context.device, pipeline, None)
                except Exception:
                    pass
            if pipeline_layout is not None:
                try:
                    vk.vkDestroyPipelineLayout(self.native_context.device, pipeline_layout, None)
                except Exception:
                    pass
            if descriptor_layout is not None:
                try:
                    vk.vkDestroyDescriptorSetLayout(self.native_context.device, descriptor_layout, None)
                except Exception:
                    pass
            raise
        with self._lock:
            self.native_compute_assets = NativeComputeAssets(
                descriptor_set_layout=descriptor_layout,
                pipeline_layout=pipeline_layout,
                pipelines=pipelines,
                push_constant_size=push_constant_size,
            )
            self.compute_pipelines_loaded = True
            self.native_kernels_bound = all(shader_name in pipelines for shader_name in REQUIRED_SHADER_SPECS)

    def _execute_native_span(
        self,
        nodes: list[Node],
        source_image: ImageFrame,
        cache_key: str | None,
        *,
        preview_max_width: int | None = None,
        preview_max_height: int | None = None,
    ) -> tuple[ImageFrame, SpanExecutionMetrics]:
        if self.native_context is None or self.native_compute_assets is None or vk is None:
            raise RuntimeError("Native Vulkan compute assets are not available.")
        rgba = self._native_rgba_array(source_image)
        buffer_size = int(rgba.nbytes)
        if buffer_size <= 0:
            raise RuntimeError("Cannot execute a Vulkan span for an empty image buffer.")

        upload_started = time.perf_counter()
        src_buffer = self._create_storage_buffer(buffer_size)
        dst_buffer = self._create_storage_buffer(buffer_size)
        allocated_buffers: list[NativeBuffer] = [src_buffer, dst_buffer]
        descriptor_pool = None
        descriptor_set = None
        command_buffer = None
        try:
            self._write_buffer(src_buffer, rgba)
            descriptor_pool, descriptor_set = self._create_descriptor_resources()
            command_buffer = self._allocate_command_buffer()
            upload_ms = (time.perf_counter() - upload_started) * 1000.0

            dispatch_started = time.perf_counter()
            src_current = src_buffer
            dst_current = dst_buffer
            current_width = source_image.width
            current_height = source_image.height
            self._begin_command_buffer(command_buffer)
            pending_steps = len(nodes) + (1 if preview_max_width is not None or preview_max_height is not None else 0)
            completed_steps = 0
            for node in nodes:
                src_current, dst_current, current_width, current_height = self._record_native_node_dispatch(
                    command_buffer,
                    node,
                    descriptor_set,
                    src_current,
                    dst_current,
                    current_width,
                    current_height,
                    allocated_buffers,
                )
                completed_steps += 1
                if completed_steps < pending_steps:
                    self._record_compute_barrier(command_buffer)
            resize_ms = 0.0
            target_size = preview_resize_dimensions(source_image.width, source_image.height, preview_max_width, preview_max_height)
            if target_size is not None:
                target_width, target_height = target_size
                resize_started = time.perf_counter()
                dst_current = self._ensure_buffer_capacity(dst_current, target_width, target_height, allocated_buffers)
                self._update_descriptor_set(descriptor_set, src_current, dst_current)
                push_constants = self._pack_resize_push_constants(current_width, current_height, target_width, target_height)
                self._record_compute_dispatch(command_buffer, "resize.comp.glsl", descriptor_set, push_constants, target_width, target_height)
                src_current, dst_current = dst_current, src_current
                current_width = target_width
                current_height = target_height
                resize_ms = (time.perf_counter() - resize_started) * 1000.0
                completed_steps += 1
            if completed_steps > 0:
                self._record_host_read_barrier(command_buffer)
            self._submit_command_buffer(command_buffer)
            dispatch_ms = (time.perf_counter() - dispatch_started) * 1000.0 - resize_ms

            download_started = time.perf_counter()
            result_shape = (current_height, current_width, 4)
            result_data = self._read_buffer(src_current, result_shape)
            download_ms = (time.perf_counter() - download_started) * 1000.0
        finally:
            if command_buffer is not None:
                self._free_command_buffer(command_buffer)
            if descriptor_pool is not None:
                try:
                    vk.vkDestroyDescriptorPool(self.native_context.device, descriptor_pool, None)
                except Exception:
                    pass
            destroyed: set[int] = set()
            for resource in allocated_buffers:
                marker = id(resource)
                if marker in destroyed:
                    continue
                destroyed.add(marker)
                self._destroy_buffer(resource)

        result = ImageFrame(
            width=current_width,
            height=current_height,
            data=result_data,
            channels=source_image.channels,
            channel_data=source_image.copy_channel_data() if current_width == source_image.width and current_height == source_image.height else {},
            pixel_aspect=source_image.pixel_aspect,
            colorspace=source_image.colorspace,
            frame=source_image.frame,
            metadata={
                **source_image.metadata,
                "gpu/backend": "vulkan",
                "gpu/simulated": False,
                "gpu/kernel_mode": "native_compute",
                "gpu/cache_key": cache_key,
                "gpu/node": nodes[-1].id,
                "gpu/resize_ms": resize_ms,
                "gpu/source_width": source_image.width,
                "gpu/source_height": source_image.height,
                "source_width": source_image.width,
                "source_height": source_image.height,
            },
            format_bbox=source_image.format_bbox if current_width == source_image.width and current_height == source_image.height else None,
            data_window=source_image.data_window if current_width == source_image.width and current_height == source_image.height else None,
        )
        return result, SpanExecutionMetrics(
            execution_backend="vulkan",
            gpu_upload_ms=upload_ms,
            gpu_dispatch_ms=max(dispatch_ms, 0.0),
            gpu_download_ms=download_ms,
            gpu_resize_ms=resize_ms,
            gpu_cache_hit=False,
            simulated=False,
            span_nodes=tuple(node.id for node in nodes),
            kernel_mode="native_compute",
        )

    def _record_native_node_dispatch(
        self,
        command_buffer: object,
        node: Node,
        descriptor_set: object,
        src_buffer: NativeBuffer,
        dst_buffer: NativeBuffer,
        current_width: int,
        current_height: int,
        allocated_buffers: list[NativeBuffer],
    ) -> tuple[NativeBuffer, NativeBuffer, int, int]:
        node_type = node.type.strip().lower()
        shader_name = self.native_shader_by_node_type.get(node_type)
        if shader_name is None:
            raise RuntimeError(f"Node type '{node.type}' does not have a native Vulkan kernel.")
        target_width = current_width
        target_height = current_height
        if node_type == "scale":
            target_width, target_height = self._scale_target_dimensions(node, current_width, current_height)
        dst_buffer = self._ensure_buffer_capacity(dst_buffer, target_width, target_height, allocated_buffers)
        self._update_descriptor_set(descriptor_set, src_buffer, dst_buffer)
        if node_type == "scale":
            push_constants = self._pack_resize_push_constants(current_width, current_height, target_width, target_height)
        else:
            push_constants = self._pack_push_constants(node, current_width, current_height)
        self._record_compute_dispatch(command_buffer, shader_name, descriptor_set, push_constants, target_width, target_height)
        return dst_buffer, src_buffer, target_width, target_height

    def _native_rgba_array(self, image: ImageFrame) -> np.ndarray:
        data = image.data
        if data.ndim != 3:
            raise RuntimeError(f"Unsupported native image layout {data.shape!r}; expected HxWxC.")
        if data.shape[2] == 4:
            return np.ascontiguousarray(data, dtype=np.float32)
        if data.shape[2] == 3:
            rgba = np.empty((image.height, image.width, 4), dtype=np.float32)
            rgba[:, :, :3] = data.astype(np.float32, copy=False)
            rgba[:, :, 3] = 1.0
            return np.ascontiguousarray(rgba)
        raise RuntimeError(f"Unsupported native channel count {data.shape[2]}; expected RGB or RGBA.")

    def _find_memory_type(self, type_bits: int, required_flags: int) -> int:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        props = vk.vkGetPhysicalDeviceMemoryProperties(self.native_context.physical_device)
        for index in range(int(props.memoryTypeCount)):
            type_matches = (type_bits & (1 << index)) != 0
            flags = int(props.memoryTypes[index].propertyFlags)
            if type_matches and (flags & required_flags) == required_flags:
                return index
        raise RuntimeError("No compatible Vulkan memory type was found for host-visible storage buffers.")

    def _required_buffer_size(self, width: int, height: int) -> int:
        return int(max(1, int(width)) * max(1, int(height)) * 4 * np.dtype(np.float32).itemsize)

    def _create_storage_buffer(self, size: int) -> NativeBuffer:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        create_info = vk.VkBufferCreateInfo(
            sType=vk.VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
            size=size,
            usage=vk.VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
            sharingMode=vk.VK_SHARING_MODE_EXCLUSIVE,
        )
        buffer = vk.vkCreateBuffer(self.native_context.device, create_info, None)
        try:
            requirements = vk.vkGetBufferMemoryRequirements(self.native_context.device, buffer)
            memory_type_index = self._find_memory_type(
                int(requirements.memoryTypeBits),
                int(vk.VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | vk.VK_MEMORY_PROPERTY_HOST_COHERENT_BIT),
            )
            alloc_info = vk.VkMemoryAllocateInfo(
                sType=vk.VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                allocationSize=int(requirements.size),
                memoryTypeIndex=memory_type_index,
            )
            memory = vk.vkAllocateMemory(self.native_context.device, alloc_info, None)
            vk.vkBindBufferMemory(self.native_context.device, buffer, memory, 0)
        except Exception:
            vk.vkDestroyBuffer(self.native_context.device, buffer, None)
            raise
        return NativeBuffer(buffer=buffer, memory=memory, size=size)

    def _ensure_buffer_capacity(
        self,
        resource: NativeBuffer,
        width: int,
        height: int,
        allocated_buffers: list[NativeBuffer],
    ) -> NativeBuffer:
        required_size = self._required_buffer_size(width, height)
        if resource.size >= required_size:
            return resource
        replacement = self._create_storage_buffer(required_size)
        allocated_buffers.append(replacement)
        return replacement

    def _destroy_buffer(self, resource: NativeBuffer | None) -> None:
        if resource is None or self.native_context is None or vk is None:
            return
        try:
            vk.vkDestroyBuffer(self.native_context.device, resource.buffer, None)
        except Exception:
            pass
        try:
            vk.vkFreeMemory(self.native_context.device, resource.memory, None)
        except Exception:
            pass

    def _write_buffer(self, resource: NativeBuffer, rgba: np.ndarray) -> None:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        mapped = vk.vkMapMemory(self.native_context.device, resource.memory, 0, resource.size, 0)
        try:
            memoryview(mapped)[: resource.size] = rgba.tobytes(order="C")
        finally:
            vk.vkUnmapMemory(self.native_context.device, resource.memory)

    def _read_buffer(self, resource: NativeBuffer, shape: tuple[int, ...]) -> np.ndarray:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        mapped = vk.vkMapMemory(self.native_context.device, resource.memory, 0, resource.size, 0)
        try:
            return np.frombuffer(memoryview(mapped), dtype=np.float32, count=int(np.prod(shape))).copy().reshape(shape)
        finally:
            vk.vkUnmapMemory(self.native_context.device, resource.memory)

    def _create_descriptor_resources(self) -> tuple[object, object]:
        if self.native_context is None or self.native_compute_assets is None or vk is None:
            raise RuntimeError("Native Vulkan compute assets are not available.")
        pool_sizes = [vk.VkDescriptorPoolSize(type=vk.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, descriptorCount=2)]
        pool_info = vk.VkDescriptorPoolCreateInfo(
            sType=vk.VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
            maxSets=1,
            poolSizeCount=1,
            pPoolSizes=pool_sizes,
        )
        descriptor_pool = vk.vkCreateDescriptorPool(self.native_context.device, pool_info, None)
        alloc_info = vk.VkDescriptorSetAllocateInfo(
            sType=vk.VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
            descriptorPool=descriptor_pool,
            descriptorSetCount=1,
            pSetLayouts=[self.native_compute_assets.descriptor_set_layout],
        )
        descriptor_set = vk.vkAllocateDescriptorSets(self.native_context.device, alloc_info)[0]
        return descriptor_pool, descriptor_set

    def _update_descriptor_set(self, descriptor_set: object, src_buffer: NativeBuffer, dst_buffer: NativeBuffer) -> None:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        src_info = vk.VkDescriptorBufferInfo(buffer=src_buffer.buffer, offset=0, range=src_buffer.size)
        dst_info = vk.VkDescriptorBufferInfo(buffer=dst_buffer.buffer, offset=0, range=dst_buffer.size)
        writes = [
            vk.VkWriteDescriptorSet(
                sType=vk.VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
                dstSet=descriptor_set,
                dstBinding=0,
                descriptorCount=1,
                descriptorType=vk.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                pBufferInfo=[src_info],
            ),
            vk.VkWriteDescriptorSet(
                sType=vk.VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
                dstSet=descriptor_set,
                dstBinding=1,
                descriptorCount=1,
                descriptorType=vk.VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
                pBufferInfo=[dst_info],
            ),
        ]
        vk.vkUpdateDescriptorSets(self.native_context.device, len(writes), writes, 0, None)

    def _pack_push_constants(self, node: Node, width: int, height: int) -> bytes:
        node_type = node.type.strip().lower()
        if node_type == "grade":
            gain = float(node.params.get("gain", node.params.get("multiply", 1.0)))
            multiply = float(node.params.get("multiply", 1.0))
            offset = float(node.params.get("offset", node.params.get("add", 0.0)))
            add = float(node.params.get("add", 0.0))
            gamma = max(float(node.params.get("gamma", 1.0)), 1e-6)
            return struct.pack("<II6f", width, height, gain, multiply, offset, add, 1.0 / gamma, 0.0)
        if node_type == "colorcorrect":
            saturation = float(node.params.get("saturation", 1.0))
            contrast = float(node.params.get("contrast", 1.0))
            gamma = max(float(node.params.get("gamma", 1.0)), 1e-6)
            gain = float(node.params.get("gain", 1.0))
            offset = float(node.params.get("offset", 0.0))
            mix = float(node.params.get("mix", 1.0))
            clamp = 1.0 if self._truthy(node.params.get("clamp", False)) else 0.0
            return struct.pack("<II8f", width, height, saturation, contrast, 1.0 / gamma, gain, offset, mix, clamp, 0.0)
        raise RuntimeError(f"Native push-constant packing is not implemented for node type '{node.type}'.")

    def _pack_resize_push_constants(self, source_width: int, source_height: int, target_width: int, target_height: int) -> bytes:
        return struct.pack("<IIII", source_width, source_height, target_width, target_height)

    def _scale_target_dimensions(self, node: Node, width: int, height: int) -> tuple[int, int]:
        scale = float(node.params.get("scale") or 1.0)
        if scale <= 0.0:
            raise RuntimeError(f"Scale node '{node.id}' must be greater than zero for native Vulkan execution.")
        return max(1, int(round(width * scale))), max(1, int(round(height * scale)))

    def _allocate_command_buffer(self) -> object:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        alloc_info = vk.VkCommandBufferAllocateInfo(
            sType=vk.VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
            commandPool=self.native_context.command_pool,
            level=vk.VK_COMMAND_BUFFER_LEVEL_PRIMARY,
            commandBufferCount=1,
        )
        return vk.vkAllocateCommandBuffers(self.native_context.device, alloc_info)[0]

    def _free_command_buffer(self, command_buffer: object) -> None:
        if self.native_context is None or vk is None:
            return
        try:
            vk.vkFreeCommandBuffers(self.native_context.device, self.native_context.command_pool, 1, [command_buffer])
        except Exception:
            pass

    def _begin_command_buffer(self, command_buffer: object) -> None:
        if vk is None:
            raise RuntimeError("Native Vulkan bindings are not available.")
        begin_info = vk.VkCommandBufferBeginInfo(sType=vk.VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO)
        vk.vkBeginCommandBuffer(command_buffer, begin_info)

    def _submit_command_buffer(self, command_buffer: object) -> None:
        if self.native_context is None or vk is None:
            raise RuntimeError("Native Vulkan context is not available.")
        vk.vkEndCommandBuffer(command_buffer)
        submit_info = vk.VkSubmitInfo(
            sType=vk.VK_STRUCTURE_TYPE_SUBMIT_INFO,
            commandBufferCount=1,
            pCommandBuffers=[command_buffer],
        )
        vk.vkQueueSubmit(self.native_context.queue, 1, [submit_info], vk.VK_NULL_HANDLE)
        vk.vkQueueWaitIdle(self.native_context.queue)

    def _record_compute_dispatch(
        self,
        command_buffer: object,
        shader_name: str,
        descriptor_set: object,
        push_constants: bytes,
        width: int,
        height: int,
    ) -> None:
        if self.native_context is None or self.native_compute_assets is None or vk is None:
            raise RuntimeError("Native Vulkan compute assets are not available.")
        pipeline = self.native_compute_assets.pipelines.get(shader_name)
        if pipeline is None:
            raise RuntimeError(f"Required Vulkan pipeline '{shader_name}' is not loaded.")
        vk.vkCmdBindPipeline(command_buffer, vk.VK_PIPELINE_BIND_POINT_COMPUTE, pipeline)
        vk.vkCmdBindDescriptorSets(
            command_buffer,
            vk.VK_PIPELINE_BIND_POINT_COMPUTE,
            self.native_compute_assets.pipeline_layout,
            0,
            1,
            [descriptor_set],
            0,
            None,
        )
        vk.vkCmdPushConstants(
            command_buffer,
            self.native_compute_assets.pipeline_layout,
            vk.VK_SHADER_STAGE_COMPUTE_BIT,
            0,
            len(push_constants),
            vk.ffi.from_buffer(push_constants),
        )
        group_x = max(1, (int(width) + 15) // 16)
        group_y = max(1, (int(height) + 15) // 16)
        vk.vkCmdDispatch(command_buffer, group_x, group_y, 1)

    def _record_compute_barrier(self, command_buffer: object) -> None:
        if vk is None:
            raise RuntimeError("Native Vulkan bindings are not available.")
        shader_write = int(getattr(vk, "VK_ACCESS_SHADER_WRITE_BIT", 0x00000040))
        shader_read = int(getattr(vk, "VK_ACCESS_SHADER_READ_BIT", 0x00000020))
        compute_stage = int(getattr(vk, "VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT", 0x00000800))
        memory_barrier = vk.VkMemoryBarrier(
            sType=vk.VK_STRUCTURE_TYPE_MEMORY_BARRIER,
            srcAccessMask=shader_write,
            dstAccessMask=shader_read | shader_write,
        )
        vk.vkCmdPipelineBarrier(
            command_buffer,
            compute_stage,
            compute_stage,
            0,
            1,
            [memory_barrier],
            0,
            None,
            0,
            None,
        )

    def _record_host_read_barrier(self, command_buffer: object) -> None:
        if vk is None:
            raise RuntimeError("Native Vulkan bindings are not available.")
        shader_write = int(getattr(vk, "VK_ACCESS_SHADER_WRITE_BIT", 0x00000040))
        host_read = int(getattr(vk, "VK_ACCESS_HOST_READ_BIT", 0x00002000))
        compute_stage = int(getattr(vk, "VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT", 0x00000800))
        host_stage = int(getattr(vk, "VK_PIPELINE_STAGE_HOST_BIT", 0x00004000))
        memory_barrier = vk.VkMemoryBarrier(
            sType=vk.VK_STRUCTURE_TYPE_MEMORY_BARRIER,
            srcAccessMask=shader_write,
            dstAccessMask=host_read,
        )
        vk.vkCmdPipelineBarrier(
            command_buffer,
            compute_stage,
            host_stage,
            0,
            1,
            [memory_barrier],
            0,
            None,
            0,
            None,
        )

    def _truthy(self, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _upload_frame(self, image: ImageFrame, frame: int, cache_key: str | None) -> tuple[GpuFrame, bool]:
        if cache_key:
            with self._lock:
                cached = self._cache.get(cache_key)
                if cached is not None:
                    self._cache.move_to_end(cache_key)
                    self._cache_hits += 1
                    return cached, True
                self._cache_misses += 1
        copied = ImageFrame(
            width=image.width,
            height=image.height,
            data=image.data.copy(),
            channels=image.channels,
            channel_data=image.copy_channel_data(),
            pixel_aspect=image.pixel_aspect,
            colorspace=image.colorspace,
            frame=image.frame,
            metadata=dict(image.metadata),
            format_bbox=image.format_bbox,
            data_window=image.data_window,
        )
        gpu_frame = GpuFrame(
            width=copied.width,
            height=copied.height,
            colorspace=copied.colorspace,
            format_name="rgba32f",
            frame=frame,
            storage_key=cache_key or "",
            image=copied,
            metadata={
                "gpu_simulated": not (self.native_execution_ready and self.native_kernels_bound),
                "gpu_native_ready": self.native_execution_ready,
                "gpu_native_kernels_bound": self.native_kernels_bound,
            },
        )
        if cache_key:
            with self._lock:
                self._cache[cache_key] = gpu_frame
                self._cache_bytes += copied.data.nbytes
                self._prune_cache()
        return gpu_frame, False

    def _download_frame(self, image: ImageFrame, node_id: str, cache_key: str | None) -> ImageFrame:
        result = ImageFrame(
            width=image.width,
            height=image.height,
            data=image.data.copy(),
            channels=image.channels,
            channel_data=image.copy_channel_data(),
            pixel_aspect=image.pixel_aspect,
            colorspace=image.colorspace,
            frame=image.frame,
            metadata={
                **image.metadata,
                "gpu/backend": "vulkan",
                "gpu/simulated": not (self.native_execution_ready and self.native_kernels_bound),
                "gpu/kernel_mode": "native_compute" if self.native_execution_ready and self.native_kernels_bound else "cpu_fallback",
                "gpu/cache_key": cache_key,
                "gpu/node": node_id,
            },
            format_bbox=image.format_bbox,
            data_window=image.data_window,
        )
        return result

    def _prune_cache(self) -> None:
        max_bytes = max(0, int(self.settings.gpu_memory_limit_mb) * 1024 * 1024)
        if max_bytes <= 0:
            self._cache.clear()
            self._cache_bytes = 0
            return
        while self._cache_bytes > max_bytes and self._cache:
            _key, entry = self._cache.popitem(last=False)
            self._cache_bytes -= entry.image.data.nbytes
