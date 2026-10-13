"""
test_ring_sync.py - tests for app/ring/sync.py (no network, no server).

These cover the part that is easy to get dangerously wrong: pairing the
right Ring device with the right engine camera, and never letting one
camera's outage land on another.
"""
import pytest

from app.ring.reader import RingCamera
from app.ring.sync import (LinkError, all_online, apply_ring_status,
                           describe_state, find_ring_device, parse_links)
from cameras import make_demo_cameras


def ring(device_id, name, online=True, reported_at="2026-10-12T05:00:00Z"):
    return RingCamera(device_id, name, online, reported_at, True, (), ())


DEVICES = [ring("ava1.ring.device.AAA", "Front Door Cam", online=True),
           ring("ava1.ring.device.BBB", "Garage Cam", online=False)]


# ------------------------------------------------------------ parse_links

def test_parse_links_basic():
    assert parse_links(["C1:Front Door Cam", "C3:ava1.ring.device.BBB"]) == {
        "C1": "Front Door Cam", "C3": "ava1.ring.device.BBB"}


def test_parse_links_strips_spaces():
    assert parse_links(["  C1 :  Front Door Cam  "]) == {"C1": "Front Door Cam"}


def test_parse_links_splits_on_the_first_colon_only():
    assert parse_links(["C1:Garage: east"]) == {"C1": "Garage: east"}


def test_parse_links_empty_list_is_fine():
    assert parse_links([]) == {}


@pytest.mark.parametrize("bad", ["C1", "C1:", ":Front Door Cam", "  :  ", ""])
def test_parse_links_rejects_malformed(bad):
    with pytest.raises(LinkError, match="CameraName:RingDeviceNameOrId"):
        parse_links([bad])


def test_parse_links_rejects_the_same_camera_twice():
    with pytest.raises(LinkError, match="C1 is linked twice"):
        parse_links(["C1:Front Door Cam", "C1:Garage Cam"])


# -------------------------------------------------------- find_ring_device

def test_find_by_exact_id():
    assert find_ring_device("ava1.ring.device.BBB", DEVICES).name == "Garage Cam"


def test_find_by_name_ignores_case():
    assert find_ring_device("front door cam", DEVICES).device_id == "ava1.ring.device.AAA"


def test_id_wins_over_a_name_that_happens_to_match_another_device():
    tricky = [ring("Garage Cam", "Something Else"), ring("ava1.ring.device.X", "Garage Cam")]
    assert find_ring_device("Garage Cam", tricky).name == "Something Else"


def test_two_devices_with_the_same_name_is_an_error_not_a_guess():
    twins = [ring("ava1.ring.device.1", "Porch"), ring("ava1.ring.device.2", "Porch")]
    with pytest.raises(LinkError, match="More than one Ring device"):
        find_ring_device("Porch", twins)


def test_duplicate_name_can_still_be_linked_by_id():
    twins = [ring("ava1.ring.device.1", "Porch"), ring("ava1.ring.device.2", "Porch")]
    assert find_ring_device("ava1.ring.device.2", twins).device_id == "ava1.ring.device.2"


def test_unknown_device_lists_what_is_available():
    with pytest.raises(LinkError) as err:
        find_ring_device("Nope", DEVICES)
    assert "'Front Door Cam'" in str(err.value) and "'Garage Cam'" in str(err.value)


def test_unknown_device_with_no_devices_at_all():
    with pytest.raises(LinkError, match="none"):
        find_ring_device("Nope", [])


# -------------------------------------------------------- apply_ring_status

def test_linked_offline_device_takes_its_camera_offline():
    cams, report = apply_ring_status(make_demo_cameras(), DEVICES, {"C1": "Garage Cam"})
    assert [c.online for c in cams] == [False, True, True]
    assert report[0]["source"] == "ring" and report[0]["online"] is False


def test_linked_online_device_keeps_its_camera_online():
    cams, _ = apply_ring_status(make_demo_cameras(), DEVICES, {"C2": "Front Door Cam"})
    assert cams[1].online is True


def test_ring_can_bring_a_simulated_offline_camera_back_online():
    sim = make_demo_cameras()
    sim[0].online = False
    cams, _ = apply_ring_status(sim, DEVICES, {"C1": "Front Door Cam"})
    assert cams[0].online is True


def test_unlinked_cameras_stay_simulated_and_unchanged():
    cams, report = apply_ring_status(make_demo_cameras(), DEVICES, {"C1": "Garage Cam"})
    for entry in report[1:]:
        assert entry["source"] == "simulated"
        assert entry["reported_at"] is None and entry["ring_device_id"] is None
    assert cams[1].online and cams[2].online


def test_unlinked_simulated_offline_camera_stays_offline():
    sim = make_demo_cameras()
    sim[2].online = False
    cams, report = apply_ring_status(sim, DEVICES, {})
    assert cams[2].online is False and report[2]["online"] is False


def test_report_carries_ring_details():
    _, report = apply_ring_status(make_demo_cameras(), DEVICES, {"C3": "Front Door Cam"})
    c3 = report[2]
    assert c3["ring_device_name"] == "Front Door Cam"
    assert c3["ring_device_id"] == "ava1.ring.device.AAA"
    assert c3["reported_at"] == "2026-10-12T05:00:00Z"


def test_the_callers_camera_list_is_never_changed():
    original = make_demo_cameras()
    apply_ring_status(original, DEVICES, {"C1": "Garage Cam"})
    assert all(c.online for c in original)


def test_footprints_are_carried_over_unchanged():
    original = make_demo_cameras()
    cams, _ = apply_ring_status(original, DEVICES, {"C1": "Garage Cam"})
    assert (cams[0].footprint == original[0].footprint).all()
    assert cams[0].position == original[0].position


def test_link_to_a_camera_that_does_not_exist():
    with pytest.raises(LinkError, match="no camera 'C9'"):
        apply_ring_status(make_demo_cameras(), DEVICES, {"C9": "Garage Cam"})


def test_two_cameras_cannot_share_one_ring_device():
    with pytest.raises(LinkError, match="both linked"):
        apply_ring_status(make_demo_cameras(), DEVICES,
                          {"C1": "Garage Cam", "C2": "ava1.ring.device.BBB"})


def test_no_links_means_everything_simulated():
    _, report = apply_ring_status(make_demo_cameras(), DEVICES, {})
    assert {r["source"] for r in report} == {"simulated"}


def test_all_online_resets_every_camera():
    sim = make_demo_cameras()
    sim[0].online = False
    assert all(c.online for c in all_online(sim))
    assert sim[0].online is False            # ...without touching the original


# --------------------------------------------------------- describe_state

def cams_with_offline(*names):
    cams = make_demo_cameras()
    for c in cams:
        c.online = c.name not in names
    return cams


def test_sentence_when_everything_is_online():
    text = describe_state(cams_with_offline(), [])
    assert text == "All 3 cameras are online. No door is more exposed than normal."


def test_sentence_for_one_offline_camera():
    results = [{"door": "front_door", "before": 0.0, "after": 0.23, "change": 0.23},
               {"door": "side_door", "before": 0.6, "after": 0.6, "change": 0.0}]
    text = describe_state(cams_with_offline("C1"), results)
    assert text == ("Camera C1 is offline: front_door attacker success rises "
                    "from 0% to 23% (+23 points).")
    assert "side_door" not in text


def test_sentence_for_several_offline_cameras():
    results = [{"door": "side_door", "before": 0.6, "after": 0.95, "change": 0.35}]
    assert describe_state(cams_with_offline("C1", "C2"), results).startswith(
        "Cameras C1, C2 are offline:")


def test_worst_door_is_listed_first():
    results = [{"door": "front_door", "before": 0.0, "after": 0.1, "change": 0.1},
               {"door": "side_door", "before": 0.6, "after": 0.9, "change": 0.3}]
    text = describe_state(cams_with_offline("C2"), results)
    assert text.index("side_door") < text.index("front_door")


def test_offline_camera_that_changes_nothing_says_so():
    results = [{"door": "front_door", "before": 0.0, "after": 0.0, "change": 0.0}]
    assert describe_state(cams_with_offline("C3"), results) == \
        "Camera C3 is offline. No door is affected."


def test_points_figure_always_equals_the_difference_of_the_rounded_numbers():
    # raw difference is 23.3 points, but the sentence shows 11% -> 35%,
    # so it must say +24, not +23.
    results = [{"door": "side_door", "before": 0.114, "after": 0.347, "change": 0.233}]
    assert "from 11% to 35% (+24 points)" in describe_state(cams_with_offline("C2"), results)


def test_a_door_that_gets_safer_is_reported_as_falling():
    results = [{"door": "side_door", "before": 0.5, "after": 0.3, "change": -0.2}]
    assert "falls from 50% to 30% (-20 points)" in describe_state(cams_with_offline("C1"), results)


def test_a_change_that_rounds_to_nothing_is_not_reported():
    results = [{"door": "side_door", "before": 0.601, "after": 0.604, "change": 0.003}]
    assert describe_state(cams_with_offline("C1"), results).endswith("No door is affected.")