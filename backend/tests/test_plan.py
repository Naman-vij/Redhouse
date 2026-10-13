"""
test_plan.py - tests for GET /api/plan (best setup + do I need more cameras)
and the new what-if switches on GET /api/scene (rotate=a,b and add=x:y:h).

Real simulator, demo house, nothing mocked. The plan is worked out once for
the whole file (about 10 seconds) and reused.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def plan():
    r = client.get("/api/plan", params={"archetype": "opportunist"})
    assert r.status_code == 200
    return r.json()


def rate(doors, door, key="rate"):
    return next(d for d in doors if d["door"] == door)[key]


# ------------------------------------------------------------------ the answer

def test_plan_has_both_answers(plan):
    assert set(plan) >= {"now", "best_existing", "more_cameras", "final", "target"}
    assert plan["starts_tested"] == 200


def test_best_use_of_existing_cameras_helps_the_exposed_door(plan):
    now = rate(plan["now"], "side_door")
    after = rate(plan["best_existing"]["doors"], "side_door", "after")
    assert now == pytest.approx(0.60)               # same figure as the rest of the page
    assert after < now


def test_it_never_makes_a_door_worse_overall(plan):
    doors = plan["best_existing"]["doors"]
    assert sum(d["after"] for d in doors) <= sum(d["before"] for d in doors)


def test_more_cameras_are_suggested_only_when_a_door_is_still_above_target(plan):
    after_turn = max(d["after"] for d in plan["best_existing"]["doors"])
    assert plan["more_cameras"]["needed"] == (after_turn > plan["target"])


def test_added_camera_gets_the_exposed_door_to_near_zero(plan):
    more = plan["more_cameras"]
    assert more["needed"] and len(more["additions"]) >= 1
    assert more["reached_target"]
    assert max(d["after"] for d in more["doors"]) <= plan["target"]


def test_before_of_each_step_is_the_after_of_the_last(plan):
    prev = {d["door"]: d["after"] for d in plan["best_existing"]["doors"]}
    now = {d["door"]: d["before"] for d in plan["more_cameras"]["doors"]}
    assert prev == now


def test_sentences_quote_the_same_numbers_as_the_tables(plan):
    side_before = round(rate(plan["best_existing"]["doors"], "side_door", "before") * 100)
    side_after = round(rate(plan["best_existing"]["doors"], "side_door", "after") * 100)
    assert f"side door {side_before} → {side_after}" in plan["best_existing"]["sentence"]
    assert "side door" in plan["more_cameras"]["sentence"]


def test_new_cameras_are_placed_on_real_ground(plan):
    twin_boundary = client.get("/api/scene").json()["site"]
    from shapely.geometry import Point, Polygon
    prop = Polygon(twin_boundary["boundary"])
    house = [Polygon(o) for o in twin_boundary["obstacles"]]
    for a in plan["more_cameras"]["additions"]:
        p = Point(a["position"])
        assert prop.contains(p)
        assert not any(h.contains(p) for h in house)


# ------------------------------------------------- preview round trip

def test_previewing_the_whole_plan_on_the_map_gives_the_planned_numbers(plan):
    """The map must show exactly what the planner promised."""
    body = client.get("/api/scene", params={
        "rotate": plan["best_existing"]["rotate_param"],
        "add": plan["more_cameras"]["add_param"]}).json()
    for d in plan["final"]:
        assert rate(body["doors"], d["door"]) == pytest.approx(d["rate"])


def test_previewing_only_the_turns_gives_the_best_existing_numbers(plan):
    body = client.get("/api/scene", params={"rotate": plan["best_existing"]["rotate_param"]}).json()
    for d in plan["best_existing"]["doors"]:
        assert rate(body["doors"], d["door"]) == pytest.approx(d["after"])


def test_proposed_cameras_are_flagged_so_the_page_can_draw_them_differently(plan):
    body = client.get("/api/scene", params={"add": plan["more_cameras"]["add_param"]}).json()
    cams = body["site"]["cameras"]
    assert [c["proposed"] for c in cams] == [False, False, False] + [True] * len(plan["more_cameras"]["additions"])
    assert cams[3]["name"] == "N1"


def test_turning_two_cameras_at_once(plan):
    body = client.get("/api/scene", params={"rotate": "C2:45,C3:-75"}).json()
    heads = {c["name"]: c["heading_deg"] for c in body["site"]["cameras"]}
    assert heads["C2"] == 45 and heads["C3"] == 105 and heads["C1"] == 90


# ------------------------------------------------- bad input

def test_unknown_archetype_is_404():
    assert client.get("/api/plan", params={"archetype": "ninja"}).status_code == 404


@pytest.mark.parametrize("bad", ["5:5", "a:b:c", "5:5:nan", "5:5:90:1", "1:1:0,2:2:0,3:3:0,4:4:0"])
def test_bad_add_is_400(bad):
    assert client.get("/api/scene", params={"add": bad}).status_code == 400


def test_camera_inside_the_house_is_refused():
    r = client.get("/api/scene", params={"add": "10:8:90"})        # middle of the house
    assert r.status_code == 400 and "inside the house" in r.json()["detail"]


def test_camera_off_the_property_is_refused():
    assert client.get("/api/scene", params={"add": "-5:3:90"}).status_code == 400


# ------------------------------------------------- "was X, now Y" while turning a camera

def test_every_door_reports_the_original_rate_so_the_page_can_show_was_and_now():
    body = client.get("/api/scene", params={"rotate": "C2:45"}).json()
    side = next(d for d in body["doors"] if d["door"] == "side_door")
    assert side["rate_original"] == pytest.approx(0.60)       # the untouched cameras
    assert side["rate"] < side["rate_original"]                # C2 turned the right way helps


def test_original_rate_ignores_what_ifs():
    plain = client.get("/api/scene").json()["doors"]
    changed = client.get("/api/scene", params={"rotate": "C1:90", "offline": "C3", "add": "5.3:3.3:90"}).json()["doors"]
    assert [d["rate_original"] for d in plain] == [d["rate_original"] for d in changed]


def test_turning_the_wrong_way_makes_the_door_worse():
    body = client.get("/api/scene", params={"rotate": "C2:-90"}).json()
    side = next(d for d in body["doors"] if d["door"] == "side_door")
    assert side["rate"] >= side["rate_original"]
