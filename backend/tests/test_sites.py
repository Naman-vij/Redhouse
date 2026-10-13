"""
test_sites.py - upload a plan, keep the site, analyse it, delete it.

Runs the real engine on a generated 20 x 12 m chemistry lab (made from a
picture, like a real upload). Nothing is mocked except where the files go.
"""
import base64
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import site_store
from app.routes import sites as sites_route

import make_test_lab as lab

client = TestClient(app)


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    """Every test saves into its own empty folder."""
    monkeypatch.setattr(site_store, "SITES_DIR", str(tmp_path / "sites"))
    site_store._CACHE.clear()


@pytest.fixture(scope="module")
def lab_png(tmp_path_factory):
    path = lab.make_lab(str(tmp_path_factory.mktemp("lab") / "lab.png"))
    return open(path, "rb").read()


def settings(**over):
    s = {**lab.user_clicks(), "mode": "indoor", "name": "Chem lab"}
    s.update(over)
    return json.dumps(s)


def upload(png, path="/api/sites", **over):
    return client.post(path, files={"file": ("plan.png", png, "image/png")}, data={"settings": settings(**over)})


@pytest.fixture()
def site_id(lab_png):
    r = upload(lab_png)
    assert r.status_code == 201, r.text
    return r.json()["site_id"]


# ------------------------------------------------------------------ check (saves nothing)

def test_check_reports_what_was_found_and_saves_nothing(lab_png, tmp_path):
    r = upload(lab_png, "/api/sites/check")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["mode"] == "indoor" and abs(body["meta"]["area_m2"] - 219) < 8
    assert body["warnings"] == []
    assert {"boundary", "obstacles", "entrances", "assets", "cameras"} <= set(body["overlay"])
    assert 0.2 < body["preview"]["watched_frac"] < 0.45
    assert any(s["near"] == "server_room" for s in body["preview"]["blind_spots"])
    assert not (tmp_path / "sites").exists() or not list((tmp_path / "sites").iterdir())


# ------------------------------------------------------------------ create / read / delete

def test_create_returns_an_unguessable_id(lab_png):
    r = upload(lab_png)
    assert r.status_code == 201
    sid = r.json()["site_id"]
    assert site_store.valid_id(sid) and len(sid) >= 16
    assert r.headers["cache-control"] == "no-store"


def test_only_the_shapes_are_stored_never_the_picture(site_id, tmp_path):
    raw = open(site_store._path(site_id), "rb").read()
    assert b"PNG" not in raw[:64] and len(raw) < 200_000
    doc = json.loads(raw)
    assert set(doc) == {"version", "created", "meta", "twin", "cameras"}
    assert doc["twin"]["mode"] == "indoor" and len(doc["cameras"]) == 3


def test_get_site_describes_it(site_id):
    body = client.get(f"/api/sites/{site_id}").json()
    assert body["meta"]["name"] == "Chem lab"
    assert [c["name"] for c in body["cameras"]] == ["C1", "C2", "C3"]


def test_delete_forgets_the_site(site_id):
    assert client.delete(f"/api/sites/{site_id}").status_code == 204
    assert client.get(f"/api/sites/{site_id}").status_code == 404
    assert client.get("/api/scene", params={"site": site_id}).status_code == 404
    assert client.delete(f"/api/sites/{site_id}").status_code == 404


def test_a_site_needs_a_camera(lab_png):
    r = upload(lab_png, cameras=[])
    assert r.status_code == 422 and "camera" in r.json()["detail"].lower()


def test_store_has_a_cap(lab_png, monkeypatch):
    monkeypatch.setattr(site_store, "MAX_SITES", 0)
    assert upload(lab_png).status_code == 507


# ------------------------------------------------------------------ the rest of the API takes ?site=

def test_scene_for_an_uploaded_floor(site_id):
    body = client.get("/api/scene", params={"site": site_id}).json()
    site = body["site"]
    assert site["mode"] == "indoor" and site["name"] == "Chem lab"
    assert [c["name"] for c in site["cameras"]] == ["C1", "C2", "C3"]
    assert {e["id"] for e in site["entrances"]} == {"server_room", "freezer"}
    assert {e["id"] for e in site["entries"]} == {"main_entrance", "fire_exit"}
    assert len(body["coverage"]) == site["n_rows"] and len(body["coverage"][0]) == site["n_cols"]
    floor = body["floor"]
    assert 0.2 < floor["watched_frac"] < 0.45 and floor["blind_runs"]
    assert floor["watched_frac"] == floor["watched_normal"] == floor["watched_original"]
    assert "of the open floor is watched" in body["summary"]
    assert "blind_mask" not in floor and "prob" not in floor


def test_blind_runs_match_the_blind_area(site_id):
    body = client.get("/api/scene", params={"site": site_id}).json()
    g = body["site"]["grid_m"]
    cells = sum(b - a for _, a, b in body["floor"]["blind_runs"])
    assert abs(cells * g * g - body["floor"]["blind_m2"]) < 1.0


def test_offline_camera_costs_floor_and_says_so(site_id):
    body = client.get("/api/scene", params={"site": site_id, "offline": "C1"}).json()
    f = body["floor"]
    assert f["watched_frac"] < f["watched_normal"]
    assert "Camera C1 is offline" in body["summary"] and "falls from" in body["summary"]


def test_turning_a_camera_moves_its_heading_and_the_floor(site_id):
    base = client.get("/api/scene", params={"site": site_id}).json()
    turned = client.get("/api/scene", params={"site": site_id, "rotate": "C3:90"}).json()
    h0 = next(c for c in base["site"]["cameras"] if c["name"] == "C3")["heading_deg"]
    h1 = next(c for c in turned["site"]["cameras"] if c["name"] == "C3")["heading_deg"]
    assert (h1 - h0) % 360 == 90
    assert turned["floor"]["watched_frac"] != base["floor"]["watched_frac"]
    assert turned["floor"]["watched_original"] == base["floor"]["watched_original"]


def test_adding_a_camera_previews_more_floor(site_id):
    base = client.get("/api/scene", params={"site": site_id}).json()
    added = client.get("/api/scene", params={"site": site_id, "add": "15.5:11.4:270"}).json()
    new = [c for c in added["site"]["cameras"] if c["proposed"]]
    assert len(new) == 1 and new[0]["fov_deg"] == 110
    assert added["floor"]["watched_frac"] > base["floor"]["watched_frac"]


def test_a_camera_cannot_be_put_inside_a_wall(site_id):
    r = client.get("/api/scene", params={"site": site_id, "add": "12.0:8.0:0"})   # inside the wall between the lab and the store
    assert r.status_code == 400 and "wall" in r.json()["detail"]
    assert client.get("/api/scene", params={"site": site_id, "add": "-5:3:0"}).status_code == 400


def test_plan_for_an_uploaded_floor(site_id):
    body = client.get("/api/plan", params={"site": site_id}).json()
    assert body["mode"] == "indoor"
    more = body["more_cameras"]
    assert more["needed"] and more["additions"] and more["watched_after"] > 0.9
    # previewing the plan's own answer through /api/scene gives the same floor figure
    scene = client.get("/api/scene", params={"site": site_id, "add": more["add_param"]}).json()
    assert abs(scene["floor"]["watched_frac"] - more["watched_after"]) < 0.001


def test_plan_best_rotation_params_preview_cleanly(site_id):
    body = client.get("/api/plan", params={"site": site_id}).json()
    p = body["best_existing"]["rotate_param"]
    r = client.get("/api/scene", params={"site": site_id, "rotate": p})
    assert r.status_code == 200


def test_outage_for_an_uploaded_floor(site_id):
    body = client.get("/api/outage", params={"site": site_id}).json()
    assert body["mode"] == "indoor"
    rows = {r["camera"]: r for r in body["cameras"]}
    assert set(rows) == {"C1", "C2", "C3"}
    assert all("floor" in r and "went offline" in r["sentence"] for r in rows.values())
    assert all(w["watched_after"] < w["watched_before"] for w in body["single_points_of_failure"])


def test_two_sites_do_not_mix(lab_png):
    a = upload(lab_png, name="Lab A").json()["site_id"]
    b = upload(lab_png, name="Lab B", cameras=lab_cameras(1)).json()["site_id"]
    assert len(client.get("/api/scene", params={"site": a}).json()["site"]["cameras"]) == 3
    assert len(client.get("/api/scene", params={"site": b}).json()["site"]["cameras"]) == 1


def lab_cameras(n):
    return lab.user_clicks()["cameras"][:n]


def test_no_site_means_the_demo_house():
    body = client.get("/api/scene").json()
    assert body["site"]["mode"] == "outdoor" and [c["name"] for c in body["site"]["cameras"]] == ["C1", "C2", "C3"]
    assert body["floor"] is None


# ------------------------------------------------------------------ bad ids

@pytest.mark.parametrize("bad", ["../../etc/passwd", "..%2f..%2fetc", "short", "x" * 100, "a b c d e f g h i j k l", "AAAAAAAAAAAAAAAA/../x", "%00"])
def test_odd_ids_are_just_not_found(bad):
    assert client.get("/api/scene", params={"site": bad}).status_code == 404
    assert client.get(f"/api/sites/{bad}").status_code == 404
    assert client.delete(f"/api/sites/{bad}").status_code in (404, 405)


def test_unknown_but_well_formed_id_is_not_found():
    assert client.get("/api/scene", params={"site": "A" * 16}).status_code == 404


def test_store_refuses_to_build_a_path_from_a_bad_id():
    with pytest.raises(ValueError):
        site_store._path("../../x")


# ------------------------------------------------------------------ bad uploads

def test_wrong_file_type(lab_png):
    r = client.post("/api/sites/check", files={"file": ("plan.png", b"hello, not a picture", "image/png")}, data={"settings": settings()})
    assert r.status_code == 415                      # judged by the first bytes, not the name


def test_empty_file():
    r = client.post("/api/sites/check", files={"file": ("a.png", b"", "image/png")}, data={"settings": settings()})
    assert r.status_code == 400


def test_too_big(lab_png, monkeypatch):
    monkeypatch.setattr(sites_route, "MAX_UPLOAD_BYTES", 1000)
    assert upload(lab_png, "/api/sites/check").status_code == 413


def test_a_tiny_file_that_claims_a_huge_picture_is_refused_before_decoding():
    import struct, zlib
    ihdr = struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    bomb = b"\x89PNG\r\n\x1a\n" + chunk
    r = client.post("/api/sites/check", files={"file": ("a.png", bomb, "image/png")}, data={"settings": settings()})
    assert r.status_code == 413


def test_truncated_png_is_a_clean_error(lab_png):
    r = client.post("/api/sites/check", files={"file": ("a.png", lab_png[:300], "image/png")}, data={"settings": settings()})
    assert r.status_code == 400 and "could not be read" in r.json()["detail"]


def test_pdf_goes_through_preview_not_straight_in():
    r = client.post("/api/sites/check", files={"file": ("a.pdf", b"%PDF-1.4 ...", "application/pdf")}, data={"settings": settings()})
    assert r.status_code == 415 and "preview" in r.json()["detail"]


@pytest.mark.parametrize("raw", ["not json", "[]", "5"])
def test_bad_settings(lab_png, raw):
    r = client.post("/api/sites/check", files={"file": ("a.png", lab_png, "image/png")}, data={"settings": raw})
    assert r.status_code == 400


def test_settings_that_are_too_large(lab_png):
    r = client.post("/api/sites/check", files={"file": ("a.png", lab_png, "image/png")}, data={"settings": json.dumps({"x": "y" * 70_000})})
    assert r.status_code == 413


def test_missing_scale_explains_itself(lab_png):
    s = {**lab.user_clicks(), "mode": "indoor"}
    del s["scale"]
    r = client.post("/api/sites/check", files={"file": ("a.png", lab_png, "image/png")}, data={"settings": json.dumps(s)})
    assert r.status_code in (400, 422) and "detail" in r.json()


def test_wrong_types_in_the_settings_do_not_crash(lab_png):
    r = upload(lab_png, "/api/sites/check", inside="oops")
    assert r.status_code in (400, 422)
    r = upload(lab_png, "/api/sites/check", cameras=[{"name": "C1", "position": "here"}])
    assert r.status_code in (400, 422)


def test_entrance_in_a_wall_explains_itself(lab_png):
    wall = [{"name": "x", "point": lab.to_px(12.0, 8.0)}]
    r = upload(lab_png, "/api/sites/check", entrances=wall)
    assert r.status_code == 422 and "entrance" in r.json()["detail"].lower()


def test_error_messages_do_not_leak_paths(lab_png):
    r = upload(lab_png, "/api/sites/check", inside=[5, 5])
    assert "/home" not in r.text and "Traceback" not in r.text


# ------------------------------------------------------------------ pdf / image preview

def _pdf_with(png_bytes, pages=1):
    import pymupdf
    doc = pymupdf.open()
    for _ in range(pages):
        pg = doc.new_page(width=842, height=595)
        pg.insert_image(pg.rect, stream=png_bytes)
    out = doc.tobytes()
    doc.close()
    return out


def test_pdf_preview_gives_a_png(lab_png):
    r = client.post("/api/plans/preview", files={"file": ("plan.pdf", _pdf_with(lab_png, 3), "application/pdf")}, data={"page": "1"})
    assert r.status_code == 200
    b = r.json()
    assert b["pages"] == 3 and b["page"] == 1 and max(b["width"], b["height"]) == 2400
    png = base64.b64decode(b["png_base64"])
    assert png.startswith(b"\x89PNG") and cv2.imdecode(np.frombuffer(png, np.uint8), 1).shape[1] == b["width"]


def test_pdf_page_out_of_range_is_clamped(lab_png):
    r = client.post("/api/plans/preview", files={"file": ("a.pdf", _pdf_with(lab_png, 2), "application/pdf")}, data={"page": "99"})
    assert r.json()["page"] == 1


def test_a_plan_made_from_a_pdf_page_builds(lab_png):
    prev = client.post("/api/plans/preview", files={"file": ("a.pdf", _pdf_with(lab_png), "application/pdf")}).json()
    assert prev["width"] > 1000
    # the page is drawn at a different size, so the clicks change; this checks it reads as a floor plan at all
    img = base64.b64decode(prev["png_base64"])
    assert client.post("/api/sites/check", files={"file": ("a.png", img, "image/png")}, data={"settings": json.dumps({"mode": "indoor"})}).status_code in (400, 422)


def test_corrupt_pdf_is_a_clean_error():
    r = client.post("/api/plans/preview", files={"file": ("a.pdf", b"%PDF-1.4\nthis is not really a pdf", "application/pdf")})
    assert r.status_code == 400 and "PDF could not be read" in r.json()["detail"]


def test_image_preview_returns_the_image(lab_png):
    r = client.post("/api/plans/preview", files={"file": ("a.png", lab_png, "image/png")})
    b = r.json()
    assert r.status_code == 200 and base64.b64decode(b["png_base64"]) == lab_png and b["pages"] == 1


def test_preview_rejects_other_types():
    assert client.post("/api/plans/preview", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")}).status_code == 415


# ------------------------------------------------------------------ an outdoor site uploads and analyses the same way

def test_outdoor_site_round_trip(tmp_path):
    import make_test_plan as T
    path = tmp_path / "plan.png"
    _, scale_pts, inside = T.make_plan(str(path))
    cams = [{"name": "C1", "position": T.to_px(11, 1), "heading_deg": 90, "fov_deg": 90, "range_m": 9},
            {"name": "C2", "position": T.to_px(2, 2), "heading_deg": 0, "fov_deg": 90, "range_m": 9}]
    s = {"mode": "outdoor", "name": "Test house", "scale": {"p1": scale_pts[0], "p2": scale_pts[1], "metres": scale_pts[2]},
         "inside": inside, "doors": [{"name": "front door", "point": T.to_px(11, 4)}, {"name": "side door", "point": T.to_px(6, 9)}],
         "cameras": cams}
    r = client.post("/api/sites", files={"file": ("p.png", path.read_bytes(), "image/png")}, data={"settings": json.dumps(s, default=float)})
    assert r.status_code == 201, r.text
    sid = r.json()["site_id"]
    scene = client.get("/api/scene", params={"site": sid}).json()
    assert scene["site"]["mode"] == "outdoor" and scene["floor"] is None
    assert {e["id"] for e in scene["site"]["entrances"]} == {"front_door", "side_door"}
    fix = client.get("/api/demo/audit", params={"site": sid, "door": "side_door"})
    assert fix.status_code == 200 and fix.json()["door"] == "side_door"
    plan = client.get("/api/plan", params={"site": sid}).json()
    assert plan.get("mode") is None and "best_existing" in plan       # outdoor keeps its own plan format
    assert client.get("/api/outage", params={"site": sid}).json()["mode"] == "outdoor"
