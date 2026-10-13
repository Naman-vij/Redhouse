"""
linking.py

The security-critical part of connecting a Ring account: proving that
the person who just landed on OUR sign-in page is the same Ring user
whose permission Ring just sent us.

How Ring's one-way account linking works (from Ring's docs):
  1. Ring sends us an access token for the user (the "token exchange").
     We look up that user's Account ID and keep the token as UNCLAIMED.
  2. Ring redirects the user's browser to our Account Link URL with two
     query parameters:  ?nonce=...&time=...
  3. The user signs in to RedHouse. We work out WHICH unclaimed token
     belongs to this person by recomputing the nonce for each one.
  4. We tell Ring the link is confirmed (separate file, next).

This file has no web or network code - just the maths and the matching.
"""

import base64
import hashlib
import hmac
import threading
import time
from dataclasses import dataclass, field

# NOTE: Ring says the nonce has a "time-bound validation window", but
# the doc pages I could read don't give its length. 10 minutes is OUR
# safe default; tune it once we see real behaviour.
DEFAULT_MAX_AGE_MS = 10 * 60 * 1000

# NOTE: allow a little clock difference between Ring's servers and ours.
MAX_CLOCK_SKEW_MS = 60 * 1000


def now_ms() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------
# The nonce
# ------------------------------------------------------------------

def compute_nonce(time_param: str | int, account_id: str, hmac_key: str) -> str:
    """
    Recompute the nonce Ring would have made for this user and time.

    Ring's recipe: HMAC-SHA256(key, "<timestamp_ms>:<account_id>"),
    encoded as URL-safe Base64 WITHOUT the "=" padding.

    NOTE (two traps from Ring's FAQ):
      - time is in MILLISECONDS, and must be used exactly as received.
      - webhooks use HEX encoding; nonces use URL-safe Base64. Same
        key, different encoding. Mixing them up fails silently.
    """
    message = f"{time_param}:{account_id}".encode("utf-8")
    mac = hmac.new(hmac_key.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def time_is_acceptable(time_param, current_ms: int,
                       max_age_ms: int = DEFAULT_MAX_AGE_MS) -> bool:
    """
    Is this link attempt recent enough to trust?

    Rejects stale links (someone replaying an old URL), links dated
    far in the future, and anything that isn't a number.

    NOTE: because Ring's time is in milliseconds, a value in SECONDS
    (like 1771130906) looks like it's from 1970 and is rejected as
    ancient - which is the right outcome and catches that mix-up early.
    """
    try:
        sent_at = int(time_param)
    except (TypeError, ValueError):
        return False
    age = current_ms - sent_at
    return -MAX_CLOCK_SKEW_MS <= age <= max_age_ms


# ------------------------------------------------------------------
# Unclaimed tokens
# ------------------------------------------------------------------

@dataclass(frozen=True)
class UnclaimedToken:
    """
    A Ring access token we've received but not yet tied to a signed-in
    RedHouse user.

    NOTE: repr=False keeps the secrets OUT of logs and error messages.
    Printing one of these shows the account id and nothing sensitive.
    """
    account_id: str
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    received_at_ms: int = 0


class UnclaimedTokenStore:
    """
    Holds tokens between step 1 (Ring sends the token) and step 3 (the
    user signs in). Expired entries are dropped automatically.

    NOTE: in memory for now. On AWS this becomes a DynamoDB table with
    an expiry, because Lambda instances don't share memory. The
    methods stay the same.
    """

    def __init__(self, ttl_ms: int = 15 * 60 * 1000, clock_ms=now_ms):
        self._ttl_ms = ttl_ms
        self._clock_ms = clock_ms
        self._tokens: dict[str, UnclaimedToken] = {}
        self._lock = threading.Lock()

    def _purge(self, current: int) -> None:
        self._tokens = {a: t for a, t in self._tokens.items()
                        if current - t.received_at_ms < self._ttl_ms}

    def add(self, token: UnclaimedToken) -> None:
        # NOTE: one entry per Ring account. If the same person starts
        # linking twice, the newest token replaces the older one.
        with self._lock:
            self._purge(self._clock_ms())
            self._tokens[token.account_id] = token

    def active(self) -> list[UnclaimedToken]:
        with self._lock:
            self._purge(self._clock_ms())
            return list(self._tokens.values())

    def claim(self, token: UnclaimedToken) -> bool:
        """Remove a token once it has been used. True if it was there."""
        with self._lock:
            return self._tokens.pop(token.account_id, None) is not None


# ------------------------------------------------------------------
# Matching
# ------------------------------------------------------------------

def find_matching_token(nonce: str | None, time_param: str, hmac_key: str,
                        unclaimed: list[UnclaimedToken]) -> UnclaimedToken | None:
    """
    Which unclaimed token does this nonce belong to?

    For each token we recompute the nonce from its stored account id and
    the received time, then compare in constant time. Only the right
    user's token produces a matching nonce.

    NOTE: we deliberately check EVERY token, even after finding a match,
    so the time taken doesn't reveal which position matched.
    """
    if not nonce or not hmac_key:
        return None                       # fail closed

    received = nonce.encode("utf-8")
    match = None
    for token in unclaimed:
        expected = compute_nonce(time_param, token.account_id, hmac_key)
        if hmac.compare_digest(expected.encode("utf-8"), received) and match is None:
            match = token
    return match


def match_link_attempt(nonce: str | None, time_param, hmac_key: str,
                       store: UnclaimedTokenStore,
                       current_ms: int | None = None,
                       max_age_ms: int = DEFAULT_MAX_AGE_MS) -> UnclaimedToken | None:
    """
    The whole check for one visit to our Account Link page:
    fresh enough? and does the nonce match a token we're holding?
    Returns the matching token, or None.
    """
    current = now_ms() if current_ms is None else current_ms
    if not time_is_acceptable(time_param, current, max_age_ms):
        return None
    return find_matching_token(nonce, str(time_param), hmac_key, store.active())
