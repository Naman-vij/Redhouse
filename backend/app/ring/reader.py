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

import ipaddress
import re
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

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


@dataclass(frozen=True)
class RingSnapshot:
    """One picture from a camera. captured_at is Ring's own time stamp for it, when Ring sends one."""
    data: bytes
    media_type: str          # "image/jpeg" or "image/png"
    captured_at: str | None


# Snapshots
# NOTE: Ring's image download only hands back a picture the camera ALREADY saved (during a motion event or a
# live view). It does not take a new one. We ask for the newest picture since a start time; a start time from
# before the user linked the app is refused (403 TIME_RANGE_NOT_AUTHORIZED), so we try a long window first
# and fall back to shorter ones.
SNAPSHOT_WINDOWS_S = (24 * 3600, 2 * 3600, 10 * 60)
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
SNAPSHOT_WIDTH, SNAPSHOT_HEIGHT = 1280, 720


def _picture_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _safe_download_url(url) -> str | None:
    """
    The picture comes from a short-lived signed link Ring points us to. We only follow an https link to a
    named host: never plain http, never a bare IP address or 'localhost', so a bad answer cannot make the
    server fetch something on its own network.
    """
    if not isinstance(url, str) or len(url) > 4096:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host == "localhost" or host.endswith(".local") or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None                                   # a bare IP address
    except ValueError:
        return url


def _url_in_json(body) -> str | None:
    """Some APIs answer with JSON holding the link instead of a redirect. Accept {url}, {data:{url}}, {data:{attributes:{url}}}."""
    for node in (body, (body or {}).get("data") if isinstance(body, dict) else None):
        if not isinstance(node, dict):
            continue
        for holder in (node, node.get("attributes")):
            if isinstance(holder, dict):
                for key in ("url", "download_url", "signed_url"):
                    if isinstance(holder.get(key), str):
                        return holder[key]
    return None


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


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    errors = body.get("errors") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return re.sub(r"[^A-Z0-9_]", "", str(errors[0].get("code") or "").upper())[:60]
    return ""


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

    # ------------------------------------------------------------------
    # The camera's latest picture
    # ------------------------------------------------------------------

    def fetch_snapshot(self, token: str, device_id: str, now_ms: int | None = None) -> RingSnapshot:
        """The newest picture the camera has saved. Raises RingReadError with a sentence a person can act on."""
        token = (token or "").strip()
        if not token or re.search(r"\s", token):
            raise RingReadError(401, "no valid token provided")
        if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 200:
            raise RingReadError(400, "no camera chosen")
        path = f"/v1/devices/{quote(device_id.strip(), safe='')}/media/image/download"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms

        last = None
        for window in SNAPSHOT_WINDOWS_S:
            body = {"type": "latest_in_range", "start_timestamp": now_ms - window * 1000,
                    "image_options": {"format": "jpeg",
                                      "resolution": {"width": SNAPSHOT_WIDTH, "height": SNAPSHOT_HEIGHT}}}
            try:
                response = self._http.post(API_BASE_URL + path, headers=headers, json=body, follow_redirects=False)
            except httpx.HTTPError:
                raise RingReadError(502, "could not reach the Ring API") from None

            if response.status_code == 401:
                raise RingReadError(401, "token expired or invalid - generate a new one in the Ring Playground")
            if response.status_code == 403 and _error_code(response) == "TIME_RANGE_NOT_AUTHORIZED":
                last = response
                continue                              # the window starts before the app was linked: try a shorter one
            if response.status_code == 403:
                raise RingReadError(403, "this token is not allowed to read pictures from this camera")
            if response.status_code == 404:
                raise RingReadError(404, "this camera has no saved picture yet - open its live view in the Ring app, "
                                         "or wait for a motion event, then try again")
            if response.status_code == 503:
                raise RingReadError(503, "the camera is offline, so Ring has no picture to give")
            if response.status_code == 429:
                raise RingReadError(429, "Ring is busy - wait a few seconds and try again")
            return self._snapshot_from(response)

        raise RingReadError(403, "Ring only gives pictures taken after this app was linked, and there are none yet - "
                                 "open the camera's live view in the Ring app, then try again")

    def _snapshot_from(self, response: httpx.Response) -> RingSnapshot:
        stamp = response.headers.get("x-media-timestamp")
        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            url = response.headers.get("location")
        elif response.is_success and _picture_type(response.content or b""):
            return self._checked(response.content, stamp)
        elif response.is_success:
            try:
                url = _url_in_json(response.json())
            except ValueError:
                url = None
        else:
            raise RingReadError(response.status_code, _error_title(response))

        url = _safe_download_url(url)
        if url is None:
            raise RingReadError(502, "Ring did not say where the picture is")
        try:
            # NOTE: the signed link carries its own permission. Our Ring token is NOT sent with it.
            with self._http.stream("GET", url, follow_redirects=False) as pic:
                if not pic.is_success:
                    raise RingReadError(502, "the picture link from Ring did not work - try again")
                data = b""
                for chunk in pic.iter_bytes():
                    data += chunk
                    if len(data) > MAX_SNAPSHOT_BYTES:
                        raise RingReadError(502, "the picture from Ring is too large")
                stamp = stamp or pic.headers.get("x-media-timestamp")
        except httpx.HTTPError:
            raise RingReadError(502, "could not download the picture from Ring") from None
        return self._checked(data, stamp)

    @staticmethod
    def _checked(data: bytes, stamp) -> RingSnapshot:
        kind = _picture_type(data)
        if kind is None or len(data) > MAX_SNAPSHOT_BYTES:
            raise RingReadError(502, "Ring sent something that is not a picture")
        stamp = re.sub(r"[^0-9A-Za-z:.+\-TZ]", "", str(stamp))[:40] if stamp else None
        return RingSnapshot(data, kind, stamp or None)
