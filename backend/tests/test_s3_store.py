"""test_s3_store.py - the same site store, kept in an S3 bucket instead of a folder (a fake bucket stands in for S3)."""
import io
import json

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.main import app
from app import site_store

import make_test_lab as lab

client = TestClient(app)


class FakeS3:
    """Just enough of the S3 client: put, get, head, delete, list."""
    def __init__(self):
        self.objects, self.puts = {}, []

    def _missing(self, op):
        return ClientError({"Error": {"Code": "NoSuchKey" if op == "GetObject" else "404", "Message": "x"}}, op)

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append({"Key": Key, **kw})
        self.objects[(Bucket, Key)] = (Body, f'"{len(self.puts)}"')

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self._missing("GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise self._missing("HeadObject")
        return {"ETag": self.objects[(Bucket, Key)][1]}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        keys = [k for (b, k) in self.objects if b == Bucket and k.startswith(Prefix)]
        return {"KeyCount": min(len(keys), MaxKeys)}


@pytest.fixture(autouse=True)
def s3(monkeypatch, tmp_path):
    fake = FakeS3()
    monkeypatch.setenv("REDHOUSE_SITES_BUCKET", "test-bucket")
    monkeypatch.setattr(site_store, "_s3_client", fake)
    monkeypatch.setattr(site_store, "SITES_DIR", str(tmp_path / "must-stay-empty"))
    site_store._CACHE.clear()
    return fake


@pytest.fixture(scope="module")
def lab_png(tmp_path_factory):
    return open(lab.make_lab(str(tmp_path_factory.mktemp("lab") / "lab.png")), "rb").read()


def upload(png):
    s = json.dumps({**lab.user_clicks(), "mode": "indoor", "name": "Chem lab"})
    return client.post("/api/sites", files={"file": ("plan.png", png, "image/png")}, data={"settings": s})


def test_a_site_is_saved_in_the_bucket_encrypted_and_not_on_disk(lab_png, s3, tmp_path):
    r = upload(lab_png)
    assert r.status_code == 201
    sid = r.json()["site_id"]
    assert ("test-bucket", f"sites/{sid}.json") in s3.objects
    assert s3.puts[0]["ServerSideEncryption"] == "AES256"
    assert not (tmp_path / "must-stay-empty").exists()
    assert b"PNG" not in s3.objects[("test-bucket", f"sites/{sid}.json")][0]        # never the picture


def test_a_saved_site_can_be_analysed(lab_png):
    sid = upload(lab_png).json()["site_id"]
    assert client.get(f"/api/sites/{sid}").status_code == 200
    scene = client.get("/api/scene", params={"site": sid}).json()
    assert scene["floor"]["open_floor_m2"] > 100


def test_the_cache_follows_changes_in_the_bucket(lab_png, s3):
    sid = upload(lab_png).json()["site_id"]
    first = site_store.load_site(sid)
    assert site_store.load_site(sid)[0] is first[0]                                  # unchanged -> same object
    key = ("test-bucket", f"sites/{sid}.json")
    s3.objects[key] = (s3.objects[key][0], '"changed"')
    assert site_store.load_site(sid)[0] is not first[0]


def test_delete_removes_it_and_a_second_delete_says_not_found(lab_png, s3):
    sid = upload(lab_png).json()["site_id"]
    assert client.delete(f"/api/sites/{sid}").status_code in (200, 204)
    assert not s3.objects
    assert client.get(f"/api/sites/{sid}").status_code == 404
    assert site_store.delete(sid) is False


def test_an_unknown_site_is_not_found(s3):
    assert site_store.read("A" * 16) is None and site_store.load_site("A" * 16) is None


def test_the_cap_applies_in_the_bucket(lab_png, monkeypatch):
    monkeypatch.setattr(site_store, "MAX_SITES", 1)
    assert upload(lab_png).status_code == 201
    assert upload(lab_png).status_code in (409, 429, 507, 503)


def test_a_real_s3_fault_is_not_mistaken_for_not_found(s3):
    def boom(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
    s3.get_object = boom
    with pytest.raises(ClientError):
        site_store.read("A" * 16)


def test_origin_secret_blocks_calls_that_skip_cloudfront(monkeypatch):
    monkeypatch.setenv("REDHOUSE_ORIGIN_SECRET", "s3cret-value")
    assert client.get("/health").status_code == 403
    assert client.get("/health", headers={"X-Origin-Verify": "wrong"}).status_code == 403
    assert client.get("/health", headers={"X-Origin-Verify": "s3cret-value"}).json() == {"status": "ok"}


def test_no_origin_secret_means_open_for_local_use(monkeypatch):
    monkeypatch.delenv("REDHOUSE_ORIGIN_SECRET", raising=False)
    assert client.get("/health").status_code == 200
