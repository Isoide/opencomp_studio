from opencomp.core.defaults import create_default_project
from opencomp.core.models import Project


def test_project_serializes_with_schema_version() -> None:
    project = create_default_project()
    payload = project.model_dump_json()
    restored = Project.model_validate_json(payload)
    assert restored.schema_version == "0.1.0"
    assert "Viewer1" in restored.graph.nodes
    assert restored.settings.frame_start == 1001
    assert restored.settings.frame_end == 1010
