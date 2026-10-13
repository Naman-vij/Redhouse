"""
test_ring_reader.py - tests for app/ring/reader.py.

The sample responses below were recorded from the real Ring Developer
Playground on 2026-10-05. Only the long device ids were shortened; the
structure is exactly what Ring returned.
"""
import json
import httpx
import pytest
from app.ring.reader import (
    RingReader, RingReadError, RingZone, parse_zone, parse_devices,
    parse_status, parse_configurations,
)

DEV = "ava1.ring.device.AAAA"

DEVICES = {"data": [{
    "type": "devices", "id": DEV,
    "attributes": {"name": "Playground Device",
                   "image_url": "https://app-content.ring.com/x/DoorbellPro/rvdp_3x.png"},
    "relationships": {"status": {"data": {"type": "device-status", "id": "s1"},
                                 "links": {"related": f"/v1/devices/{DEV}/status"}}},
}], "meta": {"time": "2026-10-05T02:25:47Z"}}

STATUS_OFFLINE = {"data": {"type": "device-status", "id": "s1", "attributes": {
    "state": None, "audio": {"snooze": {"active": None, "until": None}},
    "reported_at": "2026-10-05T02:29:02Z", "online": False}}}

# The sandbox's real zone: one polygon covering the whole image.
FULL_FRAME = {"id": "04088135", "vertices": [
    {"x": 0, "y": 0}, {"x": 0.5, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0.6},
    {"x": 1, "y": 1}, {"x": 0.5, "y": 1}, {"x": 0, "y": 1}, {"x": 0, "y": 0.6}]}

CONFIGS = {"data": {"type": "device-configurations", "id": "c1", "attributes": {
    "e2e_encryption": {"enabled": False},
    "motion_detection": {"enabled": "on", "motion_zones": [FULL_FRAME]},
    "image_enhancements": {"color_night_vision": "off", "hdr": "off",
                           "ir_led_night_vision": "off", "auto_zoom_track": "off",
                           "privacy_zones": []},
    "audio": {"customizable_slots": None, "volume": 11}}}}


def reader_for(handler):
    sent = []

    def recording(request):
        sent.append(request)
        return handler(request)

    return RingReader(httpx.Client(transport=httpx.MockTransport(recording))), sent


def ring_ok(request):
    path = request.url.path
    if path == "/v1/devices":
        return httpx.Response(200, json=DEVICES)
    if path.endswith("/status"):
        return httpx.Response(200, json=STATUS_OFFLINE)
    if path.endswith("/configurations"):
        return httpx.Response(200, json=CONFIGS)
    return httpx.Response(404, json={})


# ---------------- parsing the real responses ----------------

def test_parse_real_device_list():
    assert parse_devices(DEVICES) == [(DEV, "Playground Device")]


def test_parse_real_offline_status():
    assert parse_status(STATUS_OFFLINE) == (False, "2026-10-05T02:29:02Z")


def test_missing_or_null_online_is_treated_as_offline():
    body = {"data": {"attributes": {"online": None}}}
    assert parse_status(body)[0] is False
    assert parse_status({"data": {"attributes": {}}})[0] is False


def test_only_explicit_true_is_online():
    assert parse_status({"data": {"attributes": {"online": True}}})[0] is True
    assert parse_status({"data": {"attributes": {"online": "true"}}})[0] is False


def test_parse_real_configurations():
    enabled, motion, privacy = parse_configurations(CONFIGS)
    assert enabled is True
    assert len(motion) == 1 and len(motion[0].vertices) == 8
    assert motion[0].vertices[0] == (0.0, 0.0)
    assert motion[0].vertices[3] == (1.0, 0.6)
    assert privacy == ()


def test_motion_enabled_is_text_on_off():
    off = {"data": {"attributes": {"motion_detection": {"enabled": "off"}}}}
    assert parse_configurations(off)[0] is False
    on_upper = {"data": {"attributes": {"motion_detection": {"enabled": "ON"}}}}
    assert parse_configurations(on_upper)[0] is True


def test_privacy_zones_are_read_from_image_enhancements():
    zone = {"id": "p1", "vertices": [{"x": 0.1, "y": 0.1}, {"x": 0.4, "y": 0.1}, {"x": 0.4, "y": 0.5}]}
    body = {"data": {"attributes": {"image_enhancements": {"privacy_zones": [zone]}}}}
    assert parse_configurations(body)[2][0].zone_id == "p1"


def test_privacy_zones_under_motion_detection_are_NOT_read():
    # They are not there in Ring's real response; guard against a wrong guess.
    zone = {"id": "p1", "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]}
    body = {"data": {"attributes": {"motion_detection": {"privacy_zones": [zone]}}}}
    assert parse_configurations(body)[2] == ()


def test_empty_configuration_is_fine():
    assert parse_configurations({"data": {"attributes": {}}}) == (False, (), ())


# ---------------- strict zone checking ----------------

@pytest.mark.parametrize("bad", [
    None, "zone", [],
    {"id": "z"},                                                    # no vertices
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},             # only 2 points
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1.5, "y": 1}]},   # out of range
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": -0.1, "y": 1}]},  # negative
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": "a", "y": 1}]},   # not a number
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": True, "y": 1}]},  # bool
    {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, "point"]},
])
def test_bad_zones_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_zone(bad)


def test_zone_boundaries_zero_and_one_are_valid():
    z = parse_zone({"id": "z", "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]})
    assert isinstance(z, RingZone) and z.vertices[2] == (1.0, 1.0)


# ---------------- talking to (a fake) Ring ----------------

def test_list_cameras_end_to_end():
    reader, sent = reader_for(ring_ok)
    cams = reader.list_cameras("good-token")
    assert len(cams) == 1
    cam = cams[0]
    assert cam.device_id == DEV and cam.name == "Playground Device"
    assert cam.online is False and cam.motion_enabled is True
    assert len(cam.motion_zones) == 1 and cam.privacy_zones == ()
    assert [r.url.path for r in sent] == ["/v1/devices", f"/v1/devices/{DEV}/status",
                                          f"/v1/devices/{DEV}/configurations"]


def test_requests_carry_bearer_token_and_content_type():
    reader, sent = reader_for(ring_ok)
    reader.list_cameras("tok-123")
    for req in sent:
        assert req.method == "GET"
        assert req.headers["authorization"] == "Bearer tok-123"
        assert req.headers["content-type"] == "application/json"


def test_no_devices_gives_empty_list():
    reader, _ = reader_for(lambda r: httpx.Response(200, json={"data": []}))
    assert reader.list_cameras("t") == []


def test_expired_token_gives_a_clear_message():
    reader, _ = reader_for(lambda r: httpx.Response(401, json={}))
    with pytest.raises(RingReadError) as e:
        reader.list_cameras("old")
    assert e.value.status_code == 401 and "Playground" in e.value.title


@pytest.mark.parametrize("token", ["", "   ", None, "two words", "a\nb"])
def test_bad_token_is_rejected_before_any_request(token):
    reader, sent = reader_for(ring_ok)
    with pytest.raises(RingReadError):
        reader.list_cameras(token)
    assert sent == []


def test_network_failure_is_reported_not_crashed():
    def boom(request):
        raise httpx.ConnectError("no route")
    reader, _ = reader_for(boom)
    with pytest.raises(RingReadError) as e:
        reader.list_cameras("t")
    assert e.value.status_code == 502


def test_non_json_response_is_an_error():
    reader, _ = reader_for(lambda r: httpx.Response(200, text="<html>hi</html>"))
    with pytest.raises(RingReadError):
        reader.list_cameras("t")


def test_server_error_is_reported():
    reader, _ = reader_for(lambda r: httpx.Response(500, json={"errors": [{"title": "Boom"}]}))
    with pytest.raises(RingReadError) as e:
        reader.list_cameras("t")
    assert e.value.status_code == 500 and e.value.title == "Boom"


def test_a_weird_zone_from_ring_becomes_a_clear_error():
    bad = json.loads(json.dumps(CONFIGS))
    bad["data"]["attributes"]["motion_detection"]["motion_zones"][0]["vertices"][0]["x"] = 7

    def handler(request):
        return httpx.Response(200, json=bad) if request.url.path.endswith("/configurations") else ring_ok(request)

    reader, _ = reader_for(handler)
    with pytest.raises(RingReadError) as e:
        reader.list_cameras("t")
    assert "Playground Device" in e.value.title


def test_device_id_cannot_change_the_url_path():
    evil = {"data": [{"id": "../../admin", "attributes": {"name": "x"}}]}
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        if request.url.path == "/v1/devices":
            return httpx.Response(200, json=evil)
        return httpx.Response(200, json={"data": {"attributes": {}}})

    reader, _ = reader_for(handler)
    reader.list_cameras("t")
    assert all(p.startswith("/v1/devices/..%2F..%2Fadmin/") for p in seen[1:])


def test_token_never_appears_in_errors():
    reader, _ = reader_for(lambda r: httpx.Response(500, json={}))
    with pytest.raises(RingReadError) as e:
        reader.list_cameras("SECRET-TOKEN-VALUE")
    assert "SECRET-TOKEN-VALUE" not in str(e.value)