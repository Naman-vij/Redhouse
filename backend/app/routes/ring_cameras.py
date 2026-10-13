"""
ring_cameras.py (route)

GET /api/ring/cameras

The browser sends the Ring token in the standard header
    Authorization: Bearer <token>
and gets back every camera the token can see, with online status and
zones.

The /docs test page cannot send an "Authorization" header (Swagger UI
ignores parameters with that name), so the token may ALSO be sent as
    X-Ring-Token: <token>

SECURITY NOTES
  - The token is never stored, never logged and never sent back.
  - It travels in a header, not the URL, because URLs end up in logs.
  - For the hackathon a pasted 30-minute Playground token is fine. A
    real product would use the OAuth account-linking flow instead
    (ring_http.py / linking.py), which stores tokens safely.
"""

from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.ring.reader import RingCamera, RingReader, RingReadError, RingZone

router = APIRouter(prefix="/api/ring", tags=["ring"])


@lru_cache(maxsize=1)
def get_reader() -> RingReader:
    """
    One shared reader (so web connections are reused).

    NOTE: written as a function so tests can swap in a fake Ring with
    app.dependency_overrides, with no network involved.
    """
    return RingReader()


HINT = ("send your Ring token as:  Authorization: Bearer <token>   "
        "(or, on the /docs page, as:  X-Ring-Token: <token>)")


def _token_from_headers(authorization: str | None, x_ring_token: str | None) -> str:
    """
    Work out which token the caller sent.

    NOTE: if an Authorization header is present it must be valid - we
    do not quietly fall back to the other header, because a malformed
    header is a mistake worth telling the caller about.
    """
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail=HINT)
        return token.strip()
    if x_ring_token and x_ring_token.strip():
        return x_ring_token.strip()
    raise HTTPException(status_code=401, detail=HINT)


def _zone_json(zone: RingZone) -> dict:
    # NOTE: JSON has no tuples, so each (x, y) point becomes [x, y].
    return {"id": zone.zone_id, "vertices": [[x, y] for x, y in zone.vertices]}


def _camera_json(camera: RingCamera) -> dict:
    return {
        "device_id": camera.device_id,
        "name": camera.name,
        "online": camera.online,
        "reported_at": camera.reported_at,
        "motion_enabled": camera.motion_enabled,
        "motion_zones": [_zone_json(z) for z in camera.motion_zones],
        "privacy_zones": [_zone_json(z) for z in camera.privacy_zones],
    }


@router.get("/cameras")
def list_cameras(response: Response,
                 authorization: str | None = Header(default=None),
                 x_ring_token: str | None = Header(default=None),
                 reader: RingReader = Depends(get_reader)):
    token = _token_from_headers(authorization, x_ring_token)
    try:
        cameras = reader.list_cameras(token)
    except RingReadError as exc:
        # 401 = the user's token is the problem (they can fix it).
        # Anything else = Ring or the network is the problem (502).
        status = 401 if exc.status_code == 401 else 502
        raise HTTPException(status_code=status, detail=exc.title) from None

    # NOTE: camera data is private, so tell browsers and proxies not to
    # keep a copy.
    response.headers["Cache-Control"] = "no-store"
    return {"cameras": [_camera_json(c) for c in cameras]}
