"""
tests/test_site_plan.py

Tests for reading a site plan image and turning it into the engine's map.

The test plan is DRAWN by make_test_plan.py, so we know the true answer
and can assert on the error, not just that "something came out".
"""

import numpy as np
import pytest
from shapely.geometry import Polygon

import make_test_plan as T
from site_plan import (PlanError, load_plan_image, scale_from_two_points,
                       extract_plan, to_twin_dict, cameras_from_specs,
                       heading_from_aim, draw_overlay, detect_doors,
                       _colour_masks)
from twin import Twin
from attackers import two_seed_sets
from defender import build_risk, search_best_fix, verify_on_held_out
from metrics import audit_door


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    path = tmp_path_factory.mktemp("plan") / "test_plan.png"
    p, scale_pts, inside = T.make_plan(str(path))
    img = load_plan_image(p)
    ppm = scale_from_two_points(scale_pts[0], scale_pts[1], scale_pts[2])
    return img, ppm, inside


@pytest.fixture(scope="module")
def ex(plan):
    img, ppm, inside = plan
    return extract_plan(img, ppm, inside_px=inside)


SPECS = [
    {"name": "C1", "position": {"m": [11.0, 1.0]}, "heading": 90, "fov_deg": 90, "range_m": 9.0},
    {"name": "C2", "position": {"m": [2.0, 2.0]}, "heading": 0, "fov_deg": 90, "range_m": 9.0},
    {"name": "C3", "position": {"m": [21.0, 8.0]}, "heading": 180, "fov_deg": 90, "range_m": 9.0},
]


def iou(a, b):
    pa, pb = Polygon(a), Polygon(b)
    return pa.intersection(pb).area / pa.union(pb).area


# ------------------------------------------------------------------ scale

def test_scale_matches_the_drawing():
    p1, p2 = T.to_px(0, 0), T.to_px(24, 0)
    assert scale_from_two_points(p1, p2, 24.0) == pytest.approx(T.PPM, rel=1e-3)


def test_scale_rejects_points_too_close():
    with pytest.raises(PlanError):
        scale_from_two_points((10, 10), (12, 10), 5.0)


def test_scale_rejects_zero_distance():
    with pytest.raises(PlanError):
        scale_from_two_points((0, 0), (400, 0), 0.0)


# -------------------------------------------------------------- detection

def test_boundary_size_within_15cm(ex):
    xs = [p[0] for p in ex.boundary_m]
    ys = [p[1] for p in ex.boundary_m]
    assert max(xs) - min(xs) == pytest.approx(24.0, abs=0.15)
    assert max(ys) - min(ys) == pytest.approx(18.0, abs=0.15)


def test_boundary_overlaps_truth(ex):
    assert iou(ex.boundary_m, T.TRUE_BOUNDARY) > 0.99


def test_exactly_one_building_found(ex):
    """The page frame, title block, dimension text and north arrow must
    all be ignored - only the dwelling counts."""
    assert len(ex.obstacles_m) == 1


def test_building_overlaps_truth(ex):
    assert iou(ex.obstacles_m[0], T.TRUE_HOUSE) > 0.95


def test_both_doors_found_within_25cm(ex):
    assert len(ex.doors_m) == 2
    for true_pt in T.TRUE_DOORS.values():
        best = min(np.hypot(x - true_pt[0], y - true_pt[1]) for x, y in ex.doors_m)
        assert best < 0.25


def test_doors_sit_on_the_building_edge(ex):
    edge = Polygon(ex.obstacles_m[0]).exterior
    for x, y in ex.doors_m:
        assert edge.distance(Polygon(ex.obstacles_m[0]).exterior.interpolate(
            edge.project(__import__("shapely.geometry", fromlist=["Point"]).Point(x, y)))) < 1e-6


def test_green_marks_do_not_break_the_wall(plan):
    """A door mark sits on a wall; it must not punch a hole in it."""
    img, _, _ = plan
    dark, green = _colour_masks(img, 110)
    assert green.sum() > 0
    # every green pixel is treated as wall in the geometry mask
    assert dark[green > 0].all()


def test_no_warnings_on_a_clean_plan(ex):
    assert ex.warnings == []


def test_unclosed_boundary_is_reported(tmp_path):
    """An open outline must fail loudly, not produce a silent wrong map."""
    import cv2
    img, _, inside = (lambda p: (load_plan_image(p[0]), p[1], p[2]))(
        T.make_plan(str(tmp_path / "p.png")))
    cv2.rectangle(img, T.to_px(10, -1), T.to_px(16, 1), (255, 255, 255), -1)  # erase a wall
    with pytest.raises(PlanError, match="not closed"):
        extract_plan(img, T.PPM, inside_px=inside)


def test_inside_click_outside_image_is_reported(plan):
    img, ppm, _ = plan
    with pytest.raises(PlanError, match="outside the image"):
        extract_plan(img, ppm, inside_px=(-5, -5))


def test_detect_doors_ignores_specks():
    assert detect_doors(np.zeros((40, 40), np.uint8)) == []


# ----------------------------------------------------------------- twin

def test_twin_dict_builds_a_working_twin(ex):
    twin = Twin(to_twin_dict(ex, grid_m=0.5, door_ids=["a", "b"]))
    assert twin.n_cols >= 48 and twin.n_rows >= 36
    assert twin.blocked.sum() > 0           # the house blocks cells
    assert not twin.blocked.all()


def test_twin_dict_rejects_wrong_number_of_door_names(ex):
    with pytest.raises(PlanError):
        to_twin_dict(ex, door_ids=["only_one"])


def test_grid_is_stable_against_small_plan_errors(ex):
    """
    The headline number must not move when the traced outline is a few
    centimetres out. This is the bug that cost us 6 points earlier.
    """
    rates = []
    for pad in (0.0, 0.05, 0.2, 0.36):
        data = to_twin_dict(ex, grid_m=0.5, door_ids=["a", "b"])
        data["boundary"] = [[0, 0], [24 + pad, 0], [24 + pad, 18], [0, 18]]
        twin = Twin(data)
        cams, _ = cameras_from_specs(SPECS, ex)
        _, held = two_seed_sets(twin, 200)
        rates.append(audit_door(twin, (6.0, 9.0), build_risk(twin, cams),
                                held, 0.5)["success_rate"])
    assert max(rates) - min(rates) < 0.02


def test_every_start_point_is_usable(ex):
    """No attacker may be silently dropped for starting on a blocked cell."""
    twin = Twin(to_twin_dict(ex, grid_m=0.5, door_ids=["a", "b"]))
    cams, _ = cameras_from_specs(SPECS, ex)
    _, held = two_seed_sets(twin, 200)
    r = audit_door(twin, (6.0, 9.0), build_risk(twin, cams), held, 0.5)
    assert r["n_reached"] == 200


# --------------------------------------------------------------- cameras

def test_heading_from_two_clicks():
    assert heading_from_aim((100, 100), (200, 100)) == pytest.approx(0)      # east
    assert heading_from_aim((100, 100), (100, 50)) == pytest.approx(90)      # north
    assert heading_from_aim((100, 100), (100, 150)) == pytest.approx(270)    # south


def test_heading_rejects_identical_points():
    with pytest.raises(PlanError):
        heading_from_aim((10, 10), (10, 10))


def test_cameras_built_at_the_right_places(ex):
    cams, warns = cameras_from_specs(SPECS, ex)
    assert [c.name for c in cams] == ["C1", "C2", "C3"]
    assert cams[1].position == pytest.approx((2.0, 2.0))
    assert all(c.online for c in cams)
    assert warns == []


def test_camera_outside_the_property_warns(ex):
    specs = [dict(SPECS[0], position={"m": [100.0, 100.0]})]
    _, warns = cameras_from_specs(specs, ex)
    assert any("outside the property" in w for w in warns)


def test_camera_inside_a_building_warns(ex):
    specs = [dict(SPECS[0], position={"m": [11.0, 8.0]})]
    _, warns = cameras_from_specs(specs, ex)
    assert any("inside a building" in w for w in warns)


def test_bad_field_of_view_is_rejected(ex):
    with pytest.raises(PlanError):
        cameras_from_specs([dict(SPECS[0], fov_deg=0)], ex)


def test_bad_range_is_rejected(ex):
    with pytest.raises(PlanError):
        cameras_from_specs([dict(SPECS[0], range_m=-1)], ex)


def test_offline_camera_is_carried_through(ex):
    cams, _ = cameras_from_specs([dict(SPECS[0], online=False)], ex)
    assert cams[0].online is False


def test_pixel_positions_match_metre_positions(ex):
    by_m, _ = cameras_from_specs(SPECS, ex)
    by_px, _ = cameras_from_specs(
        [dict(s, position={"px": ex.frame.to_px(*s["position"]["m"])}) for s in SPECS], ex)
    for a, b in zip(by_m, by_px):
        assert a.position == pytest.approx(b.position, abs=0.01)


# ------------------------------------------------------- the whole chain

def test_image_plan_gives_the_same_verdict_as_the_hand_written_plan(ex):
    """
    The point of the whole feature: a plan read from an image must lead to
    the SAME recommendation as the hand-measured scenario.
    """
    twin = Twin(to_twin_dict(ex, grid_m=0.5, door_ids=["a", "b"]))
    cams, _ = cameras_from_specs(SPECS, ex)
    side_door = min(ex.doors_m, key=lambda p: p[0])      # the west-side door
    train, held = two_seed_sets(twin, 200)

    before = audit_door(twin, side_door, build_risk(twin, cams), held,
                        0.5)["success_rate"]
    res = search_best_fix(twin, cams, side_door, train, 0.5)
    ver = verify_on_held_out(twin, cams, side_door, held, 0.5,
                             res["best_camera"], res["best_angle"])

    assert before == pytest.approx(0.60, abs=0.05)       # hand-written plan: 0.60
    assert res["best_camera"] == "C2"                    # the planted mistake
    assert res["best_angle"] > 0                         # counter-clockwise
    assert ver["held_out_after"] == pytest.approx(0.445, abs=0.05)
    assert ver["genuinely_improved"]


def test_front_door_is_already_covered(ex):
    twin = Twin(to_twin_dict(ex, grid_m=0.5, door_ids=["a", "b"]))
    cams, _ = cameras_from_specs(SPECS, ex)
    front = max(ex.doors_m, key=lambda p: p[0])
    _, held = two_seed_sets(twin, 200)
    assert audit_door(twin, front, build_risk(twin, cams), held,
                      0.5)["success_rate"] == 0.0


def test_overlay_draws_without_error(plan, ex):
    img, _, _ = plan
    cams, _ = cameras_from_specs(SPECS, ex)
    out = draw_overlay(img, ex, cams, SPECS)
    assert out.shape == img.shape
    assert not np.array_equal(out, img)       # something was actually drawn
