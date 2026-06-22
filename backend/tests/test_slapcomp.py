from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from opencomp.core.evaluator import GraphEvaluator
from opencomp.core.models import Edge, Node, ProjectGraph, ProjectSettings, TileWindow
from opencomp.core.render_contract import RenderROI, RenderRequest


LOCAL_SHOT_ROOT = Path(r"E:\opencomp_tests\LAL_105_523_0010")
LOCAL_PLATE = LOCAL_SHOT_ROOT / "PLATE" / "LAL_105_523_0010_####.exr"
LOCAL_MAIN_3D = LOCAL_SHOT_ROOT / "3D" / "LAL_105_523_0010_3D_v003.####.exr"
LOCAL_CLOTHES = LOCAL_SHOT_ROOT / "3D" / "LAL_105_523_0010_3D_CLOTHES_01.####.exr"


def test_synthetic_three_input_slapcomp_evaluates_viewer_write_and_tiles(tmp_path: Path) -> None:
    graph = synthetic_slapcomp_graph(tmp_path / "slapcomp.####.png", width=96, height=54)
    evaluator = GraphEvaluator(settings=_settings(width=96, height=54, cache_enabled=True))
    plan = evaluator.execution_plan_for(
        graph,
        RenderRequest(node_id="Viewer1", frame=1001, roi=RenderROI(x=9, y=7, width=23, height=11)),
        eval_node_id="Viewer1",
        output_signature=evaluator.output_signature(graph, "Viewer1", 1001),
    )

    final_frame = evaluator.evaluate_node(graph, "Viewer1", 1001)
    write_frame = evaluator.evaluate_node(graph, "Write_EXR", 1001)
    output_path = tmp_path / "slapcomp.1001.png"
    tile_a = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(9, 7, 23, 11))
    hits_before = evaluator.cache_snapshot()["tile_cache_hits"]
    tile_b = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(9, 7, 23, 11))
    hits_after = evaluator.cache_snapshot()["tile_cache_hits"]

    assert plan.tile_native is True
    assert {item.node_id for item in plan.nodes if item.node_type == "Reformat" and item.tile_native} == {
        "Plate_Format",
        "Main3D_Format",
        "Clothes3D_Format",
    }
    expected = _expected_synthetic_slap_pixel()
    assert final_frame.width == 96
    assert final_frame.height == 54
    assert np.allclose(final_frame.data[10, 10], expected, atol=1e-6)
    assert np.allclose(write_frame.data[10, 10], expected, atol=1e-6)
    assert output_path.exists()
    assert tile_a.metadata["tile/full_width"] == 96
    assert tile_a.metadata["tile/full_height"] == 54
    assert np.allclose(tile_a.data, tile_b.data)
    assert hits_after > hits_before


def test_slapcomp_viewer_active_input_only_evaluates_selected_branch(tmp_path: Path) -> None:
    graph = synthetic_slapcomp_graph(tmp_path / "unused.####.png", width=32, height=18)
    graph.nodes["Viewer1"].params["active_input"] = "2"
    evaluator = GraphEvaluator(settings=_settings(width=32, height=18, cache_enabled=True))

    frame = evaluator.evaluate_node(graph, "Viewer1", 1001)
    timings = evaluator.cache_snapshot()["node_timings"]

    assert np.allclose(frame.data[0, 0], [0.05, 0.08, 0.12, 1.0])
    assert "Plate_Format" in timings
    assert "Main3D_Read" not in timings
    assert "Clothes3D_Read" not in timings
    assert "Merge_Clothes_Over_Main" not in timings


def test_local_lal_105_slapcomp_frame_and_cache_when_reference_files_exist(tmp_path: Path) -> None:
    _require_local_lal_105_files()
    graph = local_lal_105_slapcomp_graph(tmp_path / "local_slapcomp.####.png")
    evaluator = GraphEvaluator(settings=_settings(width=4096, height=3024, cache_enabled=True))

    started = time.perf_counter()
    frame = evaluator.evaluate_node(graph, "Viewer1", 1001)
    cold_ms = (time.perf_counter() - started) * 1000.0
    started = time.perf_counter()
    warm = evaluator.evaluate_node(graph, "Viewer1", 1001)
    warm_ms = (time.perf_counter() - started) * 1000.0
    tile = evaluator.evaluate_node_tile(graph, "Viewer1", 1001, TileWindow(0, 0, 256, 128))

    assert frame.width == 4096
    assert frame.height == 3024
    assert warm.width == frame.width
    assert warm_ms < cold_ms
    assert tile.width == 256
    assert tile.height == 128
    assert evaluator.cache_snapshot()["hits"] >= 1


def synthetic_slapcomp_graph(write_path: Path | str, *, width: int, height: int) -> ProjectGraph:
    return ProjectGraph(
        nodes={
            "Plate_Read": Node(
                id="Plate_Read",
                type="Constant",
                name="Plate_Read",
                position=(-360, -620),
                params={"width": width, "height": height, "r": 0.05, "g": 0.08, "b": 0.12, "a": 1.0},
            ),
            "Plate_Format": Node(
                id="Plate_Format",
                type="Reformat",
                name="Plate_Format",
                position=(-360, -470),
                params={"width": width, "height": height, "resize": "distort"},
            ),
            "Main3D_Read": Node(
                id="Main3D_Read",
                type="Constant",
                name="Main3D_Read",
                position=(40, -620),
                params={"width": width, "height": height, "r": 0.3, "g": 0.1, "b": 0.02, "a": 0.5},
            ),
            "Main3D_Format": Node(
                id="Main3D_Format",
                type="Reformat",
                name="Main3D_Format",
                position=(40, -470),
                params={"width": width, "height": height, "resize": "distort"},
            ),
            "Main3D_Grade": Node(
                id="Main3D_Grade",
                type="Grade",
                name="Main3D_Grade",
                position=(40, -330),
                params={"gain": 1.1, "offset": 0.01, "gamma": 1.0},
            ),
            "Clothes3D_Read": Node(
                id="Clothes3D_Read",
                type="Constant",
                name="Clothes3D_Read",
                position=(430, -620),
                params={"width": width, "height": height, "r": 0.02, "g": 0.22, "b": 0.34, "a": 0.35},
            ),
            "Clothes3D_Format": Node(
                id="Clothes3D_Format",
                type="Reformat",
                name="Clothes3D_Format",
                position=(430, -470),
                params={"width": width, "height": height, "resize": "distort"},
            ),
            "Clothes3D_Grade": Node(
                id="Clothes3D_Grade",
                type="Grade",
                name="Clothes3D_Grade",
                position=(430, -330),
                params={"gain": 0.95, "offset": 0.0, "gamma": 1.0},
            ),
            "Merge_Main3D_Over_Plate": Node(
                id="Merge_Main3D_Over_Plate",
                type="Merge",
                name="Merge_Main3D_Over_Plate",
                position=(-120, -150),
                params={"operation": "over", "mix": 1.0, "bbox": "union", "metadata_from": "b"},
            ),
            "Merge_Clothes_Over_Main": Node(
                id="Merge_Clothes_Over_Main",
                type="Merge",
                name="Merge_Clothes_Over_Main",
                position=(-120, 20),
                params={"operation": "over", "mix": 1.0, "bbox": "union", "metadata_from": "b"},
            ),
            "Final_SlapGrade": Node(
                id="Final_SlapGrade",
                type="Grade",
                name="Final_SlapGrade",
                position=(-120, 190),
                params={"gain": 1.0, "offset": 0.0, "gamma": 1.0},
            ),
            "Viewer1": Node(
                id="Viewer1",
                type="Viewer",
                name="Viewer1",
                position=(-120, 370),
                params={"active_input": "1"},
            ),
            "Write_EXR": Node(
                id="Write_EXR",
                type="Write",
                name="Write_EXR",
                position=(-120, 550),
                params={"path": str(write_path), "channels": "rgba", "overwrite": True, "create_directories": True, "metadata": "all"},
            ),
        },
        edges=[
            Edge(id="plate-format", source_node="Plate_Read", target_node="Plate_Format", target_socket="in"),
            Edge(id="main-format", source_node="Main3D_Read", target_node="Main3D_Format", target_socket="in"),
            Edge(id="main-grade", source_node="Main3D_Format", target_node="Main3D_Grade", target_socket="in"),
            Edge(id="clothes-format", source_node="Clothes3D_Read", target_node="Clothes3D_Format", target_socket="in"),
            Edge(id="clothes-grade", source_node="Clothes3D_Format", target_node="Clothes3D_Grade", target_socket="in"),
            Edge(id="merge-main-b", source_node="Plate_Format", target_node="Merge_Main3D_Over_Plate", target_socket="b"),
            Edge(id="merge-main-a", source_node="Main3D_Grade", target_node="Merge_Main3D_Over_Plate", target_socket="a"),
            Edge(id="merge-clothes-b", source_node="Merge_Main3D_Over_Plate", target_node="Merge_Clothes_Over_Main", target_socket="b"),
            Edge(id="merge-clothes-a", source_node="Clothes3D_Grade", target_node="Merge_Clothes_Over_Main", target_socket="a"),
            Edge(id="final-grade", source_node="Merge_Clothes_Over_Main", target_node="Final_SlapGrade", target_socket="in"),
            Edge(id="viewer-final", source_node="Final_SlapGrade", target_node="Viewer1", target_socket="1"),
            Edge(id="viewer-plate", source_node="Plate_Format", target_node="Viewer1", target_socket="2"),
            Edge(id="viewer-main", source_node="Merge_Main3D_Over_Plate", target_node="Viewer1", target_socket="3"),
            Edge(id="viewer-main-grade", source_node="Main3D_Grade", target_node="Viewer1", target_socket="4"),
            Edge(id="viewer-clothes-grade", source_node="Clothes3D_Grade", target_node="Viewer1", target_socket="5"),
            Edge(id="write-final", source_node="Final_SlapGrade", target_node="Write_EXR", target_socket="in"),
        ],
    )


def local_lal_105_slapcomp_graph(write_path: Path | str) -> ProjectGraph:
    graph = synthetic_slapcomp_graph(write_path, width=4096, height=3024)
    graph.nodes["Plate_Read"].type = "Read"
    graph.nodes["Plate_Read"].params = _read_params(LOCAL_PLATE)
    graph.nodes["Main3D_Read"].type = "Read"
    graph.nodes["Main3D_Read"].params = _read_params(LOCAL_MAIN_3D)
    graph.nodes["Clothes3D_Read"].type = "Read"
    graph.nodes["Clothes3D_Read"].params = _read_params(LOCAL_CLOTHES)
    return graph


def _read_params(path: Path) -> dict[str, object]:
    return {
        "path": str(path),
        "colorspace": "ACES2065-1",
        "frame_start": 1001,
        "frame_end": 1010,
        "before": "hold",
        "after": "hold",
        "missing_frames": "error",
        "auto_alpha": True,
    }


def _settings(*, width: int, height: int, cache_enabled: bool) -> ProjectSettings:
    return ProjectSettings(
        frame_start=1001,
        frame_end=1010,
        width=width,
        height=height,
        working_colorspace="ACES2065-1",
        proxy_enabled=False,
        cache_enabled=cache_enabled,
        tile_rendering_enabled=True,
        tile_height=16 if height <= 128 else 64,
        tile_workers=4,
        render_workers=4,
        read_workers=4,
    )


def _expected_synthetic_slap_pixel() -> np.ndarray:
    plate = np.array([0.05, 0.08, 0.12, 1.0], dtype=np.float32)
    main = np.array([0.3 * 1.1 + 0.01, 0.1 * 1.1 + 0.01, 0.02 * 1.1 + 0.01, 0.5], dtype=np.float32)
    clothes = np.array([0.02 * 0.95, 0.22 * 0.95, 0.34 * 0.95, 0.35], dtype=np.float32)
    main_over_plate = main + plate * (1.0 - main[3])
    clothes_over_main = clothes + main_over_plate * (1.0 - clothes[3])
    return clothes_over_main


def _require_local_lal_105_files() -> None:
    missing = [
        str(path).replace("####", "1001")
        for path in (LOCAL_PLATE, LOCAL_MAIN_3D, LOCAL_CLOTHES)
        if not Path(str(path).replace("####", "1001")).exists()
    ]
    if missing:
        pytest.skip(f"Local LAL_105_523_0010 EXR files are unavailable: {missing}")
