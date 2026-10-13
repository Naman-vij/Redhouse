"""
plan.py (route)

    GET /api/plan?archetype=opportunist

Answers two questions, in order:

  1. best_existing  "What is the best I can do with the cameras I already
                    have?"  Turn them to the angles that leave the fewest
                    intruders getting in, across ALL doors.
  2. more_cameras   "Do I need more cameras?"  If a door is still above the
                    target after step 1, say where to add cameras and what
                    that achieves.

The page previews either answer on the map by sending the plan's
`rotate_param` / `add_param` back to GET /api/scene.

HONESTY: the search uses the training attackers (seed 42). Every figure in
the response is measured on the held-out attackers (seed 99), which the
search never saw. All sentences are built from those numbers; no AI text.

SPEED: the search tries a few thousand camera set-ups, which takes ~10
seconds, so the answer is remembered per attacker type.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from app.routes.ring_status import get_site

from attackers import ARCHETYPES, two_seed_sets
from defender import success_rate
from outage import door_points
import indoor
from planner import (MAX_ADDITIONS, TARGET, apply_angles, best_rotations,
                     make_new_camera, plan_additions)

router = APIRouter(prefix="/api", tags=["plan"])

COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]


def compass(degrees: float) -> str:
    """90 -> 'north'.  0 = east, 90 = north (the map's own convention)."""
    return COMPASS[int(round((degrees % 360) / 45)) % 8]


def _pts(x: float) -> int:
    """0.384 -> 38. Rounded once, so every sentence and table agrees."""
    return round(x * 100)


def _doorname(d: str) -> str:
    return d.replace("_", " ")


def _rates(twin, cameras, held_out, risk_weight) -> dict:
    return {d: success_rate(twin, cameras, p, held_out, risk_weight)
            for d, p in door_points(twin).items()}


def _door_rows(before: dict, after: dict) -> list:
    return [{"door": d, "before": before[d], "after": after[d]} for d in before]


def _where(twin, position) -> str:
    """'south-west of the house' - which side of the building a spot is on."""
    import math
    if not twin.obstacles:
        return "on the property"
    cx, cy = twin.obstacles[0].centroid.x, twin.obstacles[0].centroid.y
    ang = math.degrees(math.atan2(position[1] - cy, position[0] - cx))
    return f"{compass(ang)} of the house"


def _turn_text(change: dict) -> str:
    return f"{change['camera']} {change['degrees']}° {change['direction']}"


def _join(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _door_list(rows: list) -> str:
    return "; ".join(f"{_doorname(r['door'])} {_pts(r['before'])} → {_pts(r['after'])}" for r in rows
                     if _pts(r["before"]) != _pts(r["after"])) or "no door changes"


def _compute(archetype_name: str, twin, cameras) -> dict:
    chosen = next(a for a in ARCHETYPES if a.name == archetype_name)
    rw = chosen.risk_weight
    training, held_out = two_seed_sets(twin, 200)

    # --- measured on the held-out attackers -----------------------------------
    now = _rates(twin, cameras, held_out, rw)

    # --- 1. best use of the cameras you have ----------------------------------
    rot = best_rotations(twin, cameras, training, rw)
    turned = apply_angles(cameras, rot["angles"])
    after_turn = _rates(twin, turned, held_out, rw)
    improved = sum(after_turn.values()) < sum(now.values()) - 1e-9

    changes = []
    if improved:
        for name, a in rot["angles"].items():
            changes.append({"camera": name, "degrees": abs(int(a)),
                            "direction": "counter-clockwise" if a > 0 else "clockwise",
                            "angle": int(a)})
    else:
        after_turn = now                                       # a plan that doesn't help on held-out is no plan

    if not changes:
        best_sentence = ("The cameras you have are already pointed about as well as they can be. "
                         "Turning them does not reduce how many intruders get in.")
    else:
        best_sentence = (f"Turn {_join([_turn_text(c) for c in changes])}. "
                         f"Intruders who get in, out of 100: {_door_list(_door_rows(now, after_turn))}.")

    best_existing = {
        "changes": changes,
        "rotate_param": ",".join(f"{c['camera']}:{c['angle']}" for c in changes),
        "doors": _door_rows(now, after_turn),
        "sentence": best_sentence,
    }

    # --- 2. do you need more cameras? ------------------------------------------
    base_for_more = apply_angles(cameras, {c["camera"]: c["angle"] for c in changes})
    needed = max(after_turn.values()) > TARGET
    additions, after_add = [], after_turn

    if needed:
        found = plan_additions(twin, base_for_more, training, rw)
        proposed = [make_new_camera(a["name"], a["position"], a["heading_deg"]) for a in found["added"]]
        after_add = _rates(twin, base_for_more + proposed, held_out, rw)
        for a in found["added"]:
            additions.append({**a, "position": list(a["position"]),
                              "compass": compass(a["heading_deg"]),
                              "where": _where(twin, a["position"])})

    target_pts = _pts(TARGET)
    reached = max(after_add.values()) <= TARGET
    if not needed:
        more_sentence = (f"No. After turning the cameras you have, every door lets in {target_pts} or "
                         f"fewer of 100 intruders.")
    elif not additions:
        more_sentence = ("Adding a camera does not help much here. Look at lighting, fencing or "
                         "locks on the exposed door instead.")
    else:
        spots = [f"{a['name']} on the {a['where'].replace(' of the house', ' side of the house')}, "
                 f"facing {a['compass']}" for a in additions]
        n = len(additions)
        lead = f"Yes. Add {n} {'camera' if n == 1 else 'cameras'}: {_join(spots)}."
        tail = (f" Every door then lets in {target_pts} or fewer of 100."
                if reached else
                f" Even then the worst door still lets in {_pts(max(after_add.values()))} of 100, "
                f"so look at lighting, fencing or locks as well.")
        more_sentence = f"{lead} Intruders who get in: {_door_list(_door_rows(after_turn, after_add))}.{tail}"

    more_cameras = {
        "needed": needed,
        "reached_target": reached,
        "additions": additions,
        "add_param": ",".join(f"{a['position'][0]}:{a['position'][1]}:{int(a['heading_deg'])}" for a in additions),
        "doors": _door_rows(after_turn, after_add),
        "sentence": more_sentence,
    }

    return {
        "archetype": chosen.name,
        "starts_tested": len(held_out),
        "target": TARGET,
        "now": [{"door": d, "rate": r} for d, r in now.items()],
        "best_existing": best_existing,
        "more_cameras": more_cameras,
        "final": [{"door": d, "rate": r} for d, r in after_add.items()],
    }


_CACHE: dict = {}


def _fingerprint(twin, cameras) -> tuple:
    """Changes whenever the site or any camera changes, so a cached plan can never go stale."""
    return (twin.mode, twin.boundary.wkt, tuple(o.wkt for o in twin.obstacles),
            tuple((e["id"], tuple(e["point"])) for e in twin.entrances),
            tuple((e["id"], tuple(e["point"])) for e in twin.entries),
            tuple((c.name, tuple(c.position), c.online, c.heading_deg, round(float(c.footprint.sum()), 3))
                  for c in cameras))


@router.get("/plan")
def get_plan(archetype: str = Query(default="opportunist"), site=Depends(get_site)):
    if not any(a.name == archetype for a in ARCHETYPES):
        raise HTTPException(status_code=404, detail=f"Unknown archetype '{archetype}'")
    twin, cameras = site
    key = (archetype, _fingerprint(twin, cameras))
    if key not in _CACHE:                       # ~10 s the first time, instant after
        if twin.mode == "indoor":
            chosen = next(a for a in ARCHETYPES if a.name == archetype)
            _CACHE[key] = {"archetype": archetype, **indoor.make_plan(twin, cameras, chosen.risk_weight)}
        else:
            _CACHE[key] = _compute(archetype, twin, cameras)
    if len(_CACHE) > 64:
        _CACHE.pop(next(iter(_CACHE)))
    return _CACHE[key]
