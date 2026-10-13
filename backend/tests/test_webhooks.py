"""
test_webhooks.py - tests for app/ring/webhooks.py (pure logic).
Run from backend/ with:  python -m pytest tests/test_webhooks.py -v
"""
import pytest
from app.ring.webhooks import (
    compute_signature, verify_signature, parse_event, triggers_audit,
    DedupeStore, RingEvent,
)

KEY = "test-key"
BODY = b'{"a":1}'

# NOTE: a payload shaped like the one in Ring's device_offline docs.
OFFLINE = {
    "meta": {"version": "1.1", "time": "2026-02-13T23:26:49Z",
             "request_id": "343385ae-1de8-4b29-85aa-07bd4b7112fd",
             "account_id": "ava1.ring.account.XXXYYY"},
    "data": {"id": "cam1_device_offline_1771025206000", "type": "device_offline",
             "attributes": {"source": "cam1", "source_type": "devices",
                            "timestamp": 1771025206000}},
}


# ---------------- signature ----------------

def test_signature_known_answer():
    # Pins the exact format: "sha256=" + HEX. This guards against the
    # mix-up Ring's FAQ warns about (webhooks = hex, nonces = base64).
    assert compute_signature(BODY, KEY) == \
        "sha256=486ecd1b18e77ef1945ee984d77f0882eaf31c75c94be6324b2c0472af9556e7"


def test_genuine_message_passes():
    assert verify_signature(BODY, compute_signature(BODY, KEY), KEY)


def test_tampered_body_fails():
    sig = compute_signature(BODY, KEY)
    assert not verify_signature(b'{"a":2}', sig, KEY)


def test_wrong_key_fails():
    assert not verify_signature(BODY, compute_signature(BODY, "other-key"), KEY)


@pytest.mark.parametrize("bad", [None, "", "   ", "sha256=", "nonsense",
                                 "sha256=zzzz", "caf\u00e9"])
def test_missing_or_garbage_header_fails_without_crashing(bad):
    assert verify_signature(BODY, bad, KEY) is False


def test_missing_key_fails():
    assert verify_signature(BODY, compute_signature(BODY, KEY), "") is False


def test_signature_without_prefix_fails():
    bare_hex = compute_signature(BODY, KEY).removeprefix("sha256=")
    assert not verify_signature(BODY, bare_hex, KEY)


# ---------------- parsing ----------------

def test_parse_offline_event():
    e = parse_event(OFFLINE)
    assert e.request_id == "343385ae-1de8-4b29-85aa-07bd4b7112fd"
    assert e.event_type == "device_offline"
    assert e.device_id == "cam1"
    assert e.account_id == "ava1.ring.account.XXXYYY"


def test_parse_reads_camelcase_subtype():
    payload = {"meta": {"request_id": "r1"},
               "data": {"type": "motion_detected", "subType": "human",
                        "attributes": {"source": "cam1"}}}
    assert parse_event(payload).sub_type == "human"


@pytest.mark.parametrize("bad", [
    None, [], "text", {},
    {"meta": {}, "data": {"type": "x"}},                      # no request_id
    {"meta": {"request_id": "r"}, "data": {}},                # no type
    {"meta": {"request_id": ""}, "data": {"type": "x"}},      # empty id
    {"meta": "no", "data": "no"},
])
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_event(bad)


# ---------------- which events re-run the audit ----------------

@pytest.mark.parametrize("kind,expected", [
    ("device_offline", True), ("device_online", True),
    ("device_added", True), ("device_removed", True),
    ("motion_detected", False), ("button_press", False),
])
def test_triggers_audit(kind, expected):
    e = RingEvent(request_id="r", event_type=kind, account_id=None, device_id=None)
    assert triggers_audit(e) is expected


# ---------------- de-duplication ----------------

def test_dedupe_first_true_then_false():
    d = DedupeStore()
    assert d.first_time("a") is True
    assert d.first_time("a") is False


def test_dedupe_ids_are_independent():
    d = DedupeStore()
    assert d.first_time("a") and d.first_time("b")


def test_dedupe_forgets_after_ttl():
    now = [1000.0]
    d = DedupeStore(ttl_seconds=60, clock=lambda: now[0])
    assert d.first_time("a")
    now[0] += 30
    assert not d.first_time("a")     # still remembered
    now[0] += 40                     # 70s total, past the 60s TTL
    assert d.first_time("a")         # forgotten, treated as new