from fastapi.testclient import TestClient

from opencomp.app import app


def test_script_tabs_can_be_created_and_activated() -> None:
    client = TestClient(app)
    project = client.post("/api/projects/new").json()
    assert project["active_script_id"] == "main"
    assert len(project["script_tabs"]) == 1

    created = client.post("/api/scripts", json={"name": "Paint Prep", "kind": "comp"}).json()
    assert created["active_script_id"] == "paint-prep"
    assert len(created["script_tabs"]) == 2
    assert "Viewer1" in created["graph"]["nodes"]

    activated = client.put("/api/scripts/active", json={"script_id": "main"}).json()
    assert activated["active_script_id"] == "main"
    graph = client.get("/api/graph").json()
    assert "Viewer1" in graph["nodes"]


def test_preferences_round_trip() -> None:
    client = TestClient(app)
    project = client.post("/api/projects/new").json()
    preferences = project["preferences"]
    preferences["autosave_seconds"] = 120
    preferences["hotkeys"]["add_group"] = "shift+g"

    response = client.put("/api/projects/preferences", json={"preferences": preferences})

    assert response.status_code == 200
    assert response.json()["autosave_seconds"] == 120
    assert response.json()["hotkeys"]["add_group"] == "shift+g"
