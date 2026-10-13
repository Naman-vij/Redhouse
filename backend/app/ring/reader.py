"""
reader.py

Reads cameras from the Ring API using a bearer token (for example the
30-minute token from Ring's Developer Playground).

Every response shape handled here was recorded from the real Playground
(2026-10-05), not guessed from the docs:

  GET /v1/devices                      -> data[]: id, attributes.name
  GET /v1/devices/{id}/status          -> data.attributes.online (true/false)
  GET /v1/devices/{id}/configurations  -> data.attributes.motion_detection
                                            .enabled   ("on" / "off")
                                            .motion_zones[]  {id, vertices[{x,y}]}
                                          data.attributes.image_enhancements
                                            .privacy_zones[]  (same zone shape)

NOTE: this is separate from ring_http.py on purpose. ring_http.py is the
production path (OAuth + account linking). This file is the quick path
(paste a token and read). Different way in, different file.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

API_BASE_URL = "https://api.amazonvision.com"
DEFAULT_TIMEOUT_S = 10.0


class RingReadError(Exception):
    """
    Something went wrong reading from Ring.

    NOTE: holds only a status code and a short, cleaned message - never
    the token - so it is safe to show to a user or write to a log.
    """

    def __init__(self, status_code: int, title: str):
        self.status_code = status_code
        self.title = title
        super().__init__(f"Ring read error {status_code}: {title}")


# ------------------------------------------------------------------
# What we hand back
# ------------------------------------------------------------------

@dataclass(frozen=True)
class RingZone:
    zone_id: str
    # NOTE: points of a polygon in NORMALISED image coordinates:
    # (0, 0) is the top-left of the camera image, (1, 1) the bottom-right.
    vertices: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class RingCamera:
    device_id: str
    name: str
    online: bool
    reported_at: str | None
    motion_enabled: bool
    motion_zones: tuple[RingZone, ...]
    privacy_zones: tuple[RingZone, ...]


# ------------------------------------------------------------------
# Parsing (pure functions - no network, easy to test)
# ------------------------------------------------------------------

def _attributes(body: dict) -> dict:
    data = body.get("data") if isinstance(body, dict) else None
    attributes = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attributes, dict):
        raise ValueError("response has no data.attributes")
    return attributes


def parse_zone(raw) -> RingZone:
    """
    One zone: {"id": "...", "vertices": [{"x": 0.5, "y": 0}, ...]}.

    NOTE: we are strict on purpose. A zone we misread could make a
    camera look like it covers ground it doesn't (or the reverse), and
    a security audit must not quietly guess.
    """
    if not isinstance(raw, dict):
        raise ValueError("zone must be an object")
    vertices = raw.get("vertices")
    if not isinstance(vertices, list) or len(vertices) < 3:
        raise ValueError("zone needs at least 3 vertices")

    points = []
    for v in vertices:
        if not isinstance(v, dict):
            raise ValueError("vertex must be an object")
        x, y = v.get("x"), v.get("y")
        for value in (x, y):
            # NOTE: bool is a number in Python (True == 1); reject it.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("vertex x and y must be numbers")
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("vertex outside the 0..1 range")
        points.append((float(x), float(y)))
    return RingZone(zone_id=str(raw.get("id") or ""), vertices=tuple(points))


def parse_devices(body: dict) -> list[tuple[str, str]]:
    """The device list -> [(device_id, name), ...]."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ValueError("device list has no 'data' array")
    devices = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            attributes = item.get("attributes")
            name = attributes.get("name") if isinstance(attributes, dict) else None
            devices.append((item["id"], str(name or "Unnamed device")))
    return devices


def parse_status(body: dict) -> tuple[bool, str | None]:
    """
    -> (online, reported_at).

    NOTE: only an explicit `true` counts as online. A missing or null
    value is treated as OFFLINE, because for a security audit it is
    safer to assume a camera is NOT watching than to assume it is.
    """
    attributes = _attributes(body)
    reported = attributes.get("reported_at")
    return attributes.get("online") is True, reported if isinstance(reported, str) else None


def parse_configurations(body: dict) -> tuple[bool, tuple[RingZone, ...], tuple[RingZone, ...]]:
    """
    -> (motion_enabled, motion_zones, privacy_zones).

    NOTE two details the docs did not make obvious:
      - motion "enabled" is the TEXT "on" / "off", not true / false.
      - privacy zones live under image_enhancements, NOT under
        motion_detection.
    """
    attributes = _attributes(body)

    motion = attributes.get("motion_detection")
    motion = motion if isinstance(motion, dict) else {}
    enabled = motion.get("enabled")
    motion_enabled = enabled is True or (isinstance(enabled, str) and enabled.lower() == "on")
    motion_zones = tuple(parse_zone(z) for z in (motion.get("motion_zones") or []))

    enhancements = attributes.get("image_enhancements")
    enhancements = enhancements if isinstance(enhancements, dict) else {}
    privacy_zones = tuple(parse_zone(z) for z in (enhancements.get("privacy_zones") or []))

    return motion_enabled, motion_zones, privacy_zones


# ------------------------------------------------------------------
# Talking to Ring
# ------------------------------------------------------------------

def _error_title(response: httpx.Response) -> str:
    title = ""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            title = str(errors[0].get("title") or errors[0].get("code") or "")
    title = re.sub(r"[^\x20-\x7E]", "", title)[:80].strip()
    return title or "no error detail"


class RingReader:
    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=DEFAULT_TIMEOUT_S)

    def _get(self, path: str, token: str) -> dict:
        # NOTE: Ring's own examples send Content-Type: application/json
        # even on GET requests, so we always include it.
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            response = self._http.get(API_BASE_URL + path, headers=headers)
        except httpx.HTTPError:
            raise RingReadError(502, "could not reach the Ring API") from None

        if response.status_code == 401:
            raise RingReadError(401, "token expired or invalid - generate a new one in the Ring Playground")
        if not response.is_success:
            raise RingReadError(response.status_code, _error_title(response))
        try:
            body = response.json()
        except ValueError:
            raise RingReadError(response.status_code, "response was not JSON") from None
        if not isinstance(body, dict):
            raise RingReadError(response.status_code, "unexpected response shape")
        return body

    def list_cameras(self, token: str) -> list[RingCamera]:
        """Every camera the token can see, with status and zones."""
        token = (token or "").strip()
        if not token or re.search(r"\s", token):
            raise RingReadError(401, "no valid token provided")

        try:
            devices = parse_devices(self._get("/v1/devices", token))
        except ValueError as exc:
            raise RingReadError(200, f"unexpected device list: {exc}") from None

        cameras = []
        for device_id, name in devices:
            # NOTE: quote() makes sure an odd id can never change the
            # URL path we call.
            safe_id = quote(device_id, safe="")
            status_body = self._get(f"/v1/devices/{safe_id}/status", token)
            config_body = self._get(f"/v1/devices/{safe_id}/configurations", token)
            try:
                online, reported_at = parse_status(status_body)
                motion_enabled, motion_zones, privacy_zones = parse_configurations(config_body)
            except ValueError as exc:
                raise RingReadError(200, f"unexpected data for '{name}': {exc}") from None
            cameras.append(RingCamera(device_id, name, online, reported_at,
                                      motion_enabled, motion_zones, privacy_zones))
        return cameras