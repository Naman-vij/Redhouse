"""
sites.py (route)

Upload a floor plan or a site plan, say where the cameras are, and get a
site the rest of the API can analyse.

    POST   /api/plans/preview     a PDF or image  ->  a PNG the page can show and click on
    POST   /api/sites/check       the plan + your clicks  ->  what we found (nothing is saved)
    POST   /api/sites             the same, and keeps the result  ->  {site_id}
    GET    /api/sites/{id}        what is stored (names and sizes only)
    DELETE /api/sites/{id}        forget it

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
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app import site_store

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
            "cameras": [{"name": c["name"], "online": c.get("online", True)} for c in doc["cameras"]]}


@router.delete("/sites/{site_id}", status_code=204)
def delete_stored_site(site_id: str):
    if not site_store.delete(site_id):
        raise HTTPException(status_code=404, detail="That site was not found. It may have been deleted.")
    return Response(status_code=204)
