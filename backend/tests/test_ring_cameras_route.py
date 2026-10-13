"""
test_ring_cameras_route.py - tests GET /api/ring/cameras end to end,
with a FAKE Ring (no network, no real token).
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ring.reader import RingReader
from app.routes.ring_cameras import get_reader

# NOTE: responses recorded from the real Ring Playground (2026-10-05),
# with the long device id shortened. Kept in THIS file so the test
# doesn't depend on another test file being importable.
DEV = "ava1.ring.device.AAAA"
DEVICES = {"data": [{"type": "devices", "id": DEV,
                     "attributes": {"name": "Playground Device"}}]}
STATUS_OFFLINE = {"data": {"type": "device-status", "attributes": {
    "reported_at": "2026-10-05T02:29:02Z", "online": False}}}
FULL_FRAME = {"id": "04088135", "vertices": [
    {"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.6},
    {"x": 1, "y": 1}, {"x": 0.5, "y": 1}, {"x": 0, "y": 1}, {"x": 0, "y": 0.6}]}
CONFIGS = {"data": {"type": "device-configurations", "attributes": {
    "motion_detection": {"enabled": "on", "motion_zones": [FULL_FRAME]},
    "image_enhancements": {"privacy_zones": []}}}}


def ring_ok(request):
    path = request.url.path
    if path == "/v1/devices":
        return httpx.Response(200, json=DEVICES)
    if path.endswith("/status"):
        return httpx.Response(200, json=STATUS_OFFLINE)
    if path.endswith("/configurations"):
        return httpx.Response(200, json=CONFIGS)
    return httpx.Response(404, json={})


client = TestClient(app)
GOOD = {"Authorization": "Bearer good-token-123"}


def use_fake_ring(handler):
    reader = RingReader(httpx.Client(transport=httpx.MockTransport(handler)))
    app.dependency_overrides[get_reader] = lambda: reader


@pytest.fixture(autouse=True)
def clean_overrides():
    # NOTE: runs after EVERY test so a fake Ring never leaks into the next.
    yield
    app.dependency_overrides.clear()


def test_returns_the_cameras():
    use_fake_ring(ring_ok)
    r = client.get("/api/ring/cameras", headers=GOOD)
    assert r.status_code == 200
    cams = r.json()["cameras"]
    assert len(cams) == 1
    cam = cams[0]
    assert cam["name"] == "Playground Device"
    assert cam["online"] is False
    assert cam["reported_at"] == "2026-10-05T02:29:02Z"
    assert cam["motion_enabled"] is True
    assert cam["privacy_zones"] == []
    zone = cam["motion_zones"][0]
    assert len(zone["vertices"]) == 8
    assert zone["vertices"][0] == [0.0, 0.0] and zone["vertices"][3] == [1.0, 0.6]


def test_response_is_marked_do_not_cache():
    use_fake_ring(ring_ok)
    assert client.get("/api/ring/cameras", headers=GOOD).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers", [
    {},                                     # no header at all
    {"Authorization": "Bearer"},            # no token
    {"Authorization": "Bearer   "},         # blank token
    {"Authorization": "Token abc"},         # wrong scheme
    {"Authorization": "abc"},               # no scheme
])
def test_missing_or_malformed_token_is_401(headers):
    use_fake_ring(ring_ok)
    r = client.get("/api/ring/cameras", headers=headers)
    assert r.status_code == 401
    assert "Authorization: Bearer" in r.json()["detail"]


def test_expired_token_is_401_with_advice():
    use_fake_ring(lambda request: httpx.Response(401, json={}))
    r = client.get("/api/ring/cameras", headers={"Authorization": "Bearer old"})
    assert r.status_code == 401
    assert "Playground" in r.json()["detail"]


def test_ring_unreachable_is_502():
    def boom(request):
        raise httpx.ConnectError("no route")
    use_fake_ring(boom)
    assert client.get("/api/ring/cameras", headers=GOOD).status_code == 502


def test_ring_server_error_is_502():
    use_fake_ring(lambda request: httpx.Response(500, json={}))
    assert client.get("/api/ring/cameras", headers=GOOD).status_code == 502


def test_unexpected_data_from_ring_is_502_not_a_crash():
    use_fake_ring(lambda request: httpx.Response(200, json={"nope": 1}))
    assert client.get("/api/ring/cameras", headers=GOOD).status_code == 502


def test_token_is_never_echoed_back():
    secret = "SECRET-TOKEN-VALUE"
    use_fake_ring(ring_ok)
    ok = client.get("/api/ring/cameras", headers={"Authorization": f"Bearer {secret}"})
    assert secret not in ok.text
    use_fake_ring(lambda request: httpx.Response(500, json={}))
    bad = client.get("/api/ring/cameras", headers={"Authorization": f"Bearer {secret}"})
    assert secret not in bad.text


def test_token_is_forwarded_to_ring_as_bearer():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return ring_ok(request)

    use_fake_ring(handler)
    client.get("/api/ring/cameras", headers={"Authorization": "Bearer abc.def-123"})
    assert seen and all(h == "Bearer abc.def-123" for h in seen)


# ---------------- the /docs page path (X-Ring-Token) ----------------

def test_x_ring_token_header_also_works():
    use_fake_ring(ring_ok)
    r = client.get("/api/ring/cameras", headers={"X-Ring-Token": "good-token-123"})
    assert r.status_code == 200 and r.json()["cameras"][0]["name"] == "Playground Device"


def test_x_ring_token_is_forwarded_to_ring_as_bearer():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return ring_ok(request)

    use_fake_ring(handler)
    client.get("/api/ring/cameras", headers={"X-Ring-Token": "abc.def-123"})
    assert seen and all(h == "Bearer abc.def-123" for h in seen)


def test_blank_x_ring_token_is_401():
    use_fake_ring(ring_ok)
    assert client.get("/api/ring/cameras", headers={"X-Ring-Token": "   "}).status_code == 401


def test_malformed_authorization_is_rejected_even_with_a_valid_x_ring_token():
    use_fake_ring(ring_ok)
    r = client.get("/api/ring/cameras",
                   headers={"Authorization": "Bearer, abc", "X-Ring-Token": "good"})
    assert r.status_code == 401


def test_the_comma_typo_from_the_docs_page_is_caught():
    use_fake_ring(ring_ok)
    r = client.get("/api/ring/cameras", headers={"Authorization": "Bearer, eyJhbGci"})
    assert r.status_code == 401