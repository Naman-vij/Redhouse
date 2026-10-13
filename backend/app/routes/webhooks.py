"""
webhooks.py (route)

POST /ring/webhook - where Ring sends "camera offline", "motion" etc.

Order matters. We do the cheap, safe checks first and only then act:
  1. signature  (is it really from Ring?)      -> else 401
  2. parse JSON (is it well-formed?)            -> else 400
  3. de-dupe    (have we handled it already?)   -> else 200 "duplicate"
  4. enqueue an audit if the event changes what the cameras see
  5. reply 200 fast (Ring requires a reply within 5 seconds)
"""

import json
import os

from fastapi import APIRouter, HTTPException, Request

from app.jobs import audit_queue
from app.ring.webhooks import (DedupeStore, parse_event, triggers_audit,
                               verify_signature)

router = APIRouter(prefix="/ring", tags=["ring"])

dedupe = DedupeStore()


def get_hmac_key() -> str:
    """
    NOTE: read at request time from an environment variable, so tests
    can set it. On AWS this will come from Secrets Manager instead -
    never hard-code the real key in the source.
    """
    return os.environ.get("RING_HMAC_KEY", "")


@router.post("/webhook")
async def receive_webhook(request: Request):
    # NOTE: we must read the RAW bytes first. Signature checking has to
    # use exactly what Ring sent, before any JSON parsing touches it.
    raw_body = await request.body()

    # 1. Authenticity
    if not verify_signature(raw_body, request.headers.get("X-Signature"), get_hmac_key()):
        # NOTE: deliberately vague. Don't tell an attacker WHY it failed.
        raise HTTPException(status_code=401, detail="invalid signature")

    # 2. Well-formed
    try:
        event = parse_event(json.loads(raw_body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad payload: {exc}")

    # 3. Duplicate? Reply 200 so Ring stops retrying, but do nothing.
    if not dedupe.first_time(event.request_id):
        return {"status": "duplicate"}

    # 4. Does this change what the cameras can see?
    if triggers_audit(event):
        audit_queue.enqueue({
            "type": "reaudit",
            "reason": event.event_type,
            "account_id": event.account_id,
            "device_id": event.device_id,
            "request_id": event.request_id,
        })
        return {"status": "accepted", "audit_queued": True}

    return {"status": "accepted", "audit_queued": False}
