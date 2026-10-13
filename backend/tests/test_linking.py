"""
test_linking.py - tests for app/ring/linking.py.
Run from backend/ with:  python -m pytest tests/test_linking.py -v
"""
import re
import pytest
from app.ring.linking import (
    compute_nonce, time_is_acceptable, find_matching_token, match_link_attempt,
    UnclaimedToken, UnclaimedTokenStore, DEFAULT_MAX_AGE_MS,
)

KEY = "test-key"
ACCOUNT = "ava1.ring.account.XXXYYY"
T = "1771130906289"          # a time in MILLISECONDS, like Ring's docs example
NOW = 1771130906289 + 60_000  # one minute after the link was created


def token(account=ACCOUNT, received=1771130900000):
    return UnclaimedToken(account_id=account, access_token="secret-access",
                          refresh_token="secret-refresh", received_at_ms=received)


# ---------------- the nonce itself ----------------

def test_nonce_known_answer():
    # Pins the exact encoding: URL-safe Base64, no padding.
    assert compute_nonce(T, ACCOUNT, KEY) == "2U3SctumvX7iEwskouWOxa6Lf60kmrza1KklG8MDHW0"


def test_nonce_has_the_same_shape_as_rings_example():
    # Ring's docs show a 43-character URL-safe string with no "=".
    n = compute_nonce(T, ACCOUNT, KEY)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", n)


def test_nonce_is_not_hex_and_not_standard_base64():
    n = compute_nonce(T, ACCOUNT, KEY)
    assert "=" not in n and "+" not in n and "/" not in n
    assert not re.fullmatch(r"[0-9a-f]{64}", n)      # webhooks use hex; nonces must not


def test_nonce_accepts_int_or_string_time():
    assert compute_nonce(int(T), ACCOUNT, KEY) == compute_nonce(T, ACCOUNT, KEY)


def test_nonce_changes_with_any_input():
    base = compute_nonce(T, ACCOUNT, KEY)
    assert compute_nonce(str(int(T) + 1), ACCOUNT, KEY) != base     # 1 ms later
    assert compute_nonce(T, "ava1.ring.account.OTHER", KEY) != base  # other user
    assert compute_nonce(T, ACCOUNT, "other-key") != base            # other key


# ---------------- the time window ----------------

def test_recent_link_is_acceptable():
    assert time_is_acceptable(T, NOW)


def test_link_just_inside_the_window_is_acceptable():
    assert time_is_acceptable(T, int(T) + DEFAULT_MAX_AGE_MS)


def test_stale_link_is_rejected():
    assert not time_is_acceptable(T, int(T) + DEFAULT_MAX_AGE_MS + 1)


def test_link_from_the_future_is_rejected():
    assert not time_is_acceptable(str(NOW + 10 * 60_000), NOW)


def test_time_in_seconds_instead_of_milliseconds_is_rejected():
    # The classic mix-up. 1771130906 (seconds) looks like 1970 in ms.
    assert not time_is_acceptable("1771130906", NOW)


@pytest.mark.parametrize("bad", [None, "", "abc", "12.5x", [], {}])
def test_garbage_time_is_rejected_without_crashing(bad):
    assert time_is_acceptable(bad, NOW) is False


# ---------------- matching the right user ----------------

def test_correct_user_matches():
    mine = token()
    nonce = compute_nonce(T, ACCOUNT, KEY)
    assert find_matching_token(nonce, T, KEY, [mine]) == mine


def test_picks_the_right_user_among_several():
    alice, bob, carol = token("acct-alice"), token("acct-bob"), token("acct-carol")
    nonce = compute_nonce(T, "acct-bob", KEY)
    assert find_matching_token(nonce, T, KEY, [alice, bob, carol]) == bob


def test_someone_elses_nonce_does_not_match_my_token():
    # Ring's docs: "nonce is invalid with a different user's token".
    nonce_for_bob = compute_nonce(T, "acct-bob", KEY)
    assert find_matching_token(nonce_for_bob, T, KEY, [token("acct-alice")]) is None


def test_wrong_time_wrong_key_or_empty_do_not_match():
    nonce = compute_nonce(T, ACCOUNT, KEY)
    assert find_matching_token(nonce, str(int(T) + 1), KEY, [token()]) is None
    assert find_matching_token(nonce, T, "other-key", [token()]) is None
    assert find_matching_token("", T, KEY, [token()]) is None
    assert find_matching_token(None, T, KEY, [token()]) is None
    assert find_matching_token(nonce, T, "", [token()]) is None      # no key: fail closed


def test_no_tokens_means_no_match():
    assert find_matching_token(compute_nonce(T, ACCOUNT, KEY), T, KEY, []) is None


# ---------------- the token store ----------------

def test_store_add_and_list():
    s = UnclaimedTokenStore(clock_ms=lambda: 1000)
    s.add(token(received=1000))
    assert [t.account_id for t in s.active()] == [ACCOUNT]


def test_store_forgets_old_tokens():
    now = [1000]
    s = UnclaimedTokenStore(ttl_ms=60_000, clock_ms=lambda: now[0])
    s.add(token(received=1000))
    now[0] = 1000 + 59_000
    assert len(s.active()) == 1
    now[0] = 1000 + 61_000
    assert s.active() == []


def test_store_keeps_only_the_newest_token_per_account():
    s = UnclaimedTokenStore(clock_ms=lambda: 1000)
    s.add(UnclaimedToken(ACCOUNT, "old", None, 1000))
    s.add(UnclaimedToken(ACCOUNT, "new", None, 1000))
    assert [t.access_token for t in s.active()] == ["new"]


def test_store_claim_removes_the_token():
    s = UnclaimedTokenStore(clock_ms=lambda: 1000)
    t = token(received=1000)
    s.add(t)
    assert s.claim(t) is True
    assert s.active() == []
    assert s.claim(t) is False        # can't claim the same token twice


def test_secrets_never_appear_in_repr():
    shown = repr(token())
    assert "secret-access" not in shown and "secret-refresh" not in shown


# ---------------- the whole check ----------------

def test_full_happy_path():
    s = UnclaimedTokenStore(clock_ms=lambda: NOW)
    s.add(token(received=NOW - 5000))
    nonce = compute_nonce(T, ACCOUNT, KEY)
    got = match_link_attempt(nonce, T, KEY, s, current_ms=NOW)
    assert got is not None and got.account_id == ACCOUNT


def test_correct_nonce_but_stale_link_is_still_rejected():
    s = UnclaimedTokenStore(clock_ms=lambda: NOW)
    s.add(token(received=NOW - 5000))
    nonce = compute_nonce(T, ACCOUNT, KEY)
    too_late = int(T) + DEFAULT_MAX_AGE_MS + 1
    assert match_link_attempt(nonce, T, KEY, s, current_ms=too_late) is None


def test_forged_nonce_is_rejected():
    s = UnclaimedTokenStore(clock_ms=lambda: NOW)
    s.add(token(received=NOW - 5000))
    assert match_link_attempt("A" * 43, T, KEY, s, current_ms=NOW) is None