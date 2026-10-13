"""
demo.py

Endpoints that run the engine on the built-in DEMO house and demo
cameras. These let the frontend be built and tested before any real
Ring data exists. Later, the same shapes will be served from real
Ring-linked sites.
"""

from fastapi import APIRouter, Depends, HTTPException

from app import engine_bridge  # noqa: F401  (side effect: puts engine/ on the path)
from app.engine_bridge import SCENARIO_PATH
from app.routes.ring_status import get_site

from twin import Twin
from cameras import make_demo_cameras
from attackers import two_seed_sets, ARCHETYPES
from defender import search_best_fix, verify_on_held_out, describe_fix, success_rate

router = APIRouter(prefix="/api/demo", tags=["demo"])


def _find_door(twin: Twin, door_id: str):
    for e in twin.entrances:
        if e["id"] == door_id:
            return tuple(e["point"])
    raise HTTPException(status_code=404, detail=f"Unknown door '{door_id}'")


@router.get("/twin")
def get_twin():
    """
    Everything the frontend needs to DRAW the property: boundary,
    obstacles, doors, grid size, and where each camera is.

    NOTE: we send plain lists/numbers only. numpy arrays and shapely
    shapes can't be turned into JSON directly, so we convert here.
    """
    twin = Twin.from_file(SCENARIO_PATH)
    return {
        "grid_m": twin.grid_m,
        "n_rows": twin.n_rows,
        "n_cols": twin.n_cols,
        "boundary": [list(p) for p in twin.boundary.exterior.coords],
        "obstacles": [[list(p) for p in ob.exterior.coords] for ob in twin.obstacles],
        "entrances": twin.entrances,
        "cameras": [{"name": c.name, "position": list(c.position),
                     "max_range_m": c.max_range_m} for c in make_demo_cameras()],
    }


_AUDITS: dict = {}


def _run_audit(door_id: str, archetype_name: str, twin: Twin, cameras: list):
    """
    Run the full audit once and remember the answer.

    NOTE: remembered per (site, door, archetype), because the search tries
    54 camera/angle combinations and takes a few seconds. The inputs are
    fixed seeds, so the answer never changes - there's no reason to
    recompute it on every page load. The key includes the site's shape and
    cameras, so one site's answer is never shown for another.
    """
    from app.routes.plan import _fingerprint
    key = (door_id, archetype_name, _fingerprint(twin, cameras))
    if key in _AUDITS:
        return _AUDITS[key]

    door = _find_door(twin, door_id)
    archetype = next((a for a in ARCHETYPES if a.name == archetype_name), None)
    if archetype is None:
        raise HTTPException(status_code=404, detail=f"Unknown archetype '{archetype_name}'")

    training, held_out = two_seed_sets(twin, 200)
    search = search_best_fix(twin, cameras, door, training, archetype.risk_weight)
    verify = verify_on_held_out(
        twin, cameras, door, held_out, archetype.risk_weight,
        search["best_camera"] or cameras[0].name, search["best_angle"])
    fix = describe_fix(search, verify)

    if len(_AUDITS) > 64:
        _AUDITS.pop(next(iter(_AUDITS)))
    _AUDITS[key] = {
        "door": door_id,
        "archetype": archetype.name,
        "starts_tested": len(training),
        "success_rate": search["baseline_rate"],
        "fix": fix,
    }
    return _AUDITS[key]


@router.get("/audit")
def get_audit(door: str = "side_door", archetype: str = "opportunist", site=Depends(get_site)):
    """Run the red-team audit on the demo house (or an uploaded site) and return the fix."""
    twin, cameras = site
    return _run_audit(door, archetype, twin, cameras)
