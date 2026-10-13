"""
test_ring_status_route.py - tests GET /api/ring/status end to end, with a
FAKE Ring (no network, no real token).

Runs the real simulator on the demo house, so these also prove the Ring
flag really changes the numbers.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ring.reader import RingReader
from app.routes.ring_cameras import get_reader

client = TestClient(app)
GOOD = {"Authorization": "Bearer good-token-123"}

FRONT = ("ava1.ring.device.FRONT", "Front Door Cam")
GARAGE = ("ava1.ring.device.GARAGE", "Garage Cam")

CONFIG = {"data": {"type": "device-configurations", "id": "c", "attributes": {
    "motion_detection": {"enabled": "on", "motion_zones": []},
    "image_enhancements": {"privacy_zones": []}}}}


def fake_ring(devices):
    """devices: list of (device_id, name, online)."""
    def handler(request):
        path = request.url.path
        if path == "/v1/devices":
            return httpx.Response(200, json={"data": [
                {"type": "devices", "id": i, "attributes": {"name": n}}
                for i, n, _ in devices]})
        for i, _, online in devices:
            if path == f"/v1/devices/{i}/status":
                return httpx.Response(200, json={"data": {"attributes": {
                    "online": online, "reported_at": "2026-10-12T05:00:00Z"}}})
            if path == f"/v1/devices/{i}/configurations":
                return httpx.Response(200, json=CONFIG)
        return httpx.Response(404, json={})
    return handler


def use_ring(handler):
    reader = RingReader(httpx.Client(transport=httpx.MockTransport(handler)))
    app.dependency_overrides[get_reader] = lambda: reader


def status(*links, headers=GOOD, **params):
    q = [("link", link) for link in links] + list(params.items())
    return client.get("/api/ring/status", params=q, headers=headers)


def door(body, name):
    return next(d for d in body["doors"] if d["door"] == name)


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------- the main story

def test_online_ring_camera_changes_nothing():
    use_ring(fake_ring([(*FRONT, True)]))
    r = status("C1:Front Door Cam")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "All 3 cameras are online. No door is more exposed than normal."
    assert all(d["change"] == 0 for d in body["doors"])


def test_offline_ring_camera_raises_the_front_door_risk():
    use_ring(fake_ring([(*FRONT, False)]))
    body = status("C1:Front Door Cam").json()
    front = door(body, "front_door")
    assert front["before"] == 0.0 and front["after"] >= 0.10
    assert "Camera C1 is offline" in body["summary"]
    assert "front_door attacker success rises from 0%" in body["summary"]


def test_a_different_camera_goes_offline_and_a_different_door_suffers():
    use_ring(fake_ring([(*GARAGE, False)]))
    body = status("C2:Garage Cam").json()
    assert door(body, "side_door")["change"] >= 0.15
    assert door(body, "front_door")["change"] == 0


def test_flipping_the_ring_status_flips_the_answer():
    use_ring(fake_ring([(*FRONT, True)]))
    online = status("C1:Front Door Cam").json()
    use_ring(fake_ring([(*FRONT, False)]))
    offline = status("C1:Front Door Cam").json()
    assert door(online, "front_door")["after"] < door(offline, "front_door")["after"]


def test_state_from_one_request_never_leaks_into_the_next():
    use_ring(fake_ring([(*FRONT, False)]))
    status("C1:Front Door Cam")                       # C1 offline...
    again = status().json()                           # ...then no links at all
    assert again["summary"].startswith("All 3 cameras are online")
    assert all(c["online"] for c in again["cameras"])


# ---------------------------------------------------- honest labelling

def test_every_camera_is_labelled_live_or_simulated():
    use_ring(fake_ring([(*FRONT, True)]))
    body = status("C1:Front Door Cam").json()
    assert [c["source"] for c in body["cameras"]] == ["ring", "simulated", "simulated"]
    assert body["live_cameras"] == 1 and body["simulated_cameras"] == 2
    assert body["cameras"][0]["ring_device_name"] == "Front Door Cam"
    assert body["cameras"][0]["reported_at"] == "2026-10-12T05:00:00Z"


def test_no_links_means_everything_simulated_but_devices_are_listed():
    use_ring(fake_ring([(*FRONT, True), (*GARAGE, False)]))
    body = status().json()
    assert body["live_cameras"] == 0 and body["simulated_cameras"] == 3
    names = {d["name"]: d for d in body["ring_devices"]}
    assert set(names) == {"Front Door Cam", "Garage Cam"}
    assert names["Garage Cam"]["online"] is False
    assert all(d["linked_to"] is None for d in body["ring_devices"])


def test_ring_devices_show_which_camera_they_are_linked_to():
    use_ring(fake_ring([(*FRONT, True), (*GARAGE, False)]))
    body = status("C3:Garage Cam").json()
    linked = {d["name"]: d["linked_to"] for d in body["ring_devices"]}
    assert linked == {"Front Door Cam": None, "Garage Cam": "C3"}


def test_two_live_cameras_at_once():
    use_ring(fake_ring([(*FRONT, False), (*GARAGE, False)]))
    body = status("C1:Front Door Cam", "C2:Garage Cam").json()
    assert body["live_cameras"] == 2
    assert body["summary"].startswith("Cameras C1, C2 are offline")
    assert door(body, "side_door")["after"] > door(body, "side_door")["before"]


def test_link_by_device_id_and_by_name_agree():
    use_ring(fake_ring([(*FRONT, False)]))
    by_id = status(f"C1:{FRONT[0]}").json()
    by_name = status("C1:front door cam").json()
    assert by_id["doors"] == by_name["doors"]


# -------------------------------------------------------- bad links -> 400

def test_malformed_link_is_400():
    use_ring(fake_ring([(*FRONT, True)]))
    r = status("C1")
    assert r.status_code == 400 and "CameraName:RingDeviceNameOrId" in r.json()["detail"]


def test_unknown_camera_is_400():
    use_ring(fake_ring([(*FRONT, True)]))
    r = status("C9:Front Door Cam")
    assert r.status_code == 400 and "no camera 'C9'" in r.json()["detail"]


def test_unknown_ring_device_is_400_and_lists_the_real_ones():
    use_ring(fake_ring([(*FRONT, True)]))
    r = status("C1:Nope")
    assert r.status_code == 400 and "'Front Door Cam'" in r.json()["detail"]


def test_one_device_linked_to_two_cameras_is_400():
    use_ring(fake_ring([(*FRONT, True)]))
    assert status("C1:Front Door Cam", "C2:Front Door Cam").status_code == 400


def test_same_camera_linked_twice_is_400():
    use_ring(fake_ring([(*FRONT, True), (*GARAGE, True)]))
    assert status("C1:Front Door Cam", "C1:Garage Cam").status_code == 400


# ----------------------------------------------------- token + Ring errors

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer"},
                                     {"Authorization": "Token abc"},
                                     {"Authorization": "Bearer, abc"}])
def test_missing_or_malformed_token_is_401(headers):
    use_ring(fake_ring([(*FRONT, True)]))
    assert status(headers=headers).status_code == 401


def test_x_ring_token_header_works_too():
    use_ring(fake_ring([(*FRONT, True)]))
    assert status(headers={"X-Ring-Token": "good-token-123"}).status_code == 200


def test_expired_token_is_401():
    use_ring(lambda request: httpx.Response(401, json={}))
    r = status("C1:Front Door Cam")
    assert r.status_code == 401 and "Playground" in r.json()["detail"]


def test_ring_server_error_is_502():
    use_ring(lambda request: httpx.Response(500, json={}))
    assert status("C1:Front Door Cam").status_code == 502


def test_ring_unreachable_is_502():
    def boom(request):
        raise httpx.ConnectError("no route")
    use_ring(boom)
    assert status("C1:Front Door Cam").status_code == 502


def test_token_is_never_echoed_back():
    secret = "SECRET-TOKEN-VALUE"
    use_ring(fake_ring([(*FRONT, False)]))
    ok = status("C1:Front Door Cam", headers={"Authorization": f"Bearer {secret}"})
    assert secret not in ok.text
    use_ring(lambda request: httpx.Response(500, json={}))
    bad = status("C1:Front Door Cam", headers={"Authorization": f"Bearer {secret}"})
    assert secret not in bad.text


def test_response_is_marked_do_not_cache():
    use_ring(fake_ring([(*FRONT, True)]))
    assert status().headers["cache-control"] == "no-store"


# --------------------------------------------------------------- misc

def test_unknown_archetype_is_404():
    use_ring(fake_ring([(*FRONT, True)]))
    assert status(archetype="ninja").status_code == 404


def test_a_different_archetype_is_accepted_and_reported():
    use_ring(fake_ring([(*FRONT, False)]))
    body = status("C1:Front Door Cam", archetype="camera_avoider").json()
    assert body["archetype"] == "camera_avoider"


def test_all_200_attackers_are_counted():
    use_ring(fake_ring([(*FRONT, True)]))
    assert status().json()["starts_tested"] == 200