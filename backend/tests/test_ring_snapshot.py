"""
test_ring_snapshot.py - reading a camera's latest picture from Ring, with a FAKE Ring (no network).

NOTE: the request shape (latest_in_range, start_timestamp in ms, image_options) follows Ring's Partner API docs
and the Amazon developer forum; it has not yet been recorded from the real Playground like the other responses.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ring.reader import RingReader, RingReadError, SNAPSHOT_WINDOWS_S
from app.routes.ring_cameras import get_reader

client = TestClient(app)
GOOD = {"Authorization": "Bearer good-token-123"}
DEV = "ava1.ring.device.AAAA"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200
SIGNED = "https://ring-media.s3.amazonaws.com/abc.jpg?X-Amz-Signature=xyz"
NOW = 1_760_000_000_000


def ring(handler):
    sent = []

    def recording(request):
        sent.append(request)
        return handler(request)

    return RingReader(httpx.Client(transport=httpx.MockTransport(recording))), sent


def redirect_then_picture(request):
    if request.url.host == "api.amazonvision.com":
        return httpx.Response(302, headers={"Location": SIGNED, "X-Media-Timestamp": "2026-10-14T02:58:11Z"})
    return httpx.Response(200, content=JPEG, headers={"Content-Type": "image/jpeg"})


def error(status, code=""):
    return lambda request: httpx.Response(status, json={"errors": [{"code": code, "title": code or "x"}]})


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    app.dependency_overrides.clear()


def test_follows_the_signed_link_without_our_token():
    reader, sent = ring(redirect_then_picture)
    shot = reader.fetch_snapshot("tok", DEV, now_ms=NOW)
    assert shot.data == JPEG and shot.media_type == "image/jpeg"
    assert shot.captured_at == "2026-10-14T02:58:11Z"
    ask, download = sent
    assert ask.method == "POST" and ask.url.path == f"/v1/devices/{DEV}/media/image/download"
    assert ask.headers["authorization"] == "Bearer tok"
    req = json.loads(ask.content)
    assert req["type"] == "latest_in_range" and req["start_timestamp"] == NOW - SNAPSHOT_WINDOWS_S[0] * 1000
    assert req["image_options"]["format"] == "jpeg"
    assert str(download.url) == SIGNED and "authorization" not in download.headers


def test_a_picture_sent_directly_also_works():
    reader, _ = ring(lambda r: httpx.Response(200, content=JPEG))
    assert reader.fetch_snapshot("tok", DEV, now_ms=NOW).data == JPEG


def test_a_link_in_json_also_works():
    def handler(r):
        if r.url.host == "api.amazonvision.com":
            return httpx.Response(200, json={"data": {"attributes": {"url": SIGNED}}})
        return httpx.Response(200, content=JPEG)
    reader, _ = ring(handler)
    assert reader.fetch_snapshot("tok", DEV, now_ms=NOW).data == JPEG


def test_falls_back_to_shorter_windows_before_the_link_date():
    calls = []

    def handler(r):
        if r.url.host != "api.amazonvision.com":
            return httpx.Response(200, content=JPEG)
        calls.append(json.loads(r.content)["start_timestamp"])
        if len(calls) < 3:
            return error(403, "TIME_RANGE_NOT_AUTHORIZED")(r)
        return httpx.Response(302, headers={"Location": SIGNED})
    reader, _ = ring(handler)
    reader.fetch_snapshot("tok", DEV, now_ms=NOW)
    assert calls == [NOW - w * 1000 for w in SNAPSHOT_WINDOWS_S]


@pytest.mark.parametrize("handler,status,words", [
    (error(401), 401, "token"),
    (error(403, "FORBIDDEN"), 403, "not allowed"),
    (error(403, "TIME_RANGE_NOT_AUTHORIZED"), 403, "after this app was linked"),
    (error(404), 404, "no saved picture"),
    (error(503), 503, "offline"),
    (lambda r: httpx.Response(302, headers={"Location": "http://ring.example.com/a.jpg"}), 502, "did not say"),
    (lambda r: httpx.Response(302, headers={"Location": "https://169.254.169.254/latest/meta-data"}), 502, "did not say"),
    (lambda r: httpx.Response(302, headers={"Location": "https://localhost/a.jpg"}), 502, "did not say"),
    (lambda r: httpx.Response(200, content=b"<html>not a picture</html>"), 502, None),
])
def test_problems_become_sentences(handler, status, words):
    reader, _ = ring(handler)
    with pytest.raises(RingReadError) as exc:
        reader.fetch_snapshot("tok", DEV, now_ms=NOW)
    assert exc.value.status_code == status
    if words:
        assert words in exc.value.title


def test_route_returns_the_picture_and_never_caches_it():
    reader, _ = ring(redirect_then_picture)
    app.dependency_overrides[get_reader] = lambda: reader
    r = client.get("/api/ring/snapshot", params={"device": DEV}, headers=GOOD)
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-captured-at"] == "2026-10-14T02:58:11Z"


def test_route_needs_a_token_and_a_device():
    assert client.get("/api/ring/snapshot", params={"device": DEV}).status_code == 401
    assert client.get("/api/ring/snapshot", headers=GOOD).status_code == 422


def test_route_passes_on_offline():
    reader, _ = ring(error(503))
    app.dependency_overrides[get_reader] = lambda: reader
    r = client.get("/api/ring/snapshot", params={"device": DEV}, headers=GOOD)
    assert r.status_code == 503 and "offline" in r.json()["detail"]
