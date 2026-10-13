"""test_planner.py - the planner's rules, on the demo house."""
import pathlib

import pytest

from attackers import ARCHETYPES, two_seed_sets
from cameras import make_demo_cameras
from planner import (TARGET, apply_angles, best_rotations, candidate_mounts,
                     make_new_camera, plan_additions)
from twin import Twin

SCENARIO = pathlib.Path(__file__).resolve().parents[2] / "scenarios" / "demo_house.json"


@pytest.fixture(scope="module")
def world():
    twin = Twin.from_file(SCENARIO)
    training, held_out = two_seed_sets(twin, 200)
    return twin, make_demo_cameras(), training, held_out


def test_mount_points_are_never_inside_the_house_or_off_the_property(world):
    twin, cams, _, _ = world
    mounts = candidate_mounts(twin, cams)
    assert len(mounts) >= 8
    for x, y in mounts:
        r, c = twin.cell_of(x, y)
        assert not twin.blocked[r, c]


def test_no_mount_is_stacked_on_an_existing_camera(world):
    twin, cams, _, _ = world
    for x, y in candidate_mounts(twin, cams):
        assert all(((x - c.position[0]) ** 2 + (y - c.position[1]) ** 2) ** 0.5 >= 1.0 for c in cams)


def test_a_rebuilt_camera_is_identical():
    a = make_new_camera("N1", (5.3, 3.3), 90)
    b = make_new_camera("N9", (5.3, 3.3), 90)
    assert (a.footprint == b.footprint).all()


def test_best_rotation_is_not_worse_than_doing_nothing(world):
    twin, cams, training, _ = world
    from defender import success_rate
    from outage import door_points
    rw = ARCHETYPES[0].risk_weight
    res = best_rotations(twin, cams, training, rw)
    doors = door_points(twin)
    now = sum(success_rate(twin, cams, p, training, rw) for p in doors.values())
    assert sum(res["training_rates"].values()) <= now


def test_rotation_angles_are_applied_to_a_copy(world):
    twin, cams, _, _ = world
    before = cams[1].footprint.copy()
    turned = apply_angles(cams, {"C2": 45})
    assert (cams[1].footprint == before).all()              # original untouched
    assert not (turned[1].footprint == before).all()


def test_additions_stop_once_every_door_is_under_target(world):
    twin, cams, training, _ = world
    res = plan_additions(twin, cams, training, ARCHETYPES[0].risk_weight)
    assert max(res["training_rates"].values()) <= TARGET
    assert 1 <= len(res["added"]) <= 3


def test_no_cameras_added_when_already_under_target(world):
    twin, cams, training, _ = world
    first = plan_additions(twin, cams, training, ARCHETYPES[0].risk_weight)
    from planner import make_new_camera as mk
    done = cams + [mk(a["name"], a["position"], a["heading_deg"]) for a in first["added"]]
    again = plan_additions(twin, done, training, ARCHETYPES[0].risk_weight)
    assert again["added"] == []
