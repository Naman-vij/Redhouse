"""test_report.py - the downloadable PDF report."""
import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import explain as ex, report as rep, site_store
from app.routes import explain as explain_route

import make_test_lab as lab

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv("REDHOUSE_BEDROCK_MODEL", raising=False)
    monkeypatch.delenv("REDHOUSE_SITES_BUCKET", raising=False)
    monkeypatch.setattr(site_store, "SITES_DIR", str(tmp_path / "sites"))
    site_store._CACHE.clear()
    explain_route._CACHE.clear()


def text_of(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    import unicodedata
    return unicodedata.normalize("NFKC", "\n".join(p.get_text() for p in doc))


@pytest.fixture(scope="module")
def lab_png(tmp_path_factory):
    return open(lab.make_lab(str(tmp_path_factory.mktemp("lab") / "lab.png")), "rb").read()


def make_lab_site(png):
    s = json.dumps({**lab.user_clicks(), "mode": "indoor", "name": "Chem lab"})
    r = client.post("/api/sites", files={"file": ("p.png", png, "image/png")}, data={"settings": s})
    assert r.status_code == 201
    return r.json()["site_id"]


def test_demo_report_is_a_real_pdf_with_the_key_numbers():
    r = client.get("/api/report")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert 'attachment; filename="redhouse-report-demo-house.pdf"' == r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    doc = pymupdf.open(stream=r.content, filetype="pdf")
    assert doc.page_count >= 2
    t = text_of(r.content)
    for needle in ("RedHouse security report", "In plain words", "Side door", "60 of 100", "40 of 100", "0 of 100",
                   "Turn C2", "Add 1 camera", "Before and after", "If a camera fails", "Page 1 of"):
        assert needle in t, needle
    assert "Built-in summary" in t                                           # Bedrock is not configured here
    assert "front_door" not in t                                              # ids are shown as words
    assert len(doc[1].get_images()) >= 2                                      # the two maps


def test_the_report_says_when_bedrock_wrote_the_summary(monkeypatch):
    class Stub:
        def converse(self, **kw):
            return {"output": {"message": {"content": [{"text": "Turning C2 would cut the side door from 60 to 40 out of 100, and one new camera would close it."}]}}}
    monkeypatch.setenv("REDHOUSE_BEDROCK_MODEL", "test-model")
    monkeypatch.setattr(ex, "_bedrock_client", lambda: Stub())
    t = text_of(client.get("/api/report").content)
    assert "Written by Amazon Bedrock (test-model)" in t and "Turning C2 would cut the side door" in t


def test_a_rejected_model_text_never_reaches_the_report(monkeypatch):
    class Stub:
        def converse(self, **kw):
            return {"output": {"message": {"content": [{"text": "Honestly 77 of 100 intruders get in at the side door unseen today."}]}}}
    monkeypatch.setenv("REDHOUSE_BEDROCK_MODEL", "test-model")
    monkeypatch.setattr(ex, "_bedrock_client", lambda: Stub())
    t = text_of(client.get("/api/report").content)
    assert "77 of 100" not in t and "Built-in summary" in t


def test_indoor_report(lab_png):
    sid = make_lab_site(lab_png)
    r = client.get("/api/report", params={"site": sid})
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('chem-lab.pdf"')
    t = text_of(r.content)
    for needle in ("Indoor floor", "Floor watched today", "30%", "96%", "Protected spot", "Server room", "Add 7 cameras",
                   "N1", "goes blind"):
        assert needle in t, needle
    assert pymupdf.open(stream=r.content, filetype="pdf").page_count >= 2


def test_report_for_an_unknown_site_is_not_found():
    assert client.get("/api/report", params={"site": "A" * 16}).status_code == 404
    assert client.get("/api/report", params={"site": "../../x"}).status_code == 404


def test_names_are_escaped_not_read_as_html():
    plan = client.get("/api/plan").json()
    scene = client.get("/api/scene").json()
    pdf = rep.build_report(site_name="<b>Evil</b> & <img src=x>", archetype="opportunist", plan=plan,
                           explained={"text": "<i>hello</i> 60", "source": "template", "model": None},
                           today=scene, after=scene, outage=client.get("/api/outage").json())
    t = text_of(pdf)
    assert "<b>Evil</b> & <img src=x>" in t and "<i>hello</i>" in t


def test_map_is_a_png():
    png = rep.map_png(client.get("/api/scene").json(), width_px=500)
    assert png.startswith(b"\x89PNG")
