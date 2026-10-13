"""
test_scene.py - tests for GET /api/scene and GET /api/outage.

These run the real simulator on the demo house (nothing is mocked), so
they also prove the what-if switches really change the numbers.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def scene(**params):
    return client.get("/api/scene", params=params)


def rate(body, door):
    return next(d for d in body["doors"] if d["door"] == door)["rate"]


# ------------------------------------------------------------------ shape

def test_scene_has_everything_the_map_needs():
    r = scene()
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"door", "archetype", "starts_tested", "site", "coverage",
                         "routes", "doors", "summary"}
    assert body["starts_tested"] == 200
    assert [c["name"] for c in body["site"]["cameras"]] == ["C1", "C2", "C3"]
    assert {e["id"] for e in body["site"]["entrances"]} == {"front_door", "side_door"}


def test_coverage_grid_matches_the_site_and_stays_between_0_and_1():
    body = scene().json()
    site, grid = body["site"], body["coverage"]
    assert len(grid) == site["n_rows"] and len(grid[0]) == site["n_cols"]
    flat = [v for row in grid for v in row]
    assert min(flat) >= 0 and max(flat) <= 1 and max(flat) > 0.5


def test_cameras_report_where_they_point():
    cams = {c["name"]: c for c in scene().json()["site"]["cameras"]}
    assert cams["C1"]["heading_deg"] == 90 and cams["C2"]["heading_deg"] == 0
    assert all(c["fov_deg"] == 90 and c["range_m"] == 9 and c["online"] for c in cams.values())


def test_default_door_is_the_most_exposed_one():
    assert scene().json()["door"] == "side_door"


def test_all_working_means_the_normal_summary():
    body = scene().json()
    assert body["summary"] == "All 3 cameras are online. No door is more exposed than normal."
    assert all(d["change"] == 0 for d in body["doors"])


def test_same_question_gives_the_same_answer():
    assert scene(door="side_door", offline="C1").json() == scene(door="side_door", offline="C1").json()


# ------------------------------------------------------------- routes

def test_breach_count_matches_the_success_rate():
    body = scene(door="side_door").json()
    n = body["routes"]["n_breached"]
    assert n / body["starts_tested"] == pytest.approx(rate(body, "side_door"))


def test_every_attacker_is_either_breached_or_caught():
    body = scene(door="side_door").json()
    assert body["routes"]["n_breached"] + body["routes"]["n_caught"] == 200


def test_example_routes_are_limited_and_have_two_or_more_points():
    routes = scene(door="side_door").json()["routes"]
    assert 0 < len(routes["breached"]) <= 16 and len(routes["caught"]) <= 8
    assert all(len(r) >= 2 for r in routes["breached"] + routes["caught"])


def test_routes_stay_inside_the_property():
    body = scene(door="side_door").json()
    xs = [p[0] for p in body["site"]["boundary"]]
    ys = [p[1] for p in body["site"]["boundary"]]
    for route in body["routes"]["breached"]:
        for x, y in route:
            assert min(xs) - 0.5 <= x <= max(xs) + 0.5 and min(ys) - 0.5 <= y <= max(ys) + 0.5


def test_a_fully_covered_door_has_no_breach_routes():
    routes = scene(door="front_door").json()["routes"]
    assert routes["n_breached"] == 0 and routes["breached"] == []


# --------------------------------------------------------------- what-ifs

def test_an_offline_camera_makes_its_door_worse():
    body = scene(door="side_door", offline="C2").json()
    assert rate(body, "side_door") >= 0.85
    assert "Camera C2 is offline" in body["summary"]
    assert next(c for c in body["site"]["cameras"] if c["name"] == "C2")["online"] is False


def test_an_offline_camera_no_longer_adds_coverage():
    normal = scene().json()["coverage"]
    off = scene(offline="C1").json()["coverage"]
    assert sum(map(sum, off)) < sum(map(sum, normal))


def test_with_every_camera_offline_nothing_is_watched():
    body = scene(offline="C1,C2,C3").json()
    assert max(v for row in body["coverage"] for v in row) == 0
    assert rate(body, "front_door") > 0.9 and rate(body, "side_door") > 0.9


def test_repeated_camera_names_are_counted_once():
    assert scene(offline="C1,C1").json() == scene(offline="C1").json()


def test_rotating_c2_the_recommended_way_helps_the_side_door():
    body = scene(door="side_door", rotate="C2:45").json()
    assert rate(body, "side_door") < 0.5
    c2 = next(c for c in body["site"]["cameras"] if c["name"] == "C2")
    assert c2["heading_deg"] == 45


def test_a_clockwise_turn_wraps_the_heading_into_0_to_360():
    c2 = next(c for c in scene(rotate="C2:-30").json()["site"]["cameras"] if c["name"] == "C2")
    assert c2["heading_deg"] == 330


def test_rotation_preview_compares_against_normal_with_the_same_rotation():
    body = scene(rotate="C2:45", offline="C1").json()
    front = next(d for d in body["doors"] if d["door"] == "front_door")
    assert front["rate_normal"] == 0 and front["rate"] > 0


def test_one_request_never_leaks_into_the_next():
    scene(offline="C1,C2,C3", rotate="C2:90")
    assert scene().json()["summary"].startswith("All 3 cameras are online")


# ------------------------------------------------------------ bad input

def test_unknown_door_is_404():
    assert scene(door="back_door").status_code == 404


def test_unknown_archetype_is_404():
    assert scene(archetype="ninja").status_code == 404


def test_unknown_camera_in_offline_is_400():
    r = scene(offline="C9")
    assert r.status_code == 400 and "no camera 'C9'" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["C2", "C2:", "C2:abc", "C2:nan", "C2:inf", "C2:181", "C9:10", ":10"])
def test_bad_rotate_is_400(bad):
    assert scene(rotate=bad).status_code == 400


def test_other_attackers_are_accepted():
    for name in ("opportunist", "camera_avoider", "side_rear_entry"):
        assert scene(archetype=name).json()["archetype"] == name


# ----------------------------------------------------------------- outage

def test_outage_has_a_row_per_camera_and_a_cell_per_door():
    body = client.get("/api/outage").json()
    assert [r["camera"] for r in body["cameras"]] == ["C1", "C2", "C3"]
    assert all({e["door"] for e in r["effects"]} == {"front_door", "side_door"} for r in body["cameras"])


def test_outage_matches_what_the_engine_reports():
    body = client.get("/api/outage").json()
    c1 = next(r for r in body["cameras"] if r["camera"] == "C1")
    front = next(e for e in c1["effects"] if e["door"] == "front_door")
    assert front["before"] == 0 and front["change"] >= 0.15
    assert "Camera C1 went offline" in c1["sentence"]


def test_single_points_of_failure_are_worst_first():
    spof = client.get("/api/outage").json()["single_points_of_failure"]
    assert spof and spof[0]["camera"] == "C2"
    assert [s["change"] for s in spof] == sorted((s["change"] for s in spof), reverse=True)


def test_outage_rejects_an_unknown_attacker():
    assert client.get("/api/outage", params={"archetype": "ninja"}).status_code == 404
