"""
scene.py (route)

Everything the MAP needs, in one response.

    GET /api/scene?door=side_door&archetype=opportunist&offline=C1,C3&rotate=C2:35
    GET /api/outage?archetype=opportunist

/api/scene runs the simulator for ONE situation and returns:
    site      the property: boundary, buildings, doors, cameras (with where
              each one points)
    coverage  a grid: how likely the cameras are to see someone in each cell
    routes    example attacker routes to the chosen door - the ones that got
              through unseen, and a few that were caught
    doors     for every door: attacker success now, and with every camera
              working (so the page can show the change)
    summary   one sentence about the cameras' current state

The query parameters are WHAT-IFS:
    offline   comma list of cameras to treat as offline        (C1,C3)
    rotate    preview cameras turned by some degrees           (C2:35 or C2:35,C3:-75)
              positive = counter-clockwise, negative = clockwise
    add       preview NEW cameras, as x:y:heading, comma separated   (5.3:3.3:90)
              (these are proposals from /api/plan, drawn but not real)

/api/outage answers the standing question "if THIS camera failed, what
happens at each door?" for every camera - the table on the page.

NOTE: nothing here is random. The attackers come from fixed seeds, so
the same question always gets the same answer.
"""

import math

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from shapely.geometry import LineString, Point

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app.ring.sync import all_online, describe_state
from app.routes.ring_status import get_site

from attackers import ARCHETYPES, two_seed_sets
from coverage import camera_probability_grid, combine_cameras
from defender import build_risk, success_rate, with_rotation
from metrics import audit_door
import indoor
from planner import MAX_ADDITIONS, make_new_camera
from outage import compare_states, describe_change, single_points_of_failure, with_online

router = APIRouter(prefix="/api", tags=["scene"])

# How many example routes to draw. Enough to show the pattern, few enough
# to stay readable and keep the response small.
MAX_BREACH_ROUTES = 16
MAX_CAUGHT_ROUTES = 8


# ----------------------------------------------------------------------
# Reading the what-if parameters
# ----------------------------------------------------------------------

def _archetype(name: str):
    found = next((a for a in ARCHETYPES if a.name == name), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown archetype '{name}'")
    return found


def _parse_offline(raw: str | None, known: set) -> list[str]:
    names = [n.strip() for n in (raw or "").split(",") if n.strip()]
    for n in names:
        if n not in known:
            raise HTTPException(status_code=400,
                                detail=f"There is no camera '{n}'. Cameras: {', '.join(sorted(known))}.")
    return list(dict.fromkeys(names))          # drop repeats, keep order


def _parse_rotate(raw: str | None, known: set) -> list:
    """'C2:35,C3:-75' -> [('C2', 35.0), ('C3', -75.0)]. Empty list when not given."""
    turns = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        name, sep, angle = part.partition(":")
        try:
            degrees = float(angle)
        except ValueError:
            degrees = math.nan
        if not sep or not math.isfinite(degrees) or abs(degrees) > 180:
            raise HTTPException(status_code=400,
                                detail="rotate must look like  C2:35  (camera, degrees between -180 and 180).")
        if name.strip() not in known:
            raise HTTPException(status_code=400,
                                detail=f"There is no camera '{name.strip()}'. Cameras: {', '.join(sorted(known))}.")
        turns.append((name.strip(), degrees))
    return turns


def _parse_add(raw: str | None, limit: int = MAX_ADDITIONS) -> list:
    """'5.3:3.3:90,12:1:45' -> [(5.3, 3.3, 90.0), (12.0, 1.0, 45.0)]"""
    out = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        bits = part.split(":")
        try:
            x, y, heading = (float(b) for b in bits)
            ok = len(bits) == 3 and all(math.isfinite(v) for v in (x, y, heading))
        except ValueError:
            ok = False
        if not ok:
            raise HTTPException(status_code=400, detail="add must look like  5.3:3.3:90  (x metres, y metres, heading degrees).")
        out.append((x, y, heading))
    if len(out) > limit:
        raise HTTPException(status_code=400, detail=f"At most {limit} new cameras can be previewed.")
    return out


# ----------------------------------------------------------------------
# Turning engine objects into plain JSON
# ----------------------------------------------------------------------

def _r2(x) -> float:
    return round(float(x), 2)


def _route_points(twin, route) -> list:
    """A route (grid cells) -> a few points in metres, for drawing."""
    pts = [twin._cell_center(r, c) for r, c in route]
    if len(pts) < 2:
        return [[_r2(x), _r2(y)] for x, y in pts]
    line = LineString(pts).simplify(0.3)        # a straight run needs only 2 points
    return [[_r2(x), _r2(y)] for x, y in line.coords]


def _view_polygon(cam):
    """A calibrated camera's measured ground, in metres, turned by however far the camera is turned now. None otherwise."""
    if cam.view_polygon is None:
        return None
    a = math.radians(((cam.heading_deg or 0.0) - (cam.view_heading_deg or 0.0)))
    px, py = cam.position
    return [[_r2(px + (x - px) * math.cos(a) - (y - py) * math.sin(a)),
             _r2(py + (x - px) * math.sin(a) + (y - py) * math.cos(a))] for x, y in cam.view_polygon]


def _spread(items: list, limit: int) -> list:
    """Up to `limit` items, evenly spread through the list."""
    if len(items) <= limit:
        return items
    idx = np.linspace(0, len(items) - 1, limit).round().astype(int)
    return [items[i] for i in idx]


def _new_camera(twin, name, position, heading):
    """A proposed camera. Indoors it is a wide camera on a fine grid; outdoors, the standard 90 degree one."""
    if twin.mode == "indoor":
        return make_new_camera(name, position, heading, indoor.NEW_FOV, indoor.NEW_RANGE, grid_m=twin.grid_m)
    return make_new_camera(name, position, heading)


def _coverage(twin, cameras) -> list:
    """Chance (0-1) that the working cameras see someone, for every grid cell."""
    if twin.mode == "indoor":
        return np.round(indoor.coverage_prob(twin, cameras), 2).tolist()
    grids = [camera_probability_grid(twin, c.position, c.footprint, c.max_range_m)
             for c in cameras if c.online]
    combined = combine_cameras(grids) if grids else np.zeros((twin.n_rows, twin.n_cols))
    return np.round(combined, 2).tolist()


# ----------------------------------------------------------------------
# GET /api/scene
# ----------------------------------------------------------------------

@router.get("/scene")
def get_scene(
    door: str | None = Query(default=None, description="Door to trace routes to. Default: the most exposed one."),
    archetype: str = Query(default="opportunist"),
    offline: str | None = Query(default=None, description="Cameras to treat as offline, e.g. C1,C3"),
    rotate: str | None = Query(default=None, description="Preview cameras turned, e.g. C2:35,C3:-75"),
    add: str | None = Query(default=None, description="Preview new cameras as x:y:heading, e.g. 5.3:3.3:90"),
    site=Depends(get_site),
):
    twin, base_cameras = site
    chosen = _archetype(archetype)
    known = {c.name for c in base_cameras}
    offline_names = _parse_offline(offline, known)
    turns = _parse_rotate(rotate, known)
    additions = _parse_add(add, indoor.MAX_ADDITIONS if twin.mode == "indoor" else MAX_ADDITIONS)

    # NOTE: every step below builds a COPY, so the shared site is never
    # changed and one request can't leak into the next.
    cameras = list(base_cameras)
    for name, degrees in turns:
        cameras = with_rotation(cameras, name, degrees)
    proposed = [_new_camera(twin, f"N{i + 1}", (x, y), h) for i, (x, y, h) in enumerate(additions)]
    for c in proposed:
        # NOTE: check the real shapes, not just the grid. cell_of() clamps a
        # far-away point to the nearest edge cell, which would let a camera
        # "off the property" slip through.
        spot = Point(c.position)
        r, col = twin.cell_of(*c.position)
        if not twin.boundary.contains(spot) or any(o.contains(spot) for o in twin.obstacles) or twin.blocked[r, col]:
            raise HTTPException(status_code=400, detail=f"A camera cannot go at {c.position}: that spot is {'a wall or furniture, or off the floor' if twin.mode == 'indoor' else 'inside the house or off the property'}.")
    cameras = cameras + proposed
    normal = all_online(cameras)
    current = cameras
    for name in offline_names:
        current = with_online(current, name, False)

    # Held-out attackers (seed 99): the same ones the recommended fix is scored on.
    _, held_out = two_seed_sets(twin, 200)
    results = compare_states(twin, normal, current, held_out, chosen.risk_weight)
    by_door = {r["door"]: r for r in results}

    # The site's own cameras, as installed (nothing turned, nothing added, all online).
    # Lets the page say "was 60, now 45" while someone turns a camera.
    original = {d: success_rate(twin, all_online(list(base_cameras)), p, held_out, chosen.risk_weight)
                for d, p in ((e["id"], tuple(e["point"])) for e in twin.entrances)}

    doors_by_id = {e["id"]: tuple(e["point"]) for e in twin.entrances}
    if door is None:
        door = max(by_door, key=lambda d: (by_door[d]["after"], by_door[d]["before"]))
    if door not in doors_by_id:
        raise HTTPException(status_code=404, detail=f"Unknown door '{door}'")

    # Example routes to the chosen door, under the cameras as they are now.
    risk = build_risk(twin, current)
    audit = audit_door(twin, doors_by_id[door], risk, held_out, chosen.risk_weight)
    breached, caught = [], []
    for res in audit["results"]:
        if not res["reached"]:
            continue
        (breached if np.exp(-res["risk"]) >= 0.5 else caught).append(res["route"])

    new_names = {c.name for c in proposed}
    cam_json = []
    for c in current:
        # NOTE: heading and field of view travel with each camera (turning one updates its heading),
        # so the map draws exactly what the numbers were worked out from.
        cam_json.append({"name": c.name, "position": [_r2(c.position[0]), _r2(c.position[1])],
                         "heading_deg": _r2((c.heading_deg or 0.0) % 360), "fov_deg": c.fov_deg or 90.0,
                         "range_m": c.max_range_m, "online": c.online, "proposed": c.name in new_names,
                         "calibrated": c.view_polygon is not None,
                         # the ground a calibrated camera really sees, turned with the camera if it is turned
                         "view_polygon": _view_polygon(c)})

    floor = None
    if twin.mode == "indoor":
        prob = indoor.coverage_prob(twin, current)
        now_rep = indoor.floor_report(twin, current, prob=prob)
        normal_rep = indoor.floor_report(twin, normal)
        orig_rep = indoor.floor_report(twin, all_online(list(base_cameras)))
        floor = {**indoor.public(now_rep), "blind_runs": indoor.runs(now_rep["blind_mask"]),
                 "watched_normal": normal_rep["watched_frac"], "watched_original": orig_rep["watched_frac"]}
        summary = indoor.describe_state(current, now_rep, normal_rep)
    else:
        summary = describe_state(current, results)

    return {
        "door": door,
        "archetype": chosen.name,
        "starts_tested": len(held_out),
        "site": {
            "mode": twin.mode, "name": twin.name,
            "grid_m": twin.grid_m, "n_rows": twin.n_rows, "n_cols": twin.n_cols,
            "origin": [_r2(twin.origin[0]), _r2(twin.origin[1])],
            "boundary": [[_r2(x), _r2(y)] for x, y in twin.boundary.exterior.coords],
            "obstacles": [[[_r2(x), _r2(y)] for x, y in ob.exterior.coords] for ob in twin.obstacles],
            "entrances": [{"id": e["id"], "point": [_r2(e["point"][0]), _r2(e["point"][1])]}
                          for e in twin.entrances],
            "entries": [{"id": e["id"], "point": [_r2(e["point"][0]), _r2(e["point"][1])]}
                        for e in twin.entries],
            "cameras": cam_json,
        },
        "floor": floor,
        "coverage": _coverage(twin, current),
        "routes": {
            "breached": [_route_points(twin, r) for r in _spread(breached, MAX_BREACH_ROUTES)],
            "caught": [_route_points(twin, r) for r in _spread(caught, MAX_CAUGHT_ROUTES)],
            "n_breached": len(breached),
            "n_caught": len(caught),
        },
        "doors": [{"door": r["door"], "point": list(doors_by_id[r["door"]]),
                   "rate": r["after"], "rate_normal": r["before"], "rate_original": original[r["door"]],
                   "change": r["change"]}
                  for r in results],
        "summary": summary,
    }


# ----------------------------------------------------------------------
# GET /api/outage
# ----------------------------------------------------------------------

@router.get("/outage")
def get_outage(archetype: str = Query(default="opportunist"), site=Depends(get_site)):
    """For each camera: what happens at each door if only THAT camera fails."""
    twin, cameras = site
    chosen = _archetype(archetype)
    _, held_out = two_seed_sets(twin, 200)

    rows = []
    normal = all_online(cameras)
    floor_rows = {r["camera"]: r for r in indoor.outage_table(twin, cameras)} if twin.mode == "indoor" else {}
    for cam in cameras:
        effects = compare_states(twin, normal, with_online(normal, cam.name, False),
                                 held_out, chosen.risk_weight)
        row = {"camera": cam.name, "effects": effects, "sentence": describe_change(cam.name, effects)}
        if cam.name in floor_rows:
            row["floor"] = floor_rows[cam.name]
            row["sentence"] = indoor.describe_outage(cam.name, floor_rows[cam.name])
        rows.append(row)

    if twin.mode == "indoor":
        # indoors a camera is a single point of failure when losing it blinds a real share of the floor
        weak = [{"camera": r["camera"], "watched_before": r["floor"]["watched_before"],
                 "watched_after": r["floor"]["watched_after"], "lost_m2": r["floor"]["lost_m2"]}
                for r in rows if "floor" in r and r["floor"]["watched_before"] - r["floor"]["watched_after"] >= 0.10]
        weak.sort(key=lambda w: w["watched_after"] - w["watched_before"])
    else:
        weak = single_points_of_failure(twin, normal, held_out, chosen.risk_weight)
    return {"mode": twin.mode, "archetype": chosen.name, "starts_tested": len(held_out),
            "cameras": rows, "single_points_of_failure": weak}
