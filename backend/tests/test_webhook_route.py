"""
test_webhook_route.py - tests the real POST /ring/webhook endpoint
end to end (signature -> parse -> de-dupe -> queue).
"""
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.jobs import audit_queue
from app.ring.webhooks import compute_signature
from app.routes import webhooks as route

KEY = "route-test-key"
client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    # NOTE: runs before EVERY test: set the key, empty the queue and
    # reset the dedupe memory, so tests can't affect each other.
    monkeypatch.setenv("RING_HMAC_KEY", KEY)
    audit_queue.clear()
    route.dedupe = route.DedupeStore()


def payload(kind="device_offline", request_id="req-1", sub=None):
    data = {"type": kind, "attributes": {"source": "cam1"}}
    if sub:
        data["subType"] = sub
    return {"meta": {"request_id": request_id, "account_id": "acct-1"}, "data": data}


def post(body: dict, signed=True, key=KEY):
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Signature"] = compute_signature(raw, key)
    return client.post("/ring/webhook", content=raw, headers=headers)


def test_valid_offline_event_queues_an_audit():
    r = post(payload("device_offline"))
    assert r.status_code == 200
    assert r.json() == {"status": "accepted", "audit_queued": True}
    assert len(audit_queue.jobs) == 1
    job = audit_queue.jobs[0]
    assert job["reason"] == "device_offline" and job["device_id"] == "cam1"


def test_motion_event_is_accepted_but_queues_no_audit():
    r = post(payload("motion_detected", sub="human"))
    assert r.status_code == 200
    assert r.json()["audit_queued"] is False
    assert audit_queue.jobs == []


def test_missing_signature_is_rejected():
    assert post(payload(), signed=False).status_code == 401
    assert audit_queue.jobs == []


def test_forged_signature_is_rejected():
    assert post(payload(), key="attacker-guess").status_code == 401
    assert audit_queue.jobs == []


def test_tampered_body_is_rejected():
    raw = json.dumps(payload("motion_detected")).encode()
    sig = compute_signature(raw, KEY)
    tampered = raw.replace(b"motion_detected", b"device_offline")
    r = client.post("/ring/webhook", content=tampered, headers={"X-Signature": sig})
    assert r.status_code == 401
    assert audit_queue.jobs == []


def test_duplicate_delivery_is_ignored():
    assert post(payload(request_id="same")).json()["status"] == "accepted"
    again = post(payload(request_id="same"))
    assert again.status_code == 200            # 200 so Ring stops retrying
    assert again.json() == {"status": "duplicate"}
    assert len(audit_queue.jobs) == 1          # but only ONE audit queued


def test_signed_but_malformed_json_is_400():
    raw = b"{not json"
    r = client.post("/ring/webhook", content=raw,
                    headers={"X-Signature": compute_signature(raw, KEY)})
    assert r.status_code == 400


def test_signed_payload_missing_fields_is_400():
    raw = json.dumps({"meta": {}, "data": {}}).encode()
    r = client.post("/ring/webhook", content=raw,
                    headers={"X-Signature": compute_signature(raw, KEY)})
    assert r.status_code == 400


def test_no_key_configured_rejects_everything():
    # If we forgot to set RING_HMAC_KEY, fail CLOSED (reject), not open.
    import os
    os.environ.pop("RING_HMAC_KEY", None)
    assert post(payload()).status_code == 401
