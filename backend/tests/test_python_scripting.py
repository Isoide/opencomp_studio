from fastapi.testclient import TestClient

from opencomp.app import app


def test_python_script_can_edit_backend_session_graph() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")
    code = r'''
node = opencomp.node("Read2")
node.value("path").setValue("C:/plates/test.####.exr")
node.value("first_frame").setValue(1001)
node.value("last_frame").setValue(1010)
node.setPosition(280, 120)

grade = opencomp.create_node("Grade", name="Grade2")
grade.value("gain").setValue(1.25)
grade.setInput("in", node)

root = opencomp.node("root")
root.value("name").setValue("test")
root.value("first_frame").setValue(1001)
root.value("last_frame").setValue(1010)

print(node.value("path").getValue())
'''

    response = client.post("/api/python/run", json={"code": code})
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert result["changed"] is True
    assert "C:/plates/test.####.exr" in result["stdout"]

    project = result["project"]
    graph = project["graph"]
    assert project["project_name"] == "test"
    assert project["settings"]["frame_start"] == 1001
    assert project["settings"]["frame_end"] == 1010
    assert graph["nodes"]["Read2"]["params"]["path"] == "C:/plates/test.####.exr"
    assert graph["nodes"]["Read2"]["params"]["frame_start"] == 1001
    assert graph["nodes"]["Read2"]["params"]["frame_end"] == 1010
    assert graph["nodes"]["Grade2"]["params"]["gain"] == 1.25
    assert any(
        edge["source_node"] == "Read2" and edge["target_node"] == "Grade2" and edge["target_socket"] == "in"
        for edge in graph["edges"]
    )


def test_python_script_error_returns_traceback_without_http_failure() -> None:
    client = TestClient(app)
    client.post("/api/projects/new")

    response = client.post("/api/python/run", json={"code": "raise RuntimeError('boom')"})
    assert response.status_code == 200
    result = response.json()
    assert result["success"] is False
    assert "RuntimeError: boom" in result["error"]
    assert "Traceback" in result["traceback"]
