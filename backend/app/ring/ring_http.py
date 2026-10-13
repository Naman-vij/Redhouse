"""
ring_http.py

The actual web calls we make TO Ring. Everything here is confirmed
against Ring's Partner API docs:

  POST https://oauth.ring.com/oauth/token          swap a code / refresh a token
  GET  https://api.amazonvision.com/v1/users/me    who does this token belong to?
  POST .../v1/accounts/me/app-integrations         confirm the account link
  PATCH .../v1/accounts/me/app-integrations        mark the link completed (mandatory)

Calls to Ring must come from our SERVER, never from the browser.

NOTE: the web client is passed in, so tests can swap in a fake one and
we never need a real Ring account to test this file.
"""

import re
from dataclasses import dataclass, field

import httpx

OAUTH_TOKEN_URL = "https://oauth.ring.com/oauth/token"
API_BASE_URL = "https://api.amazonvision.com"
APP_INTEGRATIONS_PATH = "/v1/accounts/me/app-integrations"
DEFAULT_TIMEOUT_S = 10.0


class RingApiError(Exception):
    """
    Ring answered with an error (or something unusable).

    NOTE: the message holds only the status code and a short, cleaned
    error title - never the request body, tokens or secrets - so it is
    safe to log.
    """

    def __init__(self, status_code: int, title: str):
        self.status_code = status_code
        self.title = title
        super().__init__(f"Ring API error {status_code}: {title}")


@dataclass(frozen=True)
class TokenSet:
    access_token: str = field(repr=False)          # repr=False: keep secrets out of logs
    refresh_token: str | None = field(default=None, repr=False)
    expires_in: int | None = None


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------

def mask_email(email: str) -> str:
    """
    Hide the middle of an email, like Ring's own example
    "xxx******xxx@example.com". Sent as the optional account_identifier
    so Ring can show the user which account they linked, without us
    handing over the full address.
    """
    if "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    n = len(local)
    keep = 3 if n >= 9 else 1 if n >= 3 else 0
    if keep == 0:
        return "*" * n + "@" + domain
    return local[:keep] + "*" * (n - 2 * keep) + local[-keep:] + "@" + domain


def _json_body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_title(response: httpx.Response) -> str:
    """A short, safe description of what went wrong."""
    body = _json_body(response)
    title = ""
    errors = body.get("errors")                       # Ring's JSON:API errors
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        title = str(errors[0].get("title") or errors[0].get("code") or "")
    elif isinstance(body.get("error"), str):          # the OAuth endpoint's style
        title = body["error"]
    # keep printable ASCII only, and cap the length
    title = re.sub(r"[^\x20-\x7E]", "", title)[:80].strip()
    return title or "no error detail"


def _raise_for_error(response: httpx.Response) -> None:
    if not response.is_success:
        raise RingApiError(response.status_code, _error_title(response))


def _status_of(body: dict) -> str | None:
    """Find the integration status wherever Ring puts it."""
    if isinstance(body.get("status"), str):
        return body["status"]
    data = body.get("data")
    if isinstance(data, dict):
        attributes = data.get("attributes")
        if isinstance(attributes, dict) and isinstance(attributes.get("status"), str):
            return attributes["status"]
    return None


# ------------------------------------------------------------------
# The client
# ------------------------------------------------------------------

class RingClient:
    def __init__(self, client_id: str, client_secret: str,
                 http: httpx.Client | None = None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=DEFAULT_TIMEOUT_S)

    def __repr__(self) -> str:
        # NOTE: never print the secret.
        return f"RingClient(client_id={self._client_id!r})"

    # ---------- tokens ----------

    def _token_request(self, form: dict) -> TokenSet:
        # NOTE: Ring's token endpoint wants a FORM body
        # (application/x-www-form-urlencoded), not JSON. Passing data=
        # makes httpx send it that way.
        response = self._http.post(OAUTH_TOKEN_URL, data=form)
        _raise_for_error(response)
        body = _json_body(response)

        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            raise RingApiError(response.status_code, "token response had no access_token")
        refresh = body.get("refresh_token")
        expires = body.get("expires_in")
        return TokenSet(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) else None,
            expires_in=expires if isinstance(expires, int) else None,
        )

    def exchange_code(self, code: str) -> TokenSet:
        """
        Swap the one-time code Ring gave us for tokens.
        NOTE: Ring says this must happen within 1 MINUTE of receiving
        the code, so do it immediately.
        """
        return self._token_request({
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": code,
            "client_secret": self._client_secret,
        })

    def refresh(self, refresh_token: str) -> TokenSet:
        """
        Get a fresh access token. NOTE: every refresh returns a NEW
        refresh token, and the old one stops working - so always save
        the new one.
        """
        return self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        })

    # ---------- API calls ----------

    def _api(self, method: str, path: str, access_token: str,
             json: dict | None = None) -> httpx.Response:
        # NOTE: Ring's own examples send Content-Type: application/json
        # even on GET requests, so we always include it.
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        response = self._http.request(method, API_BASE_URL + path,
                                      headers=headers, json=json)
        _raise_for_error(response)
        return response

    def get_account_id(self, access_token: str) -> str:
        """Which Ring user does this token belong to? (Ring's 'data.id')"""
        response = self._api("GET", "/v1/users/me", access_token)
        data = _json_body(response).get("data")
        account_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(account_id, str) or not account_id:
            raise RingApiError(response.status_code, "profile response had no account id")
        return account_id

    def confirm_link(self, access_token: str, nonce: str,
                     account_identifier: str | None = None) -> str | None:
        """
        Tell Ring: 'this nonce is valid, link these two accounts.'
        Call it IMMEDIATELY after matching - the window is time-limited.
        Returns Ring's status (expected: 'awaiting').
        """
        body = {"nonce": nonce}
        if account_identifier:
            body["account_identifier"] = account_identifier
        response = self._api("POST", APP_INTEGRATIONS_PATH, access_token, json=body)
        return _status_of(_json_body(response))

    def complete_link(self, access_token: str,
                      account_identifier: str | None = None) -> str | None:
        """
        Final step, and MANDATORY: until this runs the link stays
        'awaiting'. Returns Ring's status (expected: 'completed').
        """
        body: dict = {"status": "completed"}
        if account_identifier:
            body["account_identifier"] = account_identifier
        response = self._api("PATCH", APP_INTEGRATIONS_PATH, access_token, json=body)
        return _status_of(_json_body(response))