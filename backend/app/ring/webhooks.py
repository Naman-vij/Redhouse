"""
webhooks.py

Pure logic for handling webhooks that Ring sends us. No web framework
and no AWS in here, so every function can be tested in isolation.

Three jobs:
  1. verify_signature - prove the message really came from Ring
  2. parse_event      - turn Ring's JSON into a simple, checked object
  3. DedupeStore      - ignore a message Ring delivers twice

Details below are from Ring's Notifications docs: the signature is in
the X-Signature header, formatted "sha256=<hex>", an HMAC-SHA256 made
with our HMAC signing key, hex-encoded.
"""

import hashlib
import hmac
import threading
import time
from dataclasses import dataclass

SIGNATURE_PREFIX = "sha256="


# ------------------------------------------------------------------
# 1. Signature check
# ------------------------------------------------------------------

def compute_signature(raw_body: bytes, hmac_key: str) -> str:
    """
    Work out the signature Ring SHOULD have sent for this body.

    NOTE: it is computed over the RAW BYTES of the request body,
    exactly as received - not over parsed-then-re-serialised JSON.
    Re-serialising can change spacing or key order, which changes the
    signature and makes every genuine message look fake.
    """
    digest = hmac.new(hmac_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_signature(raw_body: bytes, signature_header: str | None, hmac_key: str) -> bool:
    """
    True only if the X-Signature header matches the body.

    NOTE: hmac.compare_digest takes the same time whether the first
    character or the last one differs. A plain `==` stops at the first
    difference, and an attacker could use those tiny timing gaps to
    guess a valid signature one character at a time.
    """
    if not signature_header or not hmac_key:
        return False
    expected = compute_signature(raw_body, hmac_key)
    # Encode both to bytes so a weird (non-ASCII) header can't crash us.
    return hmac.compare_digest(expected.encode("utf-8"),
                               signature_header.strip().encode("utf-8"))


# ------------------------------------------------------------------
# 2. Parsing
# ------------------------------------------------------------------

@dataclass(frozen=True)
class RingEvent:
    request_id: str            # unique per delivery - used for de-duplication
    event_type: str            # e.g. "device_offline", "motion_detected"
    account_id: str | None     # which Ring user this belongs to
    device_id: str | None      # which camera
    sub_type: str | None = None        # e.g. "human" for motion
    timestamp_ms: int | None = None


def parse_event(payload: dict) -> RingEvent:
    """
    Turn Ring's JSON:API payload into a RingEvent.

    NOTE: Ring's payload looks like
        {"meta": {"request_id": ..., "account_id": ...},
         "data": {"type": "device_offline", "subType": ...,
                  "attributes": {"source": <device id>, "timestamp": ...}}}
    We check the pieces we depend on and raise ValueError if they're
    missing, so a malformed message becomes a clean 400 error instead
    of a crash deeper in the code.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    meta = payload.get("meta")
    data = payload.get("data")
    if not isinstance(meta, dict) or not isinstance(data, dict):
        raise ValueError("payload needs 'meta' and 'data' objects")

    request_id = meta.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("meta.request_id is required")

    event_type = data.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("data.type is required")

    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        attributes = {}

    return RingEvent(
        request_id=request_id,
        event_type=event_type,
        account_id=meta.get("account_id"),
        device_id=attributes.get("source"),
        sub_type=data.get("subType"),      # NOTE: Ring spells it camelCase
        timestamp_ms=attributes.get("timestamp"),
    )


# NOTE: events that change what our cameras can see, so the security
# audit must be re-run. Motion and doorbell presses are NOT here: they
# say something happened, not that the setup changed.
AUDIT_TRIGGER_EVENTS = {"device_offline", "device_online",
                        "device_added", "device_removed"}


def triggers_audit(event: RingEvent) -> bool:
    return event.event_type in AUDIT_TRIGGER_EVENTS


# ------------------------------------------------------------------
# 3. De-duplication
# ------------------------------------------------------------------

class DedupeStore:
    """
    Remembers which request_ids we've already handled.

    Ring can deliver the same webhook more than once (for example if
    our reply was slow). Without this, one camera-offline event could
    start the same audit twice.

    NOTE: this version keeps its memory in the running process, which
    is fine on a laptop. On AWS Lambda each instance has its OWN
    memory, so we'll swap this for a DynamoDB table with an expiry.
    The interface (first_time) stays the same, so nothing else changes.
    """

    def __init__(self, ttl_seconds: int = 7 * 24 * 3600, clock=time.time):
        self._ttl = ttl_seconds
        self._clock = clock            # injectable so tests can fake time
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()  # the server handles requests in parallel

    def first_time(self, request_id: str) -> bool:
        """True the first time we see this id, False for repeats."""
        now = self._clock()
        with self._lock:
            # forget ids older than the TTL so memory doesn't grow forever
            self._seen = {k: t for k, t in self._seen.items() if now - t < self._ttl}
            if request_id in self._seen:
                return False
            self._seen[request_id] = now
            return True