"""
test_engine.py

Automated checks for the RedHouse engine. Each test protects one
thing we verified by hand earlier. Run with:  python -m pytest -v
"""

import os
import numpy as np
import pytest

from twin import Twin
from calibration import fit_homography, pixels_to_map
from coverage import camera_probability_grid, combine_cameras, risk_grid
from search import routes_to_target, reconstruct_route
from attackers import boundary_start_points, two_seed_sets
from metrics import audit_door
from cameras import make_demo_cameras
from defender import (build_risk, success_rate, search_best_fix,
                      verify_on_held_out)
from baselines import compare_all

from defender import (build_risk, success_rate, search_best_fix,
                      verify_on_held_out, describe_fix)

SCENARIO = os.path.join(os.path.dirname(__file__), "..", "..",
                        "scenarios", "demo_house.json")
SIDE_DOOR = (6.0, 9.0)


# NOTE: a "fixture" builds something once and shares it with every
# test that asks for it, instead of rebuilding the twin 15 times.
@pytest.fixture(scope="module")
def twin():
    return Twin.from_file(SCENARIO)


@pytest.fixture(scope="module")
def cameras():
    return make_demo_cameras()


# ---------------- twin.py ----------------

def test_grid_size(twin):
    # 24m x 18m at 0.5m cells -> 48 columns x 36 rows
    assert (twin.n_rows, twin.n_cols) == (36, 48)


def test_house_is_blocked_and_garden_is_open(twin):
    assert twin.blocked[twin.cell_of(11.0, 8.0)]       # inside the house
    assert not twin.blocked[twin.cell_of(2.0, 16.0)]   # open garden


def test_cell_of_never_goes_out_of_bounds(twin):
    # This is the exact bug we hit: a point ON the top/right edge
    # used to compute one cell past the grid.
    r, c = twin.cell_of(24.0, 18.0)
    assert 0 <= r < twin.n_rows and 0 <= c < twin.n_cols


# ---------------- calibration.py ----------------

def test_homography_round_trip():
    img = [(400, 500), (700, 500), (700, 300), (400, 300)]
    ground = [(0, 0), (1, 0), (1, 1), (0, 1)]
    H = fit_homography(img, ground)
    back, _ = pixels_to_map(H, np.array(img, dtype=float))
    assert np.allclose(back, ground, atol=1e-3)


# ---------------- coverage.py ----------------

def test_wall_blocks_line_of_sight(twin):
    pos = (11.0, 1.0)
    # NOTE: a full disk of footprint points so the cell behind the
    # house IS inside the footprint - the only thing stopping
    # detection there is the wall.
    rng = np.random.default_rng(1)
    r = rng.uniform(0, 20, 4000)
    a = rng.uniform(0, 2 * np.pi, 4000)
    fp = np.column_stack([pos[0] + r * np.cos(a), pos[1] + r * np.sin(a)])
    prob = camera_probability_grid(twin, pos, fp, max_range_m=20.0)
    assert prob[twin.cell_of(11.0, 14.0)] == 0.0       # behind the house
    assert prob.max() > 0.5                            # but not blind overall


def test_two_cameras_combine_correctly():
    a = np.array([[0.6]])
    b = np.array([[0.5]])
    # 1 - (1-0.6)(1-0.5) = 0.8
    assert combine_cameras([a, b])[0, 0] == pytest.approx(0.8)


def test_risk_adds_up_like_probability_multiplies():
    # Two independent 50% chances of being seen: surviving both = 25%.
    r = risk_grid(np.array([[0.5]]))[0, 0]
    assert np.exp(-(r + r)) == pytest.approx(0.25, abs=1e-3)


# ---------------- search.py ----------------

def test_route_reaches_door_and_avoids_walls(twin):
    zero_risk = np.zeros((twin.n_rows, twin.n_cols))
    target = twin.cell_of(11.0, 4.0)
    _, came_from = routes_to_target(twin, *target, zero_risk, risk_weight=0.0)
    start = twin.cell_of(2.0, 16.0)
    route = reconstruct_route(came_from, start, target)
    assert route and route[-1] == target
    # NOTE: a door sits IN the house wall, so the final cell (the door
    # itself) is a wall cell by design. Every cell BEFORE it must be
    # open ground - the route may never cut through the house.
    assert not any(twin.blocked[r, c] for r, c in route[:-1])


# ---------------- attackers.py ----------------

def test_start_points_lie_on_boundary(twin):
    for x, y in boundary_start_points(twin, 50, seed=42):
        assert x in (0.0, 24.0) or y in (0.0, 18.0) or \
            min(abs(x), abs(x - 24), abs(y), abs(y - 18)) < 1e-6


def test_same_seed_gives_same_points(twin):
    assert boundary_start_points(twin, 20, seed=5) == boundary_start_points(twin, 20, seed=5)


# ---------------- metrics.py ----------------

def test_audit_is_deterministic(twin, cameras):
    training, _ = two_seed_sets(twin, 200)
    a = success_rate(twin, cameras, SIDE_DOOR, training, 0.5)
    b = success_rate(twin, cameras, SIDE_DOOR, training, 0.5)
    assert a == b


def test_more_cameras_never_make_attacks_easier(twin, cameras):
    training, _ = two_seed_sets(twin, 200)
    one = success_rate(twin, cameras[:1], SIDE_DOOR, training, 0.5)
    three = success_rate(twin, cameras, SIDE_DOOR, training, 0.5)
    assert three <= one


def test_careful_attacker_is_no_worse_off(twin, cameras):
    # Higher risk_weight finds lower-risk routes, so success should
    # not drop. (This is the check where we corrected ourselves.)
    training, _ = two_seed_sets(twin, 200)
    risk = build_risk(twin, cameras)
    careless = audit_door(twin, SIDE_DOOR, risk, training, 0.5)["success_rate"]
    careful = audit_door(twin, SIDE_DOOR, risk, training, 5.0)["success_rate"]
    assert careful >= careless


# ---------------- defender.py (the important ones) ----------------

def test_defender_finds_the_planted_mistake(twin, cameras):
    training, _ = two_seed_sets(twin, 200)
    result = search_best_fix(twin, cameras, SIDE_DOOR, training, 0.5)
    assert result["improved"]
    assert result["best_camera"] == "C2"        # the one we mis-aimed
    assert result["best_rate"] < result["baseline_rate"]


def test_fix_generalises_to_unseen_attackers(twin, cameras):
    training, held_out = two_seed_sets(twin, 200)
    result = search_best_fix(twin, cameras, SIDE_DOOR, training, 0.5)
    v = verify_on_held_out(twin, cameras, SIDE_DOOR, held_out, 0.5,
                           result["best_camera"], result["best_angle"])
    assert v["genuinely_improved"]


def test_defender_does_not_change_the_original_cameras(twin, cameras):
    before = [c.footprint.copy() for c in cameras]
    training, _ = two_seed_sets(twin, 200)
    search_best_fix(twin, cameras, SIDE_DOOR, training, 0.5)
    for c, b in zip(cameras, before):
        assert np.array_equal(c.footprint, b)


# ---------------- baselines.py ----------------

def test_defender_beats_all_baselines(twin, cameras):
    training, held_out = two_seed_sets(twin, 200)
    r = compare_all(twin, cameras, SIDE_DOOR, training, held_out, 0.5)
    assert r["ours"] < r["no_change"]
    assert r["ours"] < r["random_mean"]
    assert r["ours"] < r["coverage_max"]

# ---------------- describe_fix: the three outcomes ----------------

def test_describe_fix_already_safe_door_needs_no_change():
    # This is the front-door bug: 0% success used to say "add a camera".
    search = {"baseline_rate": 0.0, "improved": False}
    assert describe_fix(search, {})["change_type"] == "none_needed"


def test_describe_fix_weak_door_with_no_helpful_rotation_needs_hardware():
    search = {"baseline_rate": 0.60, "improved": False}
    assert describe_fix(search, {})["change_type"] == "none"


def test_describe_fix_weak_door_with_helpful_rotation_recommends_it():
    search = {"baseline_rate": 0.60, "improved": True, "best_camera": "C2",
              "best_angle": 35, "best_rate": 0.40}
    verify = {"held_out_before": 0.6, "held_out_after": 0.4, "genuinely_improved": True}
    fix = describe_fix(search, verify)
    assert fix["change_type"] == "rotate" and fix["camera"] == "C2"


def test_front_door_is_already_well_covered(twin, cameras):
    training, held_out = two_seed_sets(twin, 200)
    front = (11.0, 4.0)
    result = search_best_fix(twin, cameras, front, training, 0.5)
    assert describe_fix(result, {})["change_type"] == "none_needed"