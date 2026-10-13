"""
test_outage.py - what happens when a camera goes offline.
Run from engine/ with:  python -m pytest tests/test_outage.py -v
"""
import os

import numpy as np
import pytest

from twin import Twin
from cameras import make_demo_cameras
from attackers import two_seed_sets
from defender import build_risk, success_rate, with_rotation
from outage import (door_points, with_online, compare_states, outage_effect,
                    single_points_of_failure, describe_change)

SCENARIO = os.path.join(os.path.dirname(__file__), "..", "..", "scenarios", "demo_house.json")
FRONT, SIDE = (11.0, 4.0), (6.0, 9.0)


@pytest.fixture(scope="module")
def twin():
    return Twin.from_file(SCENARIO)


@pytest.fixture(scope="module")
def cameras():
    return make_demo_cameras()


@pytest.fixture(scope="module")
def starts(twin):
    return two_seed_sets(twin, 200)[0]


# ---------------- the online flag ----------------

def test_cameras_are_online_by_default(cameras):
    assert all(c.online for c in cameras)


def test_offline_camera_adds_no_coverage(twin, cameras):
    # An offline camera must behave exactly like a camera that isn't there.
    offline = with_online(cameras, "C1", False)
    removed = [c for c in cameras if c.name != "C1"]
    assert np.array_equal(build_risk(twin, offline), build_risk(twin, removed))


def test_all_cameras_offline_means_no_coverage_at_all(twin, cameras, starts):
    off = cameras
    for c in cameras:
        off = with_online(off, c.name, False)
    assert not build_risk(twin, off).any()               # zero risk everywhere
    assert success_rate(twin, off, FRONT, starts, 0.5) == 1.0
    assert success_rate(twin, off, SIDE, starts, 0.5) == 1.0


def test_rotating_an_offline_camera_keeps_it_offline(cameras):
    # The bug this guards against: rebuilding a Camera by hand used to
    # reset it back to online.
    rotated = with_rotation(with_online(cameras, "C1", False), "C1", 20)
    assert [c.online for c in rotated] == [False, True, True]


def test_with_online_never_changes_the_original_list(cameras):
    with_online(cameras, "C1", False)
    assert all(c.online for c in cameras)


# ---------------- measuring the effect of an outage ----------------

def test_door_points_come_from_the_property(twin):
    assert door_points(twin) == {"front_door": FRONT, "side_door": SIDE}


def test_c1_offline_exposes_the_front_door(twin, cameras, starts):
    effect = {e["door"]: e for e in outage_effect(twin, cameras, "C1", starts, 0.5)}
    assert effect["front_door"]["before"] == 0.0
    assert effect["front_door"]["after"] == pytest.approx(0.25)
    assert effect["side_door"]["change"] == 0.0           # C1 doesn't guard the side door


def test_c2_offline_exposes_the_side_door(twin, cameras, starts):
    effect = {e["door"]: e for e in outage_effect(twin, cameras, "C2", starts, 0.5)}
    assert effect["side_door"]["before"] == pytest.approx(0.62)
    assert effect["side_door"]["after"] == pytest.approx(0.935)    # shown as 94% in sentences


def test_an_outage_never_makes_anything_safer(twin, cameras, starts):
    for cam in cameras:
        for e in outage_effect(twin, cameras, cam.name, starts, 0.5):
            assert e["change"] >= 0


def test_restoring_a_camera_reverses_the_outage(twin, cameras, starts):
    down = with_online(cameras, "C1", False)
    comeback = {e["door"]: e for e in compare_states(twin, down, cameras, starts, 0.5)}
    assert comeback["front_door"]["before"] == pytest.approx(0.25)
    assert comeback["front_door"]["after"] == 0.0


def test_an_already_offline_camera_changes_nothing_further(twin, cameras, starts):
    down = with_online(cameras, "C1", False)
    assert all(e["change"] == 0 for e in outage_effect(twin, down, "C1", starts, 0.5))


def test_results_are_repeatable(twin, cameras, starts):
    assert outage_effect(twin, cameras, "C2", starts, 0.5) == outage_effect(twin, cameras, "C2", starts, 0.5)


# ---------------- single points of failure ----------------

def test_demo_house_cameras_are_each_a_single_point_of_failure(twin, cameras, starts):
    spofs = single_points_of_failure(twin, cameras, starts, 0.5)
    assert [s["camera"] for s in spofs] == ["C2", "C1", "C3"]      # worst first
    assert spofs[0]["change"] == pytest.approx(0.315)


def test_a_high_threshold_finds_none(twin, cameras, starts):
    assert single_points_of_failure(twin, cameras, starts, 0.5, threshold=0.99) == []


def test_offline_cameras_are_not_listed_as_failure_candidates(twin, cameras, starts):
    down = with_online(cameras, "C1", False)
    assert "C1" not in [s["camera"] for s in single_points_of_failure(twin, down, starts, 0.5)]


# ---------------- the plain-English sentence ----------------

def test_sentence_for_an_outage(twin, cameras, starts):
    text = describe_change("C1", outage_effect(twin, cameras, "C1", starts, 0.5))
    assert text == ("Camera C1 went offline: front_door attacker success "
                    "rises from 0% to 25% (+25 points).")


def test_sentence_for_a_camera_coming_back(twin, cameras, starts):
    down = with_online(cameras, "C1", False)
    text = describe_change("C1", compare_states(twin, down, cameras, starts, 0.5), went_offline=False)
    assert text == ("Camera C1 came back online: front_door attacker success "
                    "falls from 25% to 0% (-25 points).")


def test_sentence_when_nothing_changes():
    same = [{"door": "front_door", "before": 0.2, "after": 0.2, "change": 0.0}]
    assert describe_change("C9", same) == "Camera C9 went offline. No door is affected."


def test_every_number_in_the_sentence_comes_from_the_results():
    results = [{"door": "side_door", "before": 0.5, "after": 0.8, "change": 0.3}]
    text = describe_change("C2", results)
    assert "50%" in text and "80%" in text and "+30 points" in text


def test_sentence_numbers_always_add_up():
    # Rounded separately this would read "62% to 94% (+31 points)".
    results = [{"door": "side_door", "before": 0.624, "after": 0.935, "change": 0.311}]
    text = describe_change("C2", results)
    assert "62%" in text and "94%" in text and "+32 points" in text
