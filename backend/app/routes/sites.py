"""
sites.py (route)

Upload a floor plan or a site plan, say where the cameras are, and get a
site the rest of the API can analyse.

    POST   /api/plans/preview     a PDF or image  ->  a PNG the page can show and click on
    POST   /api/sites/check       the plan + your clicks  ->  what we found (nothing is saved)
    POST   /api/sites             the same, and keeps the result  ->  {site_id}
    GET    /api/sites/{id}        what is stored (names and sizes only)
    DELETE /api/sites/{id}        forget it

    POST   /api/calibration/preview?site=   matching spots on a camera's picture and on the plan -> the ground
                                            that camera really sees (nothing is saved)
    POST   /api/calibration?site=           the same, and keep it with the camera. For the demo house (no
                                            site) a copy of the demo is saved, and its new id comes back
    DELETE /api/calibration?site=&camera=   go back to the guessed view for that camera

Every other endpoint then takes  ?site=<id>  (no id means the demo house).

SECURITY
  - Uploads are limited by size, by pixel count and by type (PNG, JPEG, PDF
    are recognised from their first bytes, not from the file name).
  - A PDF is only ever turned into a picture; nothing in it is run.
  - The picture is not stored, only the shapes we extracted from it.
  - Error messages say what to fix. They never include file contents or
    server paths.
"""

import base64
import json
import struct

import cv2
import numpy as np
from shapely.geometry import Point
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app import site_store
from app.engine_bridge import SCENARIO_PATH

import calibration
import indoor
import site_plan
from cameras import Camera
from twin import Twin

router = APIRouter(prefix="/api", tags=["sites"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024        # also under the AWS Lambda request limit
MAX_PIXELS = 40_000_000                   # a decoded picture of this size is ~120 MB; more is refused
MAX_SETTINGS_BYTES = 64 * 1024
PDF_LONG_SIDE_PX = 2400                   # a PDF page is drawn this big
MAX_PDF_POINTS = 14_400                   # 200 inches: a poster, not a bomb


# ----------------------------------------------------------------------
# Reading what was sent
# ----------------------------------------------------------------------

def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"That file is too big. The limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    return data


def _kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def _png_size(data: bytes):
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes):
    """Width and height from the first 'start of frame' marker, without decoding the picture."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def _decode(data: bytes, kind: str):
    """A PNG or JPEG -> an image array, after checking its size WITHOUT decoding it first."""
    size = _png_size(data) if kind == "png" else _jpeg_size(data)
    if not size or size[0] < 1 or size[1] < 1:
        raise HTTPException(status_code=400, detail="That picture could not be read.")
    if size[0] * size[1] > MAX_PIXELS:
        raise HTTPException(status_code=413, detail="That picture is too large. Resize it to under 6000 pixels on its longest side.")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="That picture could not be read.")
    return img


def _settings(raw: str) -> dict:
    if len(raw.encode()) > MAX_SETTINGS_BYTES:
        raise HTTPException(status_code=413, detail="The settings are too large.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="The settings are not valid JSON.") from None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="The settings must be an object.")
    return value


# ----------------------------------------------------------------------
# PDF / image -> a picture the page can show
# ----------------------------------------------------------------------

@router.post("/plans/preview")
def preview_plan(file: UploadFile = File(...), page: int = Form(default=0)):
    """
    Turn an upload into a PNG. A PDF is drawn (page `page`, counting from 0); a PNG or JPEG is checked
    and returned as it is. The page then shows this picture, and every click on it is in ITS pixels.
    """
    data = _read_upload(file)
    kind = _kind(data)
    if kind is None:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG or PDF.")

    if kind != "pdf":
        img = _decode(data, kind)
        h, w = img.shape[:2]
        return {"png_base64": base64.b64encode(data).decode(), "media_type": "image/" + kind,
                "width": w, "height": h, "pages": 1, "page": 0}

    try:
        import pymupdf
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF upload is not installed on this server. Export the plan as a PNG instead.") from None
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        n = doc.page_count
        if n < 1:
            raise ValueError
        page = min(max(0, int(page)), n - 1)
        pg = doc.load_page(page)
        w_pt, h_pt = pg.rect.width, pg.rect.height
        if not (0 < w_pt <= MAX_PDF_POINTS and 0 < h_pt <= MAX_PDF_POINTS):
            raise HTTPException(status_code=413, detail="That PDF page is too large.")
        zoom = PDF_LONG_SIDE_PX / max(w_pt, h_pt)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        png = pix.tobytes("png")
        out = {"png_base64": base64.b64encode(png).decode(), "media_type": "image/png",
               "width": pix.width, "height": pix.height, "pages": n, "page": page}
        doc.close()
        return out
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="That PDF could not be read. If it is protected or scanned oddly, export the plan as a PNG.") from None


# ----------------------------------------------------------------------
# plan + clicks -> a site
# ----------------------------------------------------------------------

def _plain(x):
    return json.loads(json.dumps(x, default=float))


def _build(file: UploadFile, settings_raw: str):
    data = _read_upload(file)
    kind = _kind(data)
    if kind not in ("png", "jpeg"):
        raise HTTPException(status_code=415, detail="Send the plan as a PNG or JPEG. For a PDF, use /api/plans/preview first.")
    img = _decode(data, kind)
    settings = _settings(settings_raw)
    try:
        built = site_plan.build_site(img, settings)
        twin = Twin(_plain(built.twin_dict))
    except site_plan.PlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise HTTPException(status_code=400, detail="Some of the details sent with the plan are missing or wrong. Go back a step and check the points you clicked.") from None
    finally:
        del img
    return built, twin


def _preview(built, twin) -> dict:
    """What the page shows on the confirm step: how much floor the cameras cover as drawn."""
    if twin.mode != "indoor" or not built.camera_specs:
        return {}
    cams = site_plan.cameras_from_records(built.camera_specs, twin.grid_m)
    rep = indoor.floor_report(twin, cams)
    return {"watched_frac": rep["watched_frac"], "open_floor_m2": rep["open_floor_m2"],
            "blind_m2": rep["blind_m2"], "blind_spots": rep["blind_spots"], "assets": rep["assets"],
            "entries": rep["entries"]}


@router.post("/sites/check")
def check_site(file: UploadFile = File(...), settings: str = Form(...)):
    """Read the plan and show what was found. Saves nothing."""
    built, twin = _build(file, settings)
    return _plain({"meta": built.meta, "warnings": built.warnings, "overlay": built.overlay,
                   "cameras": built.camera_specs, "preview": _preview(built, twin)})


@router.post("/sites", status_code=201)
def create_site(response: Response, file: UploadFile = File(...), settings: str = Form(...)):
    """Read the plan and keep the shapes (never the picture). Returns the id to use as ?site=."""
    built, twin = _build(file, settings)
    if not built.camera_specs:
        raise HTTPException(status_code=422, detail="Add at least one camera, or there is nothing to analyse.")
    try:
        site_id = site_store.save(built.twin_dict, built.camera_specs, built.meta)
    except site_store.StoreFull:
        raise HTTPException(status_code=507, detail="This server is full of saved sites. Delete one first.") from None
    response.headers["Cache-Control"] = "no-store"
    return _plain({"site_id": site_id, "meta": built.meta, "warnings": built.warnings})


@router.get("/sites/{site_id}")
def get_stored_site(site_id: str, response: Response):
    doc = site_store.read(site_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="That site was not found. It may have been deleted.")
    response.headers["Cache-Control"] = "no-store"
    return {"site_id": site_id, "meta": doc["meta"], "created": doc["created"],
            "cameras": [{"name": c["name"], "online": c.get("online", True), "calibrated": bool(c.get("calibration"))}
                        for c in doc["cameras"]]}


@router.delete("/sites/{site_id}", status_code=204)
def delete_stored_site(site_id: str):
    if not site_store.delete(site_id):
        raise HTTPException(status_code=404, detail="That site was not found. It may have been deleted.")
    return Response(status_code=204)


# ----------------------------------------------------------------------
# Calibrating a camera from its own picture  (engine/calibration.py does the maths)
# ----------------------------------------------------------------------

class CalibrationBody(BaseModel):
    camera: str = Field(..., max_length=16)
    image_points: list[list[float]] = Field(..., max_length=calibration.MAX_PAIRS)   # 0..1 across, 0..1 down
    ground_points: list[list[float]] = Field(..., max_length=calibration.MAX_PAIRS)  # metres on the plan
    source: Literal["ring", "upload"] = "upload"
    captured_at: str | None = Field(default=None, max_length=40)
    # Also work out where the camera is and which way it points, from the same spots (needs the picture's shape and
    # a lens angle: Ring does not say how wide the camera sees, so it is assumed and can be adjusted).
    place_camera: bool = False
    aspect: float = Field(default=16 / 9, ge=0.3, le=4.0)          # picture width / height
    lens_deg: float = Field(default=calibration.DEFAULT_LENS_DEG, ge=40, le=150)


def _demo_records() -> list:
    """The demo house's cameras as stored records (the same numbers make_demo_cameras() uses)."""
    from cameras import make_demo_cameras
    return [{"name": c.name, "position": [c.position[0], c.position[1]], "heading_deg": c.heading_deg,
             "fov_deg": c.fov_deg, "range_m": c.max_range_m, "online": True} for c in make_demo_cameras()]


def _records_for(site: str | None) -> list:
    if site is None:
        return _demo_records()
    doc = site_store.read(site)
    if doc is None:
        raise HTTPException(status_code=404, detail="That site was not found. It may have been deleted.")
    return doc["cameras"]


def _camera_index(records: list, name: str) -> int:
    for i, r in enumerate(records):
        if r["name"] == name:
            return i
    raise HTTPException(status_code=404, detail=f"There is no camera '{name}' on this site.")


def _work_out(body: CalibrationBody, record: dict, twin=None) -> dict:
    """The calibration for one camera. With place_camera, the camera is also (re)placed from the picture."""
    position = tuple(record["position"])
    pose = None
    try:
        if body.place_camera:
            pose = calibration.place_camera(body.image_points, body.ground_points, body.aspect, body.lens_deg)
            position = tuple(pose["position"])
        cal = calibration.calibrate(body.image_points, body.ground_points, position, float(record["range_m"]))
    except calibration.CalibrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    cal["position"] = list(position)
    cal["pose"] = pose
    if pose:
        cal["warnings"] = pose["warnings"] + cal["warnings"]
        if twin is not None and not twin.boundary.buffer(5.0).contains(Point(position)):
            cal["warnings"].append("The camera comes out well outside the property. Check the spots, or adjust the lens width.")
    return cal


def _twin_for(site: str | None):
    if site is None:
        return Twin.from_file(SCENARIO_PATH)
    found = site_store.load_site(site)
    return found[0] if found else None


@router.post("/calibration/preview")
def preview_calibration(body: CalibrationBody, site: str | None = Query(default=None)):
    """What the camera would see with these spots. Saves nothing, so the page can call it after every click."""
    records = _records_for(site)
    record = records[_camera_index(records, body.camera)]
    return {"camera": body.camera, **_work_out(body, record, _twin_for(site))}


@router.post("/calibration")
def save_calibration(body: CalibrationBody, response: Response, site: str | None = Query(default=None)):
    """
    Keep the calibration with the camera. From now on the measured ground replaces the guessed wedge in every
    result for this site. For the demo house a copy of the demo is saved (the demo itself is shared and never
    changes), and the copy's id comes back so the page can switch to it.
    """
    records = [dict(r) for r in _records_for(site)]
    i = _camera_index(records, body.camera)
    cal = _work_out(body, records[i], _twin_for(site))
    if not cal["checked"]:
        # 4 spots always fit perfectly, so they prove nothing. The 5th is what catches a mismatched spot.
        raise HTTPException(status_code=422, detail="Add a 5th spot so the view can be checked before it is saved.")
    was = records[i].get("calibration", {}).get("was") or {"heading_deg": records[i]["heading_deg"],
                                                           "fov_deg": records[i]["fov_deg"],
                                                           "position": list(records[i]["position"])}
    records[i] = {**records[i], "position": cal["position"], "heading_deg": cal["heading_deg"], "fov_deg": cal["fov_deg"],
                  "calibration": {"ground": cal["ground"], "image_points": cal["image_points"],
                                  "ground_points": cal["ground_points"], "checked": cal["checked"],
                                  "max_error_m": cal["max_error_m"], "area_m2": cal["area_m2"],
                                  "source": body.source, "captured_at": body.captured_at, "pose": cal["pose"],
                                  "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "was": was}}
    response.headers["Cache-Control"] = "no-store"
    if site is None:
        with open(SCENARIO_PATH) as f:
            twin_dict = {**json.load(f), "name": "Demo house (calibrated)", "mode": "outdoor"}
        twin = Twin(twin_dict)
        minx, miny, maxx, maxy = twin.boundary.bounds
        meta = {"name": "Demo house (calibrated)", "mode": "outdoor", "width_m": round(maxx - minx, 1),
                "height_m": round(maxy - miny, 1), "area_m2": round(twin.boundary.area, 1),
                "grid_m": twin.grid_m, "n_cameras": len(records)}
        try:
            site = site_store.save(twin_dict, records, meta)
        except site_store.StoreFull:
            raise HTTPException(status_code=507, detail="This server is full of saved sites. Delete one first.") from None
        return _plain({"site_id": site, "created": True, "meta": meta, "camera": body.camera, **cal})
    site_store.update_cameras(site, records)
    return _plain({"site_id": site, "created": False, "camera": body.camera, **cal})


@router.delete("/calibration", status_code=204)
def delete_calibration(site: str = Query(...), camera: str = Query(..., max_length=16)):
    """Forget a camera's calibration and go back to its guessed view (the heading it had before)."""
    records = [dict(r) for r in _records_for(site)]
    i = _camera_index(records, camera)
    cal = records[i].pop("calibration", None)
    if not cal:
        raise HTTPException(status_code=404, detail=f"Camera {camera} is not calibrated.")
    records[i].update(cal.get("was") or {})
    site_store.update_cameras(site, records)
    return Response(status_code=204)
