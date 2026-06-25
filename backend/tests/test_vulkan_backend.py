import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from opencomp.app import app
from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings
from opencomp.core.preview_renderer import PreviewRequest, get_float_preview
from opencomp.gpu.runtime import VulkanRuntime, _compiler_warning, _source_sha256
from opencomp.core.render_contract import RenderRequest
from opencomp.io.preview import preview_rgba_for_channel, resize_float_rgba


def _vulkan_test_graph() -> ProjectGraph:
    return ProjectGraph(
        nodes={
            "Constant1": Node(
                id="Constant1",
                type="Constant",
                params={"width": 16, "height": 8, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0},
            ),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.1, "offset": 0.05}),
            "ColorCorrect1": Node(
                id="ColorCorrect1",
                type="ColorCorrect",
                params={"saturation": 1.05, "contrast": 1.02, "gamma": 1.01, "gain": 1.0, "offset": 0.0, "mix": 1.0},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="constant-grade", source_node="Constant1", target_node="Grade1", target_socket="in"),
            Edge(id="grade-colorcorrect", source_node="Grade1", target_node="ColorCorrect1", target_socket="in"),
            Edge(id="colorcorrect-viewer", source_node="ColorCorrect1", target_node="Viewer1", target_socket="0"),
        ],
    )


def _vulkan_scale_graph() -> ProjectGraph:
    return ProjectGraph(
        nodes={
            "Constant1": Node(
                id="Constant1",
                type="Constant",
                params={"width": 16, "height": 8, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0},
            ),
            "Scale1": Node(id="Scale1", type="Scale", params={"scale": 0.5}),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="constant-scale", source_node="Constant1", target_node="Scale1", target_socket="in"),
            Edge(id="scale-viewer", source_node="Scale1", target_node="Viewer1", target_socket="0"),
        ],
    )


def _vulkan_mixed_scale_graph() -> ProjectGraph:
    return ProjectGraph(
        nodes={
            "Constant1": Node(
                id="Constant1",
                type="Constant",
                params={"width": 16, "height": 8, "r": 0.2, "g": 0.4, "b": 0.6, "a": 1.0},
            ),
            "Grade1": Node(id="Grade1", type="Grade", params={"gain": 1.1, "offset": 0.05}),
            "Scale1": Node(id="Scale1", type="Scale", params={"scale": 0.5}),
            "ColorCorrect1": Node(
                id="ColorCorrect1",
                type="ColorCorrect",
                params={"saturation": 1.05, "contrast": 1.02, "gamma": 1.01, "gain": 1.0, "offset": 0.0, "mix": 1.0},
            ),
            "Viewer1": Node(id="Viewer1", type="Viewer", params={"active_input": "0"}),
        },
        edges=[
            Edge(id="constant-grade", source_node="Constant1", target_node="Grade1", target_socket="in"),
            Edge(id="grade-scale", source_node="Grade1", target_node="Scale1", target_socket="in"),
            Edge(id="scale-colorcorrect", source_node="Scale1", target_node="ColorCorrect1", target_socket="in"),
            Edge(id="colorcorrect-viewer", source_node="ColorCorrect1", target_node="Viewer1", target_socket="0"),
        ],
    )


def test_execution_plan_groups_vulkan_supported_span(monkeypatch) -> None:
    monkeypatch.setenv("OPENCOMP_VULKAN_SIMULATE", "1")
    graph = _vulkan_test_graph()
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, execution_backend="vulkan"))
    request = RenderRequest(node_id="Viewer1", frame=1001, channels=["rgba"], precision="float16", storage="frontend")

    plan = evaluator.execution_plan_for(graph, request, eval_node_id="Viewer1", output_signature="viewer:1001")

    assert [span.backend for span in plan.spans] == ["cpu", "vulkan", "cpu"]
    assert plan.spans[1].node_ids == ("Grade1", "ColorCorrect1")


def test_simulated_vulkan_execution_records_gpu_metadata(monkeypatch) -> None:
    monkeypatch.setenv("OPENCOMP_VULKAN_SIMULATE", "1")
    graph = _vulkan_test_graph()
    evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, execution_backend="vulkan"))

    image = evaluator.evaluate_node(graph, "ColorCorrect1", 1001)

    assert image.metadata["gpu/backend"] == "vulkan"
    assert image.metadata["gpu/simulated"] is True
    assert any(phase["phase"] == "gpu.dispatch" for phase in evaluator.phase_timings)
    snapshot = evaluator.cache_snapshot()
    assert snapshot["gpu_runtime"]["simulated"] is True
    assert snapshot["gpu_runtime"]["misses"] >= 1
    assert np.isfinite(float(image.metadata["gpu/dispatch_ms"]))


def test_http_viewer_request_reports_vulkan_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("OPENCOMP_VULKAN_SIMULATE", "1")
    client = TestClient(app)
    project = client.post("/api/projects/new").json()
    project["settings"]["execution_backend"] = "vulkan"
    client.put("/api/projects/settings", json={"settings": project["settings"]})
    client.put("/api/graph", json={"graph": _vulkan_test_graph().model_dump()})

    response = client.post("/api/viewer/frame", json={"node_id": "Viewer1", "frame": 1001})

    assert response.status_code == 200
    status = client.get("/api/cache/status").json()
    assert status["gpu_runtime"]["simulated"] is True
    assert status["last_request_timing"]["execution_backend"] == "vulkan"
    assert "gpu_dispatch_ms" in status["last_request_timing"]


def test_compiler_warning_marks_houdini_as_fallback() -> None:
    assert _compiler_warning(r"C:\Program Files\Side Effects Software\Houdini 20.0.547\bin\glslangValidator.exe")
    assert _compiler_warning(r"C:\VulkanSDK\1.4.350.0\Bin\glslang.exe") is None


def test_shader_toolchain_status_accepts_relative_manifest_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENCOMP_VULKAN_SIMULATE", "1")
    runtime = VulkanRuntime(ProjectSettings(cache_enabled=True, execution_backend="vulkan"))
    runtime.shader_source_dir = tmp_path / "src"
    runtime.shader_compiled_dir = tmp_path / "compiled"
    runtime.shader_manifest_path = runtime.shader_compiled_dir / "manifest.json"
    runtime.shader_source_dir.mkdir(parents=True)
    runtime.shader_compiled_dir.mkdir(parents=True)

    grade_source = runtime.shader_source_dir / "grade.comp.glsl"
    color_source = runtime.shader_source_dir / "colorcorrect.comp.glsl"
    resize_source = runtime.shader_source_dir / "resize.comp.glsl"
    grade_source.write_text("#version 450\nvoid main(){}\n", encoding="utf-8")
    color_source.write_text("#version 450\nvoid main(){}\n", encoding="utf-8")
    resize_source.write_text("#version 450\nvoid main(){}\n", encoding="utf-8")
    (runtime.shader_compiled_dir / "grade.comp.spv").write_bytes(b"\x03\x02\x23\x07")
    (runtime.shader_compiled_dir / "colorcorrect.comp.spv").write_bytes(b"\x03\x02\x23\x07")
    (runtime.shader_compiled_dir / "resize.comp.spv").write_bytes(b"\x03\x02\x23\x07")
    manifest = {
        "shaders": [
            {
                "source": "grade.comp.glsl",
                "output": "grade.comp.spv",
                "entry": "main",
                "stage": "comp",
                "source_sha256": _source_sha256(grade_source),
            },
            {
                "source": "colorcorrect.comp.glsl",
                "output": "colorcorrect.comp.spv",
                "entry": "main",
                "stage": "comp",
                "source_sha256": _source_sha256(color_source),
            },
            {
                "source": "resize.comp.glsl",
                "output": "resize.comp.spv",
                "entry": "main",
                "stage": "comp",
                "source_sha256": _source_sha256(resize_source),
            },
        ]
    }
    runtime.shader_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    status = runtime._shader_toolchain_status()

    assert status.ready is True
    assert status.source_dir == str(runtime.shader_source_dir)
    assert status.compiled_dir == str(runtime.shader_compiled_dir)
    assert status.manifest_path == str(runtime.shader_manifest_path)
    assert all(item["exists"] for item in status.compiled_shaders)
    assert all(item["source_sha256_matches"] for item in status.compiled_shaders)
    assert {Path(item["source"]).name for item in status.compiled_shaders} == {
        "colorcorrect.comp.glsl",
        "grade.comp.glsl",
        "resize.comp.glsl",
    }
    runtime.close()


def test_shader_toolchain_status_uses_backend_relative_paths_for_repo_assets(monkeypatch) -> None:
    monkeypatch.setenv("OPENCOMP_VULKAN_SIMULATE", "1")
    runtime = VulkanRuntime(ProjectSettings(cache_enabled=True, execution_backend="vulkan"))

    status = runtime._shader_toolchain_status()

    assert status.source_dir == "opencomp/gpu/shaders/src"
    assert status.compiled_dir == "opencomp/gpu/shaders/compiled"
    assert status.manifest_path == "opencomp/gpu/shaders/compiled/manifest.json"
    assert all(item["source"].startswith("opencomp/gpu/shaders/src/") for item in status.compiled_shaders)
    assert all(item["output"].startswith("opencomp/gpu/shaders/compiled/") for item in status.compiled_shaders)
    runtime.close()


def test_native_vulkan_grade_colorcorrect_matches_cpu_when_available() -> None:
    cpu_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="cpu"))
    vulkan_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="vulkan"))
    runtime = vulkan_evaluator.vulkan_runtime
    if runtime is None or not runtime.native_execution_ready or not runtime.native_kernels_bound:
        pytest.skip("Native Vulkan compute path is not available on this machine.")

    image_cpu = cpu_evaluator.evaluate_node(_vulkan_test_graph(), "ColorCorrect1", 1001)
    image_vulkan = vulkan_evaluator.evaluate_node(_vulkan_test_graph(), "ColorCorrect1", 1001)

    assert image_vulkan.metadata["gpu/kernel_mode"] == "native_compute"
    assert image_vulkan.metadata["gpu/simulated"] is False
    np.testing.assert_allclose(image_vulkan.data, image_cpu.data, atol=1e-6, rtol=1e-6)


def test_native_vulkan_preview_resize_matches_cpu_and_keeps_fullres_cache() -> None:
    cpu_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="cpu"))
    vulkan_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=True, execution_backend="vulkan"))
    runtime = vulkan_evaluator.vulkan_runtime
    if runtime is None or not runtime.native_execution_ready or not runtime.native_kernels_bound:
        pytest.skip("Native Vulkan compute path is not available on this machine.")

    graph = _vulkan_test_graph()
    preview = get_float_preview(
        vulkan_evaluator,
        graph,
        PreviewRequest(cache_node_id="Viewer1", eval_node_id="ColorCorrect1", frame=1001, channel="rgba", max_width=8, max_height=8),
    )
    cpu_image = cpu_evaluator.evaluate_node(graph, "ColorCorrect1", 1001)
    cpu_preview = resize_float_rgba(preview_rgba_for_channel(cpu_image, "rgba")[0], max_width=8, max_height=8)

    assert preview.entry.execution_backend == "vulkan"
    assert preview.entry.gpu_kernel_mode == "native_compute"
    assert preview.entry.source_width == 16
    assert preview.entry.source_height == 8
    assert preview.entry.display_width == 8
    assert preview.entry.display_height == 4
    assert preview.entry.gpu_resize_ms > 0.0
    np.testing.assert_allclose(preview.entry.rgba, cpu_preview, atol=1e-5, rtol=1e-5)

    full_image = vulkan_evaluator.evaluate_node(graph, "ColorCorrect1", 1001)

    assert full_image.width == 16
    assert full_image.height == 8


def test_native_vulkan_scale_matches_cpu_when_available() -> None:
    cpu_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="cpu"))
    vulkan_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="vulkan"))
    runtime = vulkan_evaluator.vulkan_runtime
    if runtime is None or not runtime.native_execution_ready or not runtime.native_kernels_bound:
        pytest.skip("Native Vulkan compute path is not available on this machine.")

    graph = _vulkan_scale_graph()
    image_cpu = cpu_evaluator.evaluate_node(graph, "Scale1", 1001)
    image_vulkan = vulkan_evaluator.evaluate_node(graph, "Scale1", 1001)

    assert image_vulkan.metadata["gpu/kernel_mode"] == "native_compute"
    assert image_vulkan.width == 8
    assert image_vulkan.height == 4
    np.testing.assert_allclose(image_vulkan.data, image_cpu.data, atol=1e-5, rtol=1e-5)


def test_native_vulkan_mixed_grade_scale_colorcorrect_matches_cpu_when_available() -> None:
    cpu_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="cpu"))
    vulkan_evaluator = GraphEvaluator(settings=ProjectSettings(cache_enabled=False, execution_backend="vulkan"))
    runtime = vulkan_evaluator.vulkan_runtime
    if runtime is None or not runtime.native_execution_ready or not runtime.native_kernels_bound:
        pytest.skip("Native Vulkan compute path is not available on this machine.")

    graph = _vulkan_mixed_scale_graph()
    image_cpu = cpu_evaluator.evaluate_node(graph, "ColorCorrect1", 1001)
    image_vulkan = vulkan_evaluator.evaluate_node(graph, "ColorCorrect1", 1001)

    assert image_vulkan.metadata["gpu/kernel_mode"] == "native_compute"
    assert image_vulkan.width == 8
    assert image_vulkan.height == 4
    np.testing.assert_allclose(image_vulkan.data, image_cpu.data, atol=1e-5, rtol=1e-5)
