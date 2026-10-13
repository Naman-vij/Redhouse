"""
ring_status.py (route)

GET /api/ring/status

Reads the REAL online/offline state of the user's Ring cameras and shows
what that means for the site's doors.

    GET /api/ring/status?link=C1:Playground Device
    Authorization: Bearer <ring token>        (or X-Ring-Token on /docs)

HOW IT WORKS
    1. read the Ring devices with the user's token            (reader.py)
    2. copy each LINKED device's online flag onto its camera  (sync.py)
    3. re-run the attacker simulation twice - once with every camera
       online (normal) and once with the cameras as they really are now
    4. report the difference per door, plus one plain sentence

Cameras the user has not linked stay SIMULATED, and the response labels
every camera "ring" or "simulated" so a live demo can never overclaim.

NOTE: the site (map + cameras) comes from get_site(). Today that is the
built-in demo house. When site-plan upload is added, this one function
is the only thing that changes - the route itself stays the same.

SECURITY: same rules as /api/ring/cameras - the token is never stored,
logged or echoed back, and the response is marked no-store.
"""

from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app import site_store
from app.engine_bridge import SCENARIO_PATH
from app.ring.reader import RingReader, RingReadError
from app.ring.sync import (LinkError, all_online, apply_ring_status,
                           describe_state, parse_links)
from app.routes.ring_cameras import _token_from_headers, get_reader

import indoor
from twin import Twin
from cameras import make_demo_cameras
from attackers import ARCHETYPES, two_seed_sets
from outage import compare_states

router = APIRouter(prefix="/api/ring", tags=["ring"])


@lru_cache(maxsize=1)
def _demo_site():
    return Twin.from_file(SCENARIO_PATH), make_demo_cameras()


def get_site(site: str | None = Query(default=None, description="An uploaded site's id. Leave out for the demo house.")):
    """
    (twin, cameras) every endpoint calculates for: the demo house, or an uploaded site.

    NOTE: written as a function so tests can swap in a different site with
    app.dependency_overrides. An id that does not exist (or is not even shaped
    like one) is a plain 404, so ids cannot be probed.
    """
    if site is None:
        return _demo_site()
    found = site_store.load_site(site)
    if found is None:
        raise HTTPException(status_code=404, detail="That site was not found. It may have been deleted.")
    return found


@router.get("/status")
def ring_status(
    response: Response,
    link: list[str] = Query(
        default=[],
        description="Link an engine camera to a Ring device: CameraName:RingDeviceNameOrId. "
                    "Repeat for each camera, e.g. link=C1:Playground Device"),
    archetype: str = Query(default="opportunist"),
    authorization: str | None = Header(default=None),
    x_ring_token: str | None = Header(default=None),
    reader: RingReader = Depends(get_reader),
    site=Depends(get_site),
):
    token = _token_from_headers(authorization, x_ring_token)

    chosen = next((a for a in ARCHETYPES if a.name == archetype), None)
    if chosen is None:
        raise HTTPException(status_code=404, detail=f"Unknown archetype '{archetype}'")

    try:
        links = parse_links(link)
    except LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    try:
        ring_cameras = reader.list_cameras(token)
    except RingReadError as exc:
        # 401 = the user's token is the problem; anything else = Ring or
        # the network is the problem (502). Same rule as /api/ring/cameras.
        status = 401 if exc.status_code == 401 else 502
        raise HTTPException(status_code=status, detail=exc.title) from None

    twin, cameras = site
    try:
        current, report = apply_ring_status(cameras, ring_cameras, links)
    except LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # NOTE: held-out attackers (seed 99), the same set the fix is scored on.
    _, held_out = two_seed_sets(twin, 200)
    results = compare_states(twin, all_online(cameras), current, held_out,
                             chosen.risk_weight)

    # which engine camera each Ring device ended up linked to
    linked_to = {r["ring_device_id"]: r["name"] for r in report if r["ring_device_id"]}
    live = sum(1 for r in report if r["source"] == "ring")

    response.headers["Cache-Control"] = "no-store"
    return {
        "archetype": chosen.name,
        "starts_tested": len(held_out),
        "live_cameras": live,
        "simulated_cameras": len(report) - live,
        "cameras": report,
        "doors": results,
        "summary": (indoor.describe_state(current, indoor.floor_report(twin, current),
                                          indoor.floor_report(twin, all_online(cameras)))
                    if twin.mode == "indoor" else describe_state(current, results)),
        # So the user can see what is available to link, even before linking.
        "ring_devices": [{"device_id": d.device_id, "name": d.name, "online": d.online,
                          "reported_at": d.reported_at,
                          "linked_to": linked_to.get(d.device_id)} for d in ring_cameras],
    }
