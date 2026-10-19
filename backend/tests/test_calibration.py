"""
test_calibration.py - calibrating a camera from its own picture.

The "pictures" here come from a pretend pinhole camera (calibration.project_ground_to_picture), so we know
exactly what the real camera sees and can check RedHouse gets the same answer.
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point

from app import engine_bridge  # noqa: F401
from app import site_store
from app.main import app

import calibration as C
from site_plan import cameras_from_records

client = TestClient(app)

# Demo camera C1: mounted at (11, 1), really facing north. We pretend it is 2.5 m up, tilted 30 degrees down.
CAM = (11.0, 1.0, 2.5)
GROUND = [(9, 4), (13, 4), (14, 7), (8, 7), (12, 5)]


def picture(points, heading=90, tilt=30, fov=100):
    return [list(C.project_ground_to_picture(p, CAM, heading, tilt, fov)) for p in points]


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setattr(site_store, "SITES_DIR", str(tmp_path / "sites"))
    monkeypatch.delenv("REDHOUSE_SITES_BUCKET", raising=False)
    site_store._CACHE.clear()


# ---------------------------------------------------------------- the maths

def test_footprint_matches_what_the_camera_really_sees():
    cal = C.calibrate(picture(GROUND), GROUND, CAM[:2], 9.0)
    poly = C.polygon_of(cal)
    rng = np.random.default_rng(0)
    wrong = 0
    for x, y in rng.uniform(-2, 24, (3000, 2)):
        p = C.project_ground_to_picture((x, y), CAM, 90, 30, 100)
        truth = p is not None and 0 <= p[0] <= 1 and 0 <= p[1] <= 1 and np.hypot(x - 11, y - 1) <= 9
        wrong += truth != poly.contains(Point(x, y))
    assert wrong == 0
    assert cal["checked"] and cal["max_error_m"] < 0.01
    assert abs(cal["heading_deg"] - 90) < 1


def test_a_slightly_misplaced_check_spot_is_a_warning():
    cal = C.calibrate(picture(GROUND), GROUND[:4] + [(12, 6.5)], CAM[:2], 9.0)
    assert cal["max_error_m"] == pytest.approx(1.5, abs=0.05)
    assert any("check spot landed 1.5 m" in w for w in cal["warnings"])


def test_click_wobble_is_tolerated_and_reported():
    rng = np.random.default_rng(1)
    img = [[u + rng.normal(0, 0.005), v + rng.normal(0, 0.005)] for u, v in picture(GROUND)]
    cal = C.calibrate(img, GROUND, CAM[:2], 9.0)
    assert 0 < cal["max_error_m"] < 1.0


def test_four_spots_work_but_say_they_were_not_checked():
    cal = C.calibrate(picture(GROUND[:4]), GROUND[:4], CAM[:2], 9.0)
    assert cal["checked"] is False and cal["max_error_m"] is None
    assert any("5th" in w for w in cal["warnings"])


def test_spots_matched_in_the_wrong_order_are_refused():
    img = picture(GROUND)
    img[0], img[1] = img[1], img[0]
    with pytest.raises(C.CalibrationError, match="order"):
        C.calibrate(img, GROUND, CAM[:2], 9.0)


def test_a_check_spot_in_the_wrong_place_is_refused():
    ground = GROUND[:4] + [(10, 9)]                   # the picture shows (12, 5); the plan click is 4.5 m away
    with pytest.raises(C.CalibrationError, match="check spot"):
        C.calibrate(picture(GROUND), ground, CAM[:2], 9.0)


@pytest.mark.parametrize("img,ground,msg", [
    ([[0.1, 0.5]] * 3, [(0, 0), (1, 0), (0, 1)], "4 to 8"),
    ([[0.1, 0.5], [0.11, 0.5], [0.5, 0.9], [0.9, 0.5]], [(0, 0), (5, 0), (0, 5), (5, 5)], "on top of each other"),
    ([[0.1, 0.5], [0.5, 0.5], [0.9, 0.5], [0.5, 0.9]], [(0, 0), (5, 0), (9, 1), (5, 5)], "straight line"),
    ([[0.1, 1.5], [0.5, 0.5], [0.9, 0.5], [0.5, 0.9]], [(0, 0), (5, 0), (9, 4), (5, 5)], "outside the picture"),
])
def test_unusable_spots_say_what_to_fix(img, ground, msg):
    with pytest.raises(C.CalibrationError, match=msg):
        C.calibrate(img, ground, (0, 0), 9.0)


def test_a_calibrated_record_becomes_a_camera_with_the_measured_footprint():
    cal = C.calibrate(picture(GROUND), GROUND, CAM[:2], 9.0)
    rec = {"name": "C1", "position": [11, 1], "heading_deg": cal["heading_deg"], "fov_deg": cal["fov_deg"],
           "range_m": 9.0, "calibration": cal}
    cam = cameras_from_records([rec], 0.5)[0]
    poly = C.polygon_of(cal)
    assert cam.view_polygon is not None
    assert all(poly.buffer(1e-6).contains(Point(x, y)) for x, y in cam.footprint[:500])


# ---------------------------------------------------------------- the routes

def body(**kw):
    return {"camera": "C1", "image_points": picture(GROUND), "ground_points": [list(p) for p in GROUND], **kw}


def test_preview_saves_nothing():
    r = client.post("/api/calibration/preview", json=body())
    assert r.status_code == 200, r.text
    assert r.json()["checked"] is True and len(r.json()["ground"]) >= 3
    assert site_store.MAX_SITES and not list((site_store.os.path.exists(site_store.SITES_DIR) and
                                              site_store.os.listdir(site_store.SITES_DIR)) or [])


def test_bad_spots_are_a_422_with_a_sentence():
    img = picture(GROUND)
    img[0], img[1] = img[1], img[0]
    r = client.post("/api/calibration/preview", json=body(image_points=img))
    assert r.status_code == 422 and "order" in r.json()["detail"]


def test_unknown_camera_is_404():
    assert client.post("/api/calibration/preview", json=body(camera="C9")).status_code == 404


def test_calibrating_the_demo_saves_a_copy_and_changes_the_results():
    before = client.get("/api/scene").json()
    r = client.post("/api/calibration", json=body(source="ring", captured_at="2026-10-14T03:00:00Z"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True
    site = out["site_id"]

    # the demo itself is untouched
    assert client.get("/api/scene").json()["site"]["cameras"] == before["site"]["cameras"]

    after = client.get("/api/scene", params={"site": site}).json()
    c1 = next(c for c in after["site"]["cameras"] if c["name"] == "C1")
    assert c1["calibrated"] is True and len(c1["view_polygon"]) >= 3
    assert next(c for c in after["site"]["cameras"] if c["name"] == "C2")["calibrated"] is False
    assert after["coverage"] != before["coverage"]
    # the other cameras are rebuilt exactly as in the demo
    assert [c for c in after["site"]["cameras"] if c["name"] != "C1"] == \
           [{**c, "calibrated": False, "view_polygon": None} for c in before["site"]["cameras"] if c["name"] != "C1"]

    stored = site_store.read(site)["cameras"][0]["calibration"]
    assert stored["source"] == "ring" and stored["captured_at"] == "2026-10-14T03:00:00Z"
    assert "image" not in str(stored).replace("image_points", "")        # no picture kept, only spots


def test_turning_a_calibrated_camera_turns_its_view():
    site = client.post("/api/calibration", json=body()).json()["site_id"]
    still = client.get("/api/scene", params={"site": site}).json()
    turned = client.get("/api/scene", params={"site": site, "rotate": "C1:90"}).json()
    a = next(c for c in still["site"]["cameras"] if c["name"] == "C1")["view_polygon"]
    b = next(c for c in turned["site"]["cameras"] if c["name"] == "C1")["view_polygon"]
    # a quarter turn counter-clockwise about (11, 1): (x, y) -> (11 - (y - 1), 1 + (x - 11))
    for (x, y), (tx, ty) in zip(a, b):
        assert abs(tx - (11 - (y - 1))) < 0.05 and abs(ty - (1 + (x - 11))) < 0.05


def test_recalibrating_a_saved_site_updates_it_in_place_and_delete_restores():
    site = client.post("/api/calibration", json=body()).json()["site_id"]
    r = client.post("/api/calibration", params={"site": site}, json=body())
    assert r.status_code == 200 and r.json()["created"] is False and r.json()["site_id"] == site
    assert client.delete("/api/calibration", params={"site": site, "camera": "C1"}).status_code == 204
    cam = site_store.read(site)["cameras"][0]
    assert "calibration" not in cam and cam["heading_deg"] == 90
    assert client.delete("/api/calibration", params={"site": site, "camera": "C1"}).status_code == 404


def test_plan_and_report_work_for_a_calibrated_site():
    site = client.post("/api/calibration", json=body()).json()["site_id"]
    assert client.get("/api/plan", params={"site": site}).status_code == 200
    r = client.get("/api/report", params={"site": site})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_stored_site_lists_which_cameras_are_calibrated():
    site = client.post("/api/calibration", json=body()).json()["site_id"]
    cams = client.get(f"/api/sites/{site}").json()["cameras"]
    assert [c["calibrated"] for c in cams] == [True, False, False]


# ---------------------------------------------------------------- placing the camera from its picture

ASPECT = 16 / 9
TRUE = {"cam": (13.0, 1.0, 2.6), "heading": 100, "tilt": 33, "lens": 100}   # NOT where the demo puts C1 (11, 1)
SPOTS = [(12, 4), (16, 5), (9, 5), (14, 8), (11, 7)]


def shot(lens=100, **kw):
    t = {**TRUE, **kw}
    return [list(C.project_ground_to_picture(p, t["cam"], t["heading"], t["tilt"], lens, aspect=ASPECT)) for p in SPOTS]


def test_the_camera_is_found_from_its_own_picture():
    pose = C.place_camera(shot(), [list(p) for p in SPOTS], ASPECT, 100)
    assert pose["position"] == pytest.approx([13.0, 1.0], abs=0.05)
    assert pose["height_m"] == pytest.approx(2.6, abs=0.05)
    assert pose["heading_deg"] == pytest.approx(100, abs=0.5) and pose["tilt_deg"] == pytest.approx(33, abs=0.5)


def test_a_wrong_lens_guess_is_off_by_about_what_we_say():
    pose = C.place_camera(shot(lens=120), [list(p) for p in SPOTS], ASPECT, 100)       # real lens is wider than assumed
    off = float(np.hypot(pose["position"][0] - 13.0, pose["position"][1] - 1.0))
    assert 0.3 < off < 3.0
    assert pose["uncertainty_m"] > 0.3                                                  # it admits it is unsure


def test_placing_is_off_by_default_and_the_camera_stays_where_it_was():
    r = client.post("/api/calibration/preview", json={**body(), "image_points": shot(), "ground_points": [list(p) for p in SPOTS]})
    assert r.status_code == 200 and r.json()["pose"] is None and r.json()["position"] == [11.0, 1.0]


def test_preview_with_placing_returns_the_pose_and_a_view_from_there():
    r = client.post("/api/calibration/preview", json={**body(), "image_points": shot(), "ground_points": [list(p) for p in SPOTS],
                                                       "place_camera": True, "aspect": ASPECT, "lens_deg": 100})
    out = r.json()
    assert r.status_code == 200, r.text
    assert out["position"] == pytest.approx([13.0, 1.0], abs=0.1) and out["pose"]["height_m"] == pytest.approx(2.6, abs=0.1)


def test_saving_with_placing_moves_the_camera_and_delete_puts_it_back():
    req = {**body(), "image_points": shot(), "ground_points": [list(p) for p in SPOTS],
           "place_camera": True, "aspect": ASPECT, "lens_deg": 100}
    site = client.post("/api/calibration", json=req).json()["site_id"]
    cam = next(c for c in client.get("/api/scene", params={"site": site}).json()["site"]["cameras"] if c["name"] == "C1")
    assert cam["position"] == pytest.approx([13.0, 1.0], abs=0.1)
    assert site_store.read(site)["cameras"][0]["calibration"]["pose"]["lens_deg"] == 100
    assert client.delete("/api/calibration", params={"site": site, "camera": "C1"}).status_code == 204
    rec = site_store.read(site)["cameras"][0]
    assert rec["position"] == [11.0, 1.0] and rec["heading_deg"] == 90


def test_a_camera_that_comes_out_underground_is_refused_in_words():
    # spots matched mirror-wise so no camera above the ground can have taken this picture
    flipped = [[u, 1 - v] for u, v in shot()]
    with pytest.raises(C.CalibrationError):
        C.place_camera(flipped, [list(p) for p in SPOTS], ASPECT, 100)


# ---------------------------------------------------------------- views that wrap around the camera

CROWDED_PICTURE = [[0.46, 0.62], [0.54, 0.60], [0.55, 0.68], [0.47, 0.70], [0.50, 0.65]]
SPREAD_PLAN = [[6, 3], [17, 3], [18, 9], [5, 9], [11, 6]]


@pytest.mark.parametrize("n", [4, 5])
def test_spots_that_make_the_camera_see_all_round_itself_are_refused(n):
    with pytest.raises(C.CalibrationError, match="all the way round"):
        C.calibrate(CROWDED_PICTURE[:n], SPREAD_PLAN[:n], CAM[:2], 9.0)


def test_the_route_says_so_in_words_not_a_circle():
    r = client.post("/api/calibration/preview", json={"camera": "C1", "image_points": CROWDED_PICTURE, "ground_points": SPREAD_PLAN})
    assert r.status_code == 422 and "all the way round" in r.json()["detail"]


def test_four_spots_can_be_previewed_but_not_saved():
    four = body()
    four["image_points"], four["ground_points"] = four["image_points"][:4], four["ground_points"][:4]
    assert client.post("/api/calibration/preview", json=four).status_code == 200
    r = client.post("/api/calibration", json=four)
    assert r.status_code == 422 and "5th spot" in r.json()["detail"]


# ---------------------------------------------------------------- the sample pictures

def test_sample_pictures_give_a_checked_view_and_the_right_camera():
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "lib", "samplePictures.json")
    if not os.path.exists(path):
        pytest.skip("frontend folder not present")
    samples = json.load(open(path))
    assert set(samples) == {"C1", "C2", "C3"}
    from cameras import make_demo_cameras
    cams = {c.name: c for c in make_demo_cameras()}
    for name, s in samples.items():
        img = [sp["img"] for sp in s["spots"]]
        gnd = [sp["ground"] for sp in s["spots"]]
        assert len(img) == 5 and tuple(s["camera"]) == tuple(cams[name].position)
        cal = C.calibrate(img, gnd, tuple(s["camera"]), cams[name].max_range_m)
        assert cal["checked"] and cal["max_error_m"] < 0.05 and not cal["warnings"]
        pose = C.place_camera(img, gnd, s["aspect"], s["lens_deg"])
        assert np.hypot(pose["position"][0] - s["camera"][0], pose["position"][1] - s["camera"][1]) < 0.1
        assert 0 <= pose["heading_deg"] < 360
        assert abs(((pose["heading_deg"] - cams[name].heading_deg + 180) % 360) - 180) < 2
