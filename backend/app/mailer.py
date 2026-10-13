"""
mailer.py

Send the PDF report to an address the person typed, through Amazon SES.

NOT SET UP: if REDHOUSE_EMAIL_FROM is empty, nothing is sent and the page tells the person to download instead.
SES TEST MODE: a new AWS account can only send to addresses it has verified. SES then refuses the others
(MessageRejected) and we say so plainly. Asking AWS for production access lifts that.
NO STORAGE: the address is used for this one message and is not saved or logged.
ABUSE: the endpoint can only send this one fixed message, to one address, and is rate limited (RateLimit below).
"""

import os
import re
import time
from collections import defaultdict, deque
from email.message import EmailMessage

ADDRESS = re.compile(r"^[^@\s,;<>\"()\[\]\\]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,}$")


class MailError(Exception):
    """Carries an HTTP status and a message fit to show a person."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def clean_address(raw: str) -> str:
    addr = (raw or "").strip()
    if len(addr) > 254 or not ADDRESS.match(addr) or ".." in addr:
        raise MailError(400, "That does not look like an email address.")
    return addr


def sender() -> str:
    return os.environ.get("REDHOUSE_EMAIL_FROM", "").strip()


def one_line(text: str, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:limit]


class RateLimit:
    """At most `limit` hits per `window` seconds for each key. Kept in memory, so each server process counts its own."""
    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 5000:                       # forget idle keys
            for k in [k for k, v in self.hits.items() if not v]:
                del self.hits[k]
        return True


def build_message(to: str, site_name: str, summary: str, pdf: bytes, filename: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"] = sender(), to
    msg["Subject"] = f"Your RedHouse report: {one_line(site_name) or 'your site'}"
    msg.set_content(
        f"Your RedHouse report for {one_line(site_name) or 'your site'} is attached.\n\n"
        f"{summary.strip()}\n\n"
        "These are simulated intruders on a model of your site. It shows where cameras help; it is not a guarantee.\n"
        "You asked for this report on the RedHouse page. Nothing else will be sent to this address.\n"
    )
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return msg


def _ses():
    import boto3     # imported here so the app runs without it
    return boto3.client("sesv2", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def send_report(to: str, site_name: str, summary: str, pdf: bytes, filename: str, client=None) -> None:
    if not sender():
        raise MailError(503, "Email is not set up on this site. Use the download button instead.")
    msg = build_message(to, site_name, summary, pdf, filename)
    try:
        (client or _ses()).send_email(
            FromEmailAddress=sender(), Destination={"ToAddresses": [to]}, Content={"Raw": {"Data": msg.as_bytes()}})
    except Exception as err:
        code = getattr(err, "response", {}).get("Error", {}).get("Code", "")
        if code in ("MessageRejected", "NotFoundException", "BadRequestException") or "not verified" in str(err):
            raise MailError(400, "The email service is still in test mode and can only send to addresses that were "
                                 "verified first. Use the download button instead.")
        raise MailError(502, "The email could not be sent. Use the download button instead.")
