"""
test_api.py - checks the API endpoints. Run from backend/ with:
    python -m pytest -v
"""
from fastapi.testclient import TestClient
from app.main import app

# NOTE: TestClient calls the API directly in memory, without starting
# a real server, so tests run in seconds and need no open port.
client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_twin_has_everything_the_frontend_needs():
    data = client.get("/api/demo/twin").json()
    assert data["n_rows"] == 36 and data["n_cols"] == 48
    assert len(data["boundary"]) >= 4
    assert len(data["obstacles"]) == 1
    assert {e["id"] for e in data["entrances"]} == {"front_door", "side_door"}
    assert [c["name"] for c in data["cameras"]] == ["C1", "C2", "C3"]


def test_audit_finds_the_planted_camera_mistake():
    data = client.get("/api/demo/audit").json()
    fix = data["fix"]
    assert fix["camera"] == "C2"
    assert fix["after_success_rate"] < fix["before_success_rate"]
    assert fix["genuinely_improved"] is True


def test_unknown_door_gives_404_not_a_crash():
    assert client.get("/api/demo/audit?door=garage").status_code == 404


def test_unknown_archetype_gives_404():
    assert client.get("/api/demo/audit?archetype=ninja").status_code == 404


def test_well_covered_door_says_no_change_needed():
    # NOTE: this one depends on the describe_fix change in
    # engine/defender.py from my last message. If it fails, that
    # edit hasn't been applied yet.
    fix = client.get("/api/demo/audit?door=front_door").json()["fix"]
    assert fix["change_type"] == "none_needed"
    assert "already well covered" in fix["description"]