"""
planner.py

Two questions a homeowner actually asks:

  1. "What is the BEST I can do with the cameras I already have?"
     -> best_rotations(): turn each camera to the angle that leaves the
        fewest intruders getting in, across ALL doors together.

  2. "Do I need MORE cameras, and where?"
     -> plan_additions(): keep adding the single camera (place + direction)
        that helps most, until every door is near zero or more cameras stop
        helping.

HOW IT STAYS HONEST (same rule as defender.py)
  The search only ever looks at the TRAINING attackers (seed 42). The
  numbers we report as the result are measured on the HELD-OUT attackers
  (seed 99) that the search never saw. If a plan only works on the training
  set, the held-out number shows it.

Nothing here is random and nothing here is an AI guess. Same site, same
answer, every time.

NOTE: mount points are limited to places a camera can really go: just
outside the house walls (eaves / wall mounts) and the property's corners
(fence posts). We never suggest a camera floating in the middle of a lawn.
"""

from dataclasses import replace

import numpy as np

from twin import Twin
from cameras import Camera, make_wedge_footprint, points_needed
from coverage import camera_probability_grid, combine_cameras, risk_grid
from defender import rotate_footprint
from metrics import audit_door
from outage import door_points

# "Near zero": at most this share of intruders may get in at ANY door.
TARGET = 0.05

# The most cameras we will ever suggest adding. More than this is a sign
# the property needs a different kind of fix (lighting, fencing, a person).
MAX_ADDITIONS = 3

# A new camera is only worth recommending if it takes at least this many
# points (as a share of total door exposure) off. 0.02 = 2 percentage points.
MIN_GAIN = 0.02

# New cameras match the demo cameras: 90 degree view, 9 m range.
NEW_FOV = 90.0
NEW_RANGE = 9.0
NEW_SEED = 100          # fixes the footprint pattern, so a camera is rebuilt identically anywhere


def make_new_camera(name: str, position, heading_deg: float,
                    fov_deg: float = NEW_FOV, range_m: float = NEW_RANGE, grid_m=None) -> Camera:
    """
    Build a proposed camera. The API uses this too, so a camera described
    as (x, y, heading) is the SAME camera in the planner and on the map.
    """
    pos = (round(float(position[0]), 2), round(float(position[1]), 2))
    # NOTE: indoors the grid is fine, so a wedge needs many more points to cover every cell.
    n = points_needed(fov_deg, range_m, grid_m) if grid_m else 3000
    fp = make_wedge_footprint(pos, heading_deg, fov_deg, range_m, n_points=n, seed=NEW_SEED)
    return Camera(name, pos, fp, range_m, heading_deg=float(heading_deg) % 360, fov_deg=float(fov_deg))


# ----------------------------------------------------------------------
# Scoring a camera set
# ----------------------------------------------------------------------

class _Scorer:
    """
    Scores camera sets: attacker success at every door.

    NOTE: the slow part is working out what one camera sees (line of sight
    over the grid). A camera turned to one angle, or placed at one spot,
    always sees the same thing, so each is worked out ONCE and remembered.
    """

    def __init__(self, twin: Twin, doors: dict, starts: list, risk_weight: float):
        self.twin, self.doors, self.starts, self.rw = twin, doors, starts, risk_weight
        self._grids = {}

    def grid(self, key, cam: Camera) -> np.ndarray:
        if key not in self._grids:
            self._grids[key] = camera_probability_grid(
                self.twin, cam.position, cam.footprint, cam.max_range_m)
        return self._grids[key]

    def rates(self, grids: list) -> dict:
        t = self.twin
        if grids:
            risk = risk_grid(combine_cameras(grids))
        else:
            risk = np.zeros((t.n_rows, t.n_cols))
        return {d: audit_door(t, p, risk, self.starts, self.rw)["success_rate"]
                for d, p in self.doors.items()}


def _total(rates: dict) -> float:
    """Total exposure = sum of the door rates. Lower is better."""
    return sum(rates.values())


# ----------------------------------------------------------------------
# 1. Best use of the cameras you already have
# ----------------------------------------------------------------------

def best_rotations(twin, cameras, training, risk_weight, step=15, passes=2) -> dict:
    """
    Find the angle for each camera that leaves the fewest intruders in.

    Method: take the cameras one at a time and try every angle (a full
    circle in `step` degree jumps), keeping the best. Go round twice,
    because moving one camera can change what is best for another.

    Returns {camera_name: degrees}, positive = counter-clockwise, and the
    training rates it reached. Cameras that should stay put are omitted.
    """
    doors = door_points(twin)
    scorer = _Scorer(twin, doors, training, risk_weight)
    live = [c for c in cameras if c.online]
    angles = {c.name: 0 for c in live}

    def grids_for(chosen: dict) -> list:
        out = []
        for c in live:
            a = chosen[c.name]
            rotated = replace(c, footprint=rotate_footprint(c.footprint, c.position, a)) if a else c
            out.append(scorer.grid((c.name, a), rotated))
        return out

    best_rates = scorer.rates(grids_for(angles))
    best_total = _total(best_rates)

    for _ in range(passes):
        changed = False
        for cam in live:
            keep = angles[cam.name]
            for a in range(-180 + step, 180 + 1, step):
                if a == keep:
                    continue
                trial = {**angles, cam.name: a}
                rates = scorer.rates(grids_for(trial))
                # strictly better by a real margin, so ties never cause churn
                if _total(rates) < best_total - 1e-9:
                    best_total, best_rates, angles = _total(rates), rates, trial
                    changed = True
        if not changed:
            break

    return {"angles": {n: a for n, a in angles.items() if a != 0}, "training_rates": best_rates}


def apply_angles(cameras: list, angles: dict) -> list:
    """A COPY of the cameras with each named camera turned by its angle."""
    out = []
    for c in cameras:
        a = angles.get(c.name, 0)
        if a:
            # NOTE: heading_deg moves with the footprint, so a turned camera still draws and plans correctly
            head = None if c.heading_deg is None else (c.heading_deg + a) % 360
            out.append(replace(c, footprint=rotate_footprint(c.footprint, c.position, a), heading_deg=head))
        else:
            out.append(c)
    return out


# ----------------------------------------------------------------------
# 2. Where a new camera could go
# ----------------------------------------------------------------------

def candidate_mounts(twin: Twin, existing: list, spacing_m=2.5, offset_m=0.7) -> list:
    """
    Places a camera could realistically be fixed: along the outside of the
    house walls, and at the property's corners. Returns [(x, y), ...].
    """
    pts = []
    for ob in twin.obstacles:
        ring = ob.buffer(offset_m, join_style=2).exterior        # mitred, so corners stay sharp
        n = max(4, int(ring.length // spacing_m))
        for i in range(n):
            p = ring.interpolate(ring.length * i / n)
            pts.append((p.x, p.y))
    inset = twin.boundary.buffer(-offset_m, join_style=2)
    if not inset.is_empty:
        for x, y in list(inset.exterior.coords)[:-1]:
            pts.append((x, y))

    good = []
    for x, y in pts:
        x, y = round(x, 2), round(y, 2)
        r, c = twin.cell_of(x, y)
        if twin.blocked[r, c]:
            continue
        if any(np.hypot(x - e.position[0], y - e.position[1]) < 1.0 for e in existing):
            continue                                              # no point stacking on top of one
        if (x, y) not in good:
            good.append((x, y))
    return good


def plan_additions(twin, cameras, training, risk_weight, headings=range(0, 360, 45),
                   max_additions=MAX_ADDITIONS, target=TARGET) -> dict:
    """
    Add cameras one at a time, each time choosing the single best
    (position, direction) from every candidate. Stop when every door is at
    or under `target`, or the next camera would barely help.

    Returns the cameras added (in order) and the training rates after each.
    """
    doors = door_points(twin)
    scorer = _Scorer(twin, doors, training, risk_weight)
    live = [c for c in cameras if c.online]
    grids = [scorer.grid(("existing", c.name, tuple(c.footprint[0])), c) for c in live]

    rates = scorer.rates(grids)
    added, history = [], [{"added": None, "rates": rates}]
    mounts = candidate_mounts(twin, live)

    while len(added) < max_additions and max(rates.values()) > target:
        best = None
        for pos in mounts:
            for h in headings:
                cam = make_new_camera("N", pos, h)
                g = scorer.grid(("new", pos, h), cam)
                trial = scorer.rates(grids + [g])
                score = (_total(trial), max(trial.values()))
                if best is None or score < best["score"]:
                    best = {"score": score, "rates": trial, "pos": pos, "heading": h, "grid": g}
        if best is None or _total(rates) - best["score"][0] < MIN_GAIN:
            break                                                  # more cameras stop helping
        name = f"N{len(added) + 1}"
        added.append({"name": name, "position": best["pos"], "heading_deg": float(best["heading"]),
                      "fov_deg": NEW_FOV, "range_m": NEW_RANGE})
        grids.append(best["grid"])
        rates = best["rates"]
        history.append({"added": name, "rates": rates})

    return {"added": added, "history": history, "training_rates": rates}
