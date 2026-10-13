"""
Tests for indoor mode, run on the generated 20 x 12 m chemistry lab
(engine/make_test_lab.py). The lab is built from a picture, exactly as an
uploaded floor plan would be, so these tests cover the whole path:
picture -> geometry -> cameras -> watched floor -> plan.
"""
import json
from dataclasses import replace

import cv2
import numpy as np
import pytest

import indoor
import make_test_lab as lab
import site_plan
from attackers import entry_start_points
from cameras import Camera, make_wedge_footprint, points_needed
from coverage import camera_probability_grid, line_of_sight_clear
from defender import rotate_footprint
from planner import apply_angles
from twin import Twin


def _camera(rec, grid_m):
    fp = make_wedge_footprint(rec["position"], rec["heading_deg"], rec["fov_deg"], rec["range_m"],
                              n_points=points_needed(rec["fov_deg"], rec["range_m"], grid_m), seed=1)
    return Camera(rec["name"], tuple(rec["position"]), fp, rec["range_m"], rec["online"],
                  heading_deg=rec["heading_deg"], fov_deg=rec["fov_deg"])


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    path = lab.make_lab(str(tmp_path_factory.mktemp("lab") / "lab.png"))
    img = cv2.imread(path)
    return site_plan.build_site(img, {**lab.user_clicks(), "mode": "indoor", "name": "Chem lab"})


@pytest.fixture(scope="module")
def twin(built):
    return Twin(json.loads(json.dumps(built.twin_dict, default=float)))


@pytest.fixture(scope="module")
def cams(built, twin):
    return [_camera(r, twin.grid_m) for r in built.camera_specs]


@pytest.fixture(scope="module")
def report(twin, cams):
    return indoor.floor_report(twin, cams)


def _by_name(cams, name):
    return next(c for c in cams if c.name == name)


# ---------------------------------------------------------------- the floor

def test_lab_is_built_as_an_indoor_site(built, twin):
    assert twin.mode == "indoor" and built.meta["mode"] == "indoor"
    assert abs(built.meta["area_m2"] - 219) < 8
    assert {e["id"] for e in twin.entries} == {"main_entrance", "fire_exit"}
    assert {e["id"] for e in twin.entrances} == {"server_room", "freezer"}


def test_open_floor_is_joined_to_an_entrance(twin):
    floor = indoor.open_floor(twin)
    assert floor.sum() > 0 and not (floor & twin.blocked).any()
    # a cell sealed off from every entrance does not count
    sealed = Twin({**json.loads(json.dumps({
        "grid_m": 1.0, "boundary": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "obstacles": [{"type": "obstacle", "polygon": [[4, 0], [5, 0], [5, 10], [4, 10]]}],
        "entrances": [{"id": "x", "point": [8, 5]}], "entries": [{"id": "door", "point": [1.5, 5.5]}],
        "mode": "indoor"}))})
    f = indoor.open_floor(sealed)
    assert f[5, 1] and not f[5, 8]


def test_entry_starts_are_on_open_floor_near_a_door(twin):
    pts = entry_start_points(twin, 200, seed=42)
    assert len(pts) == 200
    for x, y in pts:
        r, c = twin.cell_of(x, y)
        assert not twin.blocked[r, c]
    near = [min(np.hypot(x - e["point"][0], y - e["point"][1]) for e in twin.entries) for x, y in pts]
    assert max(near) <= 1.7


def test_no_entrance_is_a_clear_error(twin):
    bare = Twin({**twin_dict(twin), "entries": []})
    with pytest.raises(ValueError, match="no entrances"):
        entry_start_points(bare, 10, seed=1)


def twin_dict(t):
    return {"grid_m": t.grid_m, "boundary": list(t.boundary.exterior.coords),
            "obstacles": [{"type": "obstacle", "polygon": list(o.exterior.coords)} for o in t.obstacles],
            "entrances": t.entrances, "entries": t.entries, "mode": "indoor"}


# ---------------------------------------------------------------- what a camera sees

def test_fast_visibility_matches_the_reference_line_of_sight(twin):
    pos = (3.0, 1.5)                                   # corridor, west end
    dist = indoor.visible_from(twin, pos, 12.0)
    cr, cc = twin.cell_of(*pos)
    rng = np.random.default_rng(0)
    free = np.argwhere(~twin.blocked)
    for r, c in free[rng.choice(len(free), 400, replace=False)]:
        cx, cy = twin._cell_center(r, c)
        inside = np.hypot(cx - pos[0], cy - pos[1]) < 12.0
        expect = inside and line_of_sight_clear(twin, cr, cc, r, c, strict=True)
        assert np.isfinite(dist[r, c]) == expect, (r, c)


def test_a_wall_hides_the_room_behind_it(twin):
    seen = indoor.visible_from(twin, (3.0, 1.5), 15.0)       # corridor camera
    r, c = twin.cell_of(3.0, 8.0)                            # main lab, straight through the wall
    assert not np.isfinite(seen[r, c])


def test_model_agrees_with_the_real_camera(twin, cams):
    c = cams[0]
    model = indoor.CamModel(twin, c.position, c.fov_deg, c.max_range_m).grid(c.heading_deg)
    real = camera_probability_grid(twin, c.position, c.footprint, c.max_range_m)
    both = (model > 0) & (real > 0)
    assert both.sum() > 300
    assert ((model > 0) ^ (real > 0)).sum() < 0.05 * both.sum()


# ---------------------------------------------------------------- the report

def test_report_finds_the_planted_blind_spot(report):
    assert 0.2 < report["watched_frac"] < 0.45
    server = next(a for a in report["assets"] if a["id"] == "server_room")
    freezer = next(a for a in report["assets"] if a["id"] == "freezer")
    assert not server["watched"] and freezer["watched"]
    assert any(s["near"] == "server_room" for s in report["blind_spots"])


def test_entrance_status_looks_just_inside_the_door(report):
    # the doorway is a gap in the wall; the status must come from the floor beside it
    assert all(0 <= e["chance_seen"] <= 1 for e in report["entries"])
    assert any(e["chance_seen"] > 0 for e in report["entries"])


def test_report_numbers_agree_with_each_other(report, twin):
    assert abs(report["blind_m2"] + report["watched_frac"] * report["open_floor_m2"] - report["open_floor_m2"]) < 1.0
    assert "blind_mask" not in indoor.public(report)
    json.dumps(indoor.public(report))                      # must be JSON-safe


def test_no_cameras_means_nothing_is_watched(twin):
    rep = indoor.floor_report(twin, [])
    assert rep["watched_frac"] == 0 and rep["blind_m2"] == rep["open_floor_m2"]


def test_offline_camera_watches_nothing(twin, cams):
    off = [replace(c, online=False) for c in cams]
    assert indoor.floor_report(twin, off)["watched_frac"] == 0


def test_outage_table_costs_a_camera_its_floor(twin, cams):
    table = {r["camera"]: r for r in indoor.outage_table(twin, cams)}
    assert set(table) == {"C1", "C2", "C3"}
    assert all(r["watched_after"] <= r["watched_before"] for r in table.values())
    assert max(r["lost_m2"] for r in table.values()) > 5


# ---------------------------------------------------------------- the search

def test_rotation_search_repairs_a_misaimed_camera(twin, cams):
    good = indoor.floor_report(twin, cams)["watched_frac"]
    bad = [replace(c, footprint=rotate_footprint(c.footprint, c.position, 150), heading_deg=(c.heading_deg + 150) % 360)
           if c.name == "C2" else c for c in cams]
    hurt = indoor.floor_report(twin, bad)["watched_frac"]
    assert hurt < good - 0.02                              # the mistake really costs floor
    found = indoor.best_rotations(twin, bad)
    fixed = indoor.floor_report(twin, apply_angles(bad, found["angles"]))["watched_frac"]
    assert fixed >= good - 0.02
    assert "C2" in found["angles"]


def test_rotation_search_leaves_good_cameras_alone(twin, cams):
    assert indoor.best_rotations(twin, cams)["angles"] == {}


def test_added_cameras_go_on_walls_and_raise_coverage(twin, cams):
    found = indoor.plan_additions(twin, cams)
    h = found["history"]
    assert h == sorted(h) and found["added"]
    wall_free = indoor.candidate_mounts(twin, cams)
    for a in found["added"]:
        assert tuple(a["position"]) in wall_free
        assert not twin.blocked[twin.cell_of(*a["position"])]
    assert len(found["added"]) <= indoor.MAX_ADDITIONS


def test_search_is_repeatable(twin, cams):
    assert indoor.plan_additions(twin, cams) == indoor.plan_additions(twin, cams)


def test_mounts_are_beside_a_wall_not_a_bench(twin, cams):
    from shapely.geometry import Point
    walls = [twin.boundary.exterior] + [o for o in twin.obstacles if indoor._is_wall(o)]
    assert [o for o in twin.obstacles if not indoor._is_wall(o)], "the lab has benches"
    for x, y in indoor.candidate_mounts(twin, cams):
        assert min(w.distance(Point(x, y)) for w in walls) <= 0.8


def test_full_plan_is_honest_and_consistent(twin, cams):
    plan = indoor.make_plan(twin, cams, 1.0)
    more = plan["more_cameras"]
    assert plan["mode"] == "indoor" and plan["starts_tested"] == 200
    assert more["watched_after"] > plan["now"]["watched_frac"] + 0.3
    assert more["watched_after"] == pytest.approx(plan["final"]["watched_frac"])
    # the sentence quotes the same figure the report holds
    assert f"{round(plan['final']['watched_frac'] * 100)}%" in more["sentence"]
    # the page previews additions through add_param: x:y:heading, one per addition
    assert len(more["add_param"].split(",")) == len(more["additions"])
    # the server room ends up watched
    server = next(a for a in plan["final"]["assets"] if a["id"] == "server_room")
    assert server["watched"]
    json.dumps(plan, default=float)


def test_plan_with_no_blind_spots_says_no(twin, cams):
    extra = indoor.plan_additions(twin, cams)["added"]
    from planner import make_new_camera
    full = cams + [make_new_camera(a["name"], a["position"], a["heading_deg"], a["fov_deg"], a["range_m"],
                                   grid_m=twin.grid_m) for a in extra]
    plan = indoor.make_plan(twin, full, 1.0)
    assert plan["more_cameras"]["needed"] is False or plan["more_cameras"]["watched_after"] >= plan["now"]["watched_frac"]


def test_where_names_a_part_of_the_floor(twin):
    assert "south-west" in indoor.where(twin, (1.0, 1.0))
    assert "near the server room" in indoor.where(twin, (15.5, 9.5))
