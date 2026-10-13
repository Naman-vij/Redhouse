"""test_email_report.py - emailing the PDF report. SES is a stub; nothing is sent."""
import email
from email import policy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import mailer, site_store
from app.routes import explain as explain_route, report as report_route

client = TestClient(app)


class SES:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    def send_email(self, **kw):
        if self.error:
            raise self.error
        self.sent.append(kw)


class Rejected(Exception):
    response = {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified."}}


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv("REDHOUSE_BEDROCK_MODEL", raising=False)
    monkeypatch.delenv("REDHOUSE_SITES_BUCKET", raising=False)
    monkeypatch.setenv("REDHOUSE_EMAIL_FROM", "reports@example.com")
    monkeypatch.setattr(site_store, "SITES_DIR", str(tmp_path / "sites"))
    site_store._CACHE.clear()
    explain_route._CACHE.clear()
    for lim in (report_route._PER_ADDRESS, report_route._PER_VISITOR, report_route._ALL):
        lim.hits.clear()


def stub(monkeypatch, ses):
    monkeypatch.setattr(mailer, "_ses", lambda: ses)
    return ses


def test_the_pdf_is_attached_and_sent_to_one_address(monkeypatch):
    ses = stub(monkeypatch, SES())
    r = client.post("/api/report/email", json={"email": "owner@example.com"})
    assert r.status_code == 204 and len(ses.sent) == 1
    call = ses.sent[0]
    assert call["FromEmailAddress"] == "reports@example.com"
    assert call["Destination"] == {"ToAddresses": ["owner@example.com"]}
    msg = email.message_from_bytes(call["Content"]["Raw"]["Data"], policy=policy.default)
    assert msg["To"] == "owner@example.com" and msg["Subject"].startswith("Your RedHouse report")
    pdf = [p for p in msg.iter_attachments()][0]
    assert pdf.get_content_type() == "application/pdf" and pdf.get_content().startswith(b"%PDF")
    assert "simulated" in msg.get_body().get_content()


@pytest.mark.parametrize("bad", ["", "nobody", "a@b", "a@b.c", "a b@example.com", "a@example.com,b@example.com",
                                 "a@example.com;b@example.com", "<a@example.com>", "a..b@example.com", "x" * 300 + "@example.com"])
def test_bad_addresses_are_refused(monkeypatch, bad):
    ses = stub(monkeypatch, SES())
    r = client.post("/api/report/email", json={"email": bad})
    assert r.status_code == 400 and not ses.sent


def test_not_set_up_says_so(monkeypatch):
    monkeypatch.delenv("REDHOUSE_EMAIL_FROM")
    ses = stub(monkeypatch, SES())
    r = client.post("/api/report/email", json={"email": "owner@example.com"})
    assert r.status_code == 503 and "download" in r.json()["detail"] and not ses.sent


def test_ses_test_mode_gets_a_plain_message(monkeypatch):
    stub(monkeypatch, SES(Rejected()))
    r = client.post("/api/report/email", json={"email": "owner@example.com"})
    assert r.status_code == 400 and "test mode" in r.json()["detail"]


def test_other_failures_are_a_502_without_details(monkeypatch):
    stub(monkeypatch, SES(RuntimeError("secret internals")))
    r = client.post("/api/report/email", json={"email": "owner@example.com"})
    assert r.status_code == 502 and "secret" not in r.text


def test_one_address_is_limited(monkeypatch):
    ses = stub(monkeypatch, SES())
    codes = [client.post("/api/report/email", json={"email": "owner@example.com"}).status_code for _ in range(4)]
    assert codes == [204, 204, 204, 429] and len(ses.sent) == 3


def test_one_visitor_is_limited(monkeypatch):
    stub(monkeypatch, SES())
    codes = [client.post("/api/report/email", json={"email": f"p{i}@example.com"},
                         headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"}).status_code for i in range(7)]
    assert codes == [204] * 6 + [429]
    assert client.post("/api/report/email", json={"email": "q@example.com"},
                       headers={"x-forwarded-for": "5.6.7.8"}).status_code == 204


def test_unknown_site_is_a_404_and_sends_nothing(monkeypatch):
    ses = stub(monkeypatch, SES())
    r = client.post("/api/report/email?site=nope", json={"email": "owner@example.com"})
    assert r.status_code == 404 and not ses.sent


def test_header_injection_is_harmless():
    msg = mailer.build_message("a@example.com", "Home\r\nBcc: x@evil.com", "Summary text here.", b"%PDF", "r.pdf")
    assert "Bcc" not in msg and "\n" not in msg["Subject"]


def test_rate_limit_window():
    lim = mailer.RateLimit(2, 10)
    assert lim.allow("k", 0) and lim.allow("k", 1) and not lim.allow("k", 2) and lim.allow("k", 11)
