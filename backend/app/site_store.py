"""
site_store.py

Where uploaded sites are kept, and how they are loaded again.

WHAT IS STORED
  Only the GEOMETRY we extracted (outline, walls, doors, protected spots)
  and the camera records. The uploaded picture itself is NEVER saved:
  it is read, turned into shapes, and thrown away. A floor plan can show
  where the valuables are, so we keep as little of it as we can.

WHO CAN READ A SITE
  Each site gets a random id (about 96 bits). Whoever knows the id can
  read or delete it; there is no list endpoint, so ids cannot be guessed
  or browsed. The page remembers the ids in the browser.

WHERE
  Two places, same files, same rules:
    - an S3 bucket, when REDHOUSE_SITES_BUCKET is set (this is what runs on AWS: Lambda's own disk is
      wiped between runs, so sites would vanish). The bucket is private, encrypted, never public.
    - a folder of small JSON files otherwise. REDHOUSE_SITES_DIR overrides the folder.
  Nothing outside this file knows which one is in use.
"""

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app.engine_bridge import _REPO_ROOT

from twin import Twin
from site_plan import cameras_from_records

SITES_DIR = os.environ.get("REDHOUSE_SITES_DIR", os.path.join(_REPO_ROOT, "data", "sites"))
S3_PREFIX = "sites/"
_s3_client = None                                     # made on first use; tests put a fake one here
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")        # token_urlsafe(12) is 16 characters
MAX_SITES = 500                                       # a hackathon-sized cap, so a script cannot fill the disk
_CACHE: dict = {}                                     # id -> (file time, twin, cameras)
_CACHE_MAX = 16


class StoreFull(Exception):
    pass


def valid_id(site_id) -> bool:
    return isinstance(site_id, str) and bool(ID_RE.match(site_id))


def _path(site_id: str) -> str:
    if not valid_id(site_id):                         # nothing but safe characters ever reaches the file system
        raise ValueError("bad site id")
    return os.path.join(SITES_DIR, site_id + ".json")


def new_id() -> str:
    return secrets.token_urlsafe(12)


# ---- S3 mode -----------------------------------------------------------------------------------

def _bucket():
    return os.environ.get("REDHOUSE_SITES_BUCKET") or None


def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3                                  # only needed on AWS
        _s3_client = boto3.client("s3")
    return _s3_client


def _key(site_id: str) -> str:
    if not valid_id(site_id):
        raise ValueError("bad site id")
    return S3_PREFIX + site_id + ".json"


def _missing(err) -> bool:
    code = str(getattr(err, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound")


def _plain(x):
    """numpy numbers -> python numbers, so the JSON writer never trips."""
    return json.loads(json.dumps(x, default=float))


def save(twin_dict: dict, cameras: list, meta: dict) -> str:
    """Store a site and return its new id."""
    bucket = _bucket()
    if bucket:
        found = _s3().list_objects_v2(Bucket=bucket, Prefix=S3_PREFIX, MaxKeys=MAX_SITES + 1)
        if found.get("KeyCount", 0) >= MAX_SITES:
            raise StoreFull()
    else:
        os.makedirs(SITES_DIR, exist_ok=True)
        if sum(1 for n in os.listdir(SITES_DIR) if n.endswith(".json")) >= MAX_SITES:
            raise StoreFull()
    site_id = new_id()
    doc = {"version": 1, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "meta": _plain(meta), "twin": _plain(twin_dict), "cameras": _plain(cameras)}
    if bucket:
        _s3().put_object(Bucket=bucket, Key=_key(site_id), Body=json.dumps(doc).encode(),
                         ContentType="application/json", ServerSideEncryption="AES256")
        return site_id
    fd, tmp = tempfile.mkstemp(dir=SITES_DIR, suffix=".tmp")      # write whole, then rename: never a half file
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, _path(site_id))
    return site_id


def update_cameras(site_id: str, cameras: list) -> bool:
    """Replace a stored site's camera records (used when a camera is calibrated). False if there is no such site."""
    doc = read(site_id)
    if doc is None:
        return False
    doc["cameras"] = _plain(cameras)
    _CACHE.pop(site_id, None)
    bucket = _bucket()
    if bucket:
        _s3().put_object(Bucket=bucket, Key=_key(site_id), Body=json.dumps(doc).encode(),
                         ContentType="application/json", ServerSideEncryption="AES256")
        return True
    fd, tmp = tempfile.mkstemp(dir=SITES_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, _path(site_id))
    return True


def read(site_id: str):
    """The stored document, or None if there is no such site."""
    if not valid_id(site_id):
        return None
    bucket = _bucket()
    if bucket:
        try:
            body = _s3().get_object(Bucket=bucket, Key=_key(site_id))["Body"].read()
            return json.loads(body)
        except json.JSONDecodeError:
            return None
        except Exception as err:                      # no such site; anything else is a real fault
            if _missing(err):
                return None
            raise
    try:
        with open(_path(site_id)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_site(site_id: str):
    """(twin, cameras) for a stored site, or None. Remembered until the file changes."""
    if not valid_id(site_id):
        return None
    bucket = _bucket()
    try:
        if bucket:                                    # the object's ETag changes whenever its contents do
            stamp = _s3().head_object(Bucket=bucket, Key=_key(site_id))["ETag"]
        else:
            stamp = os.stat(_path(site_id)).st_mtime_ns
    except FileNotFoundError:
        _CACHE.pop(site_id, None)
        return None
    except Exception as err:
        if bucket and _missing(err):
            _CACHE.pop(site_id, None)
            return None
        raise
    hit = _CACHE.get(site_id)
    if hit and hit[0] == stamp:
        return hit[1], hit[2]
    doc = read(site_id)
    if doc is None:
        return None
    twin = Twin({**doc["twin"], "site_id": site_id})
    cameras = cameras_from_records(doc["cameras"], twin.grid_m)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[site_id] = (stamp, twin, cameras)
    return twin, cameras


def delete(site_id: str) -> bool:
    _CACHE.pop(site_id, None)
    if not valid_id(site_id):
        return False
    bucket = _bucket()
    if bucket:
        try:
            _s3().head_object(Bucket=bucket, Key=_key(site_id))      # delete_object says "ok" even for a missing key
        except Exception as err:
            if _missing(err):
                return False
            raise
        _s3().delete_object(Bucket=bucket, Key=_key(site_id))
        return True
    try:
        os.remove(_path(site_id))
        return True
    except FileNotFoundError:
        return False
