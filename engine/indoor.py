"""
indoor.py

The indoor side of RedHouse: a floor plan, some cameras, and one question -
"is every part of this floor watched, and if not, what is the smallest fix?"

WHAT "WATCHED" MEANS
  A spot on the floor is WATCHED when the cameras together have at least a
  25% chance of seeing someone standing there (same camera maths as outdoors:
  linear fall-off with distance, walls block the view, cameras combine as
  1 - product of misses). Anything under that is a BLIND SPOT.
  25% sounds low, but a person crossing a few watched cells is seen almost
  surely: three cells at 25% is already a 58% chance, and the attacker model
  (see the asset figures) uses the same numbers.

HOW THE SEARCH WORKS (no AI, no randomness)
  1. best_rotations():  turn each camera to the heading that watches the most
                        floor. Nothing is bought or moved.
  2. plan_additions():  if blind spots remain, add the camera (a spot on a wall
                        and a direction) that watches the most blind floor,
                        and repeat until 95% is watched or a camera stops
                        helping.
  Both search with a fast geometric model of a camera (see CamModel). Every
  figure we REPORT is then measured again with the real Camera objects, the
  same ones the map draws, so the numbers on screen always agree.

WHAT THE ATTACKER NUMBERS ARE
  asset_rates() sends 200 held-out intruders (fixed seed 99, never used while
  searching) in through the entrances and reports how many reach each
  protected spot unseen. That is the same test as outdoors.
"""

import hashlib
import math
from dataclasses import replace

import cv2
import numpy as np
from shapely import prepared
from shapely.geometry import Point

from attackers import entry_start_points, two_seed_sets
from cameras import points_needed
from coverage import camera_probability_grid, combine_cameras
from defender import success_rate
from outage import door_points
from planner import apply_angles, make_new_camera

WATCHED_P = 0.25          # chance of being seen at which a spot counts as watched
GOAL = 0.95               # share of open floor we try to watch (the rest is usually behind furniture)
NEW_FOV = 110.0           # a wide indoor camera
NEW_RANGE = 12.0
MAX_ADDITIONS = 8         # more than this means the layout needs a different answer
MIN_GAIN = 0.005          # a new camera must watch at least 0.5% more of the floor
MIN_BLIND_SPOT_M2 = 0.5   # smaller gaps are counted but not listed
P_MAX = 0.9               # same ceiling as coverage.py
MOUNT_SPACING_M = 1.0     # candidate camera spots are about this far apart
COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]


# ----------------------------------------------------------------------
# The floor: which cells count, and what a camera can see from a spot
# ----------------------------------------------------------------------

def open_floor(twin) -> np.ndarray:
    """
    Cells an intruder could stand on: free floor that is joined to an
    entrance. A sealed cupboard or a gap behind a wall is not part of it.
    """
    free = (~twin.blocked).astype(np.uint8)
    _, labels = cv2.connectedComponents(free, connectivity=4)
    keep = set()
    for x, y in entry_start_points(twin, 60, seed=1):
        r, c = twin.cell_of(x, y)
        if labels[r, c] > 0:
            keep.add(int(labels[r, c]))
    return np.isin(labels, list(keep)) if keep else np.zeros_like(twin.blocked)


def cell_centres(twin):
    """x and y of every cell centre, as two (rows, cols) arrays."""
    g = twin.grid_m
    cols, rows = np.meshgrid(np.arange(twin.n_cols), np.arange(twin.n_rows))
    return twin.origin[0] + (cols + 0.5) * g, twin.origin[1] + (rows + 0.5) * g


def visible_from(twin, position, range_m) -> np.ndarray:
    """
    Every free cell within `range_m` that a camera at `position` has a clear
    line to. Same rule as coverage.line_of_sight_clear(strict=True), done for
    all cells at once so a search can afford thousands of camera positions.
    Returns an (rows, cols) array: distance in metres, or inf if not visible.
    """
    px, py = position
    cam_r, cam_c = twin.cell_of(px, py)
    cx, cy = cell_centres(twin)
    dist = np.hypot(cx - px, cy - py)
    rows, cols = np.nonzero((~twin.blocked) & (dist < range_m))
    dr, dc = rows - cam_r, cols - cam_c
    steps = np.maximum(np.abs(dr), np.abs(dc))
    safe = np.where(steps > 0, steps, 1)
    ok = np.ones(len(rows), dtype=bool)
    prev_r = prev_c = None
    for i in range(1, int(steps.max()) + 1 if len(steps) else 1):
        active = steps >= i
        t = i / safe
        r = np.clip(np.round(cam_r + dr * t).astype(int), 0, twin.n_rows - 1)
        c = np.clip(np.round(cam_c + dc * t).astype(int), 0, twin.n_cols - 1)
        ok &= ~(twin.blocked[r, c] & active)
        if prev_r is not None:                     # no squeezing diagonally between two wall corners
            pinch = (active & (r != prev_r) & (c != prev_c)
                     & twin.blocked[prev_r, c] & twin.blocked[r, prev_c])
            ok &= ~pinch
        prev_r, prev_c = r, c
    out = np.full((twin.n_rows, twin.n_cols), np.inf)
    out[rows[ok], cols[ok]] = dist[rows[ok], cols[ok]]
    return out


class CamModel:
    """
    A camera at a fixed spot, which can be pointed any way. The slow part
    (what can be SEEN from this spot) is worked out once; pointing it a
    different way is then just a mask.
    """
    def __init__(self, twin, position, fov_deg, range_m):
        self.position, self.fov, self.range = position, float(fov_deg), float(range_m)
        self.dist = visible_from(twin, position, range_m)
        cx, cy = cell_centres(twin)
        self.angle = np.degrees(np.arctan2(cy - position[1], cx - position[0]))
        self._cache = {}

    def grid(self, heading_deg) -> np.ndarray:
        key = round(float(heading_deg) % 360, 3)
        if key not in self._cache:
            off = (self.angle - heading_deg + 180) % 360 - 180
            seen = np.isfinite(self.dist) & (np.abs(off) <= self.fov / 2 + 1e-9)
            p = np.zeros_like(self.dist)
            p[seen] = P_MAX * (1 - self.dist[seen] / self.range)
            self._cache[key] = p
        return self._cache[key]


def _combine(grids, shape):
    miss = np.ones(shape)
    for g in grids:
        miss *= 1 - g
    return 1 - miss


# ----------------------------------------------------------------------
# Reporting: how much floor is watched, and where the gaps are
# ----------------------------------------------------------------------

def _near_asset(twin, x, y, within_m=3.0):
    best = None
    for e in twin.entrances:
        d = math.hypot(e["point"][0] - x, e["point"][1] - y)
        if d <= within_m and (best is None or d < best[1]):
            best = (e["id"], d)
    return best[0] if best else None


def coverage_prob(twin, cameras) -> np.ndarray:
    """
    Chance of being seen on every cell, from the REAL cameras (the ones the map draws).

    NOTE: one camera's grid is slow to work out and never changes for the same camera, so each is
    remembered on the twin itself (it goes when the twin goes, so it can never be stale).
    """
    live = [c for c in cameras if c.online]
    if not live:
        return np.zeros((twin.n_rows, twin.n_cols))
    cache = twin.__dict__.setdefault("_camera_grids", {})
    if len(cache) > 300:
        cache.clear()
    grids = []
    for c in live:
        key = (c.name, tuple(c.position), float(c.max_range_m), hashlib.blake2b(c.footprint.tobytes(), digest_size=8).digest())
        if key not in cache:
            cache[key] = camera_probability_grid(twin, c.position, c.footprint, c.max_range_m)
        grids.append(cache[key])
    return combine_cameras(grids)


def runs(mask) -> list:
    """A True/False grid as [[row, first_col, last_col_plus_one], ...]: small to send, easy to draw."""
    out = []
    for r in range(mask.shape[0]):
        row = mask[r]
        if not row.any():
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        out += [[r, int(a), int(b)] for a, b in zip(edges[::2], edges[1::2])]
    return out


def describe_state(cameras, now_rep, normal_rep) -> str:
    """One sentence about the cameras' current state, built only from the report numbers."""
    offline = [c.name for c in cameras if not c.online]
    b, a = _pct(normal_rep["watched_frac"]), _pct(now_rep["watched_frac"])
    if not offline:
        return f"All {len(cameras)} cameras are online. {a}% of the open floor is watched."
    who = f"Camera {offline[0]} is offline" if len(offline) == 1 else f"Cameras {', '.join(offline)} are offline"
    lost = [x["id"].replace("_", " ") for x, y in zip(normal_rep["assets"], now_rep["assets"])
            if x["watched"] and not y["watched"]]
    if a == b:
        return f"{who}. No floor stops being watched."
    tail = f" Not watched any more: {_join(lost)}." if lost else ""
    return f"{who}: watched floor falls from {b}% to {a}% ({a - b} points).{tail}"


def describe_outage(camera, row) -> str:
    """The same, for the 'what if this one camera failed' table."""
    b, a = _pct(row["watched_before"]), _pct(row["watched_after"])
    if a == b:
        return f"Camera {camera} went offline. No floor stops being watched."
    lost = [x.replace("_", " ") for x in row["assets_exposed"]]
    tail = f" Not watched any more: {_join(lost)}." if lost else ""
    return (f"Camera {camera} went offline: watched floor falls from {b}% to {a}% ({a - b} points), "
            f"{row['lost_m2']:g} m² goes blind.{tail}")


def floor_report(twin, cameras, prob=None) -> dict:
    """
    The facts about a camera set on this floor. All plain numbers; the
    sentences are built from these elsewhere.
    """
    prob = coverage_prob(twin, cameras) if prob is None else prob
    floor = open_floor(twin)
    g2 = twin.grid_m ** 2
    watched = floor & (prob >= WATCHED_P)
    blind = floor & ~watched
    n_floor = int(floor.sum())

    spots = []
    n, labels, stats, cents = cv2.connectedComponentsWithStats(blind.astype(np.uint8), connectivity=8)
    order = sorted(range(1, n), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    for i in order:
        area = stats[i, cv2.CC_STAT_AREA] * g2
        if area < MIN_BLIND_SPOT_M2:
            continue
        rr, cc = np.nonzero(labels == i)
        k = int(np.argmin((rr - cents[i][1]) ** 2 + (cc - cents[i][0]) ** 2))     # a real blind cell near the middle
        x, y = twin._cell_center(rr[k], cc[k])
        spots.append({"id": f"B{len(spots) + 1}", "area_m2": round(float(area), 1),
                      "point": [round(float(x), 2), round(float(y), 2)],
                      "near": _near_asset(twin, x, y)})
        if len(spots) == 6:
            break

    def at(point):
        cell = twin.free_cell_near(point[0], point[1], 1.5)       # a doorway sits in the wall, so look just inside it
        return float(prob[cell]) if cell else 0.0

    return {
        "open_floor_m2": round(n_floor * g2, 1),
        "watched_frac": float(watched.sum() / n_floor) if n_floor else 0.0,
        "blind_m2": round(float(blind.sum() * g2), 1),
        "blind_spots": spots,
        "assets": [{"id": e["id"], "chance_seen": round(at(e["point"]), 2),
                    "watched": at(e["point"]) >= WATCHED_P} for e in twin.entrances],
        "entries": [{"id": e["id"], "chance_seen": round(at(e["point"]), 2),
                     "watched": at(e["point"]) >= WATCHED_P} for e in twin.entries],
        "blind_mask": blind, "watched_mask": watched, "prob": prob,
    }


def public(report: dict) -> dict:
    """The report without its numpy arrays, safe to send as JSON."""
    return {k: v for k, v in report.items() if k not in ("blind_mask", "watched_mask", "prob")}


def asset_rates(twin, cameras, held_out, risk_weight) -> dict:
    """Share of 200 held-out intruders who reach each protected spot unseen."""
    return {d: success_rate(twin, cameras, p, held_out, risk_weight) for d, p in door_points(twin).items()}


def outage_table(twin, cameras) -> list:
    """What each camera is worth: floor watched if it goes offline, and what would turn blind."""
    base = floor_report(twin, cameras)
    out = []
    for cam in cameras:
        if not cam.online:
            continue
        rep = floor_report(twin, [replace(c, online=False) if c.name == cam.name else c for c in cameras])
        lost = base["watched_mask"] & ~rep["watched_mask"]
        out.append({"camera": cam.name, "watched_before": base["watched_frac"],
                    "watched_after": rep["watched_frac"],
                    "lost_m2": round(float(lost.sum() * twin.grid_m ** 2), 1),
                    "assets_exposed": [a["id"] for a, b in zip(base["assets"], rep["assets"])
                                       if a["watched"] and not b["watched"]]})
    return out


# ----------------------------------------------------------------------
# The search
# ----------------------------------------------------------------------

def _models_for(twin, cameras):
    """A CamModel for each online camera that knows where it points."""
    out = []
    for c in cameras:
        if not c.online:
            continue
        if c.heading_deg is None or c.fov_deg is None:
            raise ValueError(f"Camera {c.name} has no heading or field of view, so it cannot be planned.")
        out.append((c, CamModel(twin, c.position, c.fov_deg, c.max_range_m)))
    return out


def priority_weights(twin, floor) -> np.ndarray:
    """1 for every floor cell, and 4 within 1.5 m of an entrance or protected spot: those matter most."""
    w = floor.astype(float)
    cx, cy = cell_centres(twin)
    for e in list(twin.entries) + list(twin.entrances):
        w[np.hypot(cx - e["point"][0], cy - e["point"][1]) <= 1.5] *= 4
    return w


def _score(prob, floor, weights=None):
    """Higher is better: (weighted) floor watched, then, as a tie-break, how well it is watched."""
    w = floor.astype(float) if weights is None else weights
    return (float(w[prob >= WATCHED_P].sum()), float(prob[floor].sum()))


def best_rotations(twin, cameras, step=15, passes=2) -> dict:
    """
    Turn each camera to the heading that watches the most floor. Cameras are
    taken one at a time, every heading in `step` degree jumps; two rounds
    because turning one can change what suits another.
    Returns {"angles": {name: degrees counter-clockwise}, "watched_frac": float}.
    """
    floor = open_floor(twin)
    wts = priority_weights(twin, floor)
    pairs = _models_for(twin, cameras)
    shape = (twin.n_rows, twin.n_cols)
    delta = {c.name: 0 for c, _ in pairs}

    def prob_for(d):
        return _combine([m.grid(c.heading_deg + d[c.name]) for c, m in pairs], shape)

    best = _score(prob_for(delta), floor, wts)
    min_gain = max(1.0, 0.001 * wts.sum())                       # ignore changes smaller than 0.1% of the floor
    for _ in range(passes):
        changed = False
        for c, _m in pairs:
            keep = delta[c.name]
            for a in range(-180 + step, 181, step):
                if a == keep:
                    continue
                s = _score(prob_for({**delta, c.name: a}), floor, wts)
                if s[0] >= best[0] + min_gain:
                    best, delta, changed = s, {**delta, c.name: a}, True
        if not changed:
            break
    p = prob_for(delta)
    return {"angles": {k: v for k, v in delta.items() if v},
            "watched_frac": float(((p >= WATCHED_P) & floor).sum() / max(1, floor.sum()))}


def candidate_mounts(twin, existing, spacing_m=MOUNT_SPACING_M) -> list:
    """
    Where a camera could really be fixed: on open floor beside a WALL, which
    means the outer wall or a thin inner wall. Not on benches or furniture.
    """
    wall_like = [o for o in twin.obstacles if _is_wall(o)]
    rim = twin.boundary.exterior
    floor = open_floor(twin)
    cx, cy = cell_centres(twin)
    near = np.zeros_like(floor)
    # a free cell is "beside a wall" when it is next to the outer wall or inside a thin inner wall's reach
    pw = [prepared.prep(w.buffer(twin.grid_m)) for w in wall_like]
    rr, cc = np.nonzero(floor)
    for r, c in zip(rr, cc):
        p = Point(cx[r, c], cy[r, c])
        if p.distance(rim) <= 0.5 + twin.grid_m or any(w.contains(p) for w in pw):
            near[r, c] = True
    pts = []
    taken = []
    for r, c in zip(*np.nonzero(near)):                          # raster order, so the result never changes
        x, y = float(cx[r, c]), float(cy[r, c])
        if any(math.hypot(x - tx, y - ty) < spacing_m for tx, ty in taken):
            continue
        if any(math.hypot(x - e.position[0], y - e.position[1]) < 1.0 for e in existing):
            continue
        taken.append((x, y))
        pts.append((round(x, 2), round(y, 2)))
    return pts


def _is_wall(poly) -> bool:
    """Long and thin (a wall) rather than a bench or a pillar."""
    rect = poly.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    a = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
    b = math.hypot(xs[2] - xs[1], ys[2] - ys[1])
    short, long_ = min(a, b), max(a, b)
    return short <= 0.45 and long_ >= 1.2


def plan_additions(twin, cameras, goal=GOAL, max_additions=MAX_ADDITIONS, headings=range(0, 360, 30)) -> dict:
    """
    Add cameras one at a time, each time the single (wall spot, direction)
    that watches the most floor that is not watched yet. Stop at `goal`, or
    when the next camera would add under MIN_GAIN of the floor.
    """
    floor = open_floor(twin)
    wts = priority_weights(twin, floor)
    n = max(1, int(floor.sum()))
    shape = (twin.n_rows, twin.n_cols)
    pairs = _models_for(twin, cameras)
    grids = [m.grid(c.heading_deg) for c, m in pairs]
    prob = _combine(grids, shape) if grids else np.zeros(shape)

    def frac(p):
        return float(((p >= WATCHED_P) & floor).sum() / n)
    watched = frac(prob)

    mounts = candidate_mounts(twin, [c for c, _ in pairs])
    models = {}
    added, history = [], [watched]
    while len(added) < max_additions and watched < goal:
        best = None
        for pos in mounts:
            if pos not in models:
                models[pos] = CamModel(twin, pos, NEW_FOV, NEW_RANGE)
            for h in headings:
                p = 1 - (1 - prob) * (1 - models[pos].grid(h))
                s = _score(p, floor, wts)
                if best is None or s > best[0]:
                    best = (s, pos, h, p)
        if best is None or frac(best[3]) - watched < MIN_GAIN:
            break
        _, pos, h, prob = best
        watched = frac(prob)
        added.append({"name": f"N{len(added) + 1}", "position": pos, "heading_deg": float(h),
                      "fov_deg": NEW_FOV, "range_m": NEW_RANGE})
        history.append(watched)

    # NOTE: a camera added early can become pointless once later ones cover the same floor. Drop any that
    # now add under MIN_GAIN, so we never recommend a camera that does nothing.
    kept = list(range(len(added)))
    for i in reversed(range(len(added))):
        others = [m.grid(c.heading_deg) for c, m in pairs] + \
                 [models[added[j]["position"]].grid(added[j]["heading_deg"]) for j in kept if j != i]
        if watched - frac(_combine(others, shape)) < MIN_GAIN:
            kept.remove(i)
            watched = frac(_combine(others, shape))
    added = [dict(added[j], name=f"N{k + 1}") for k, j in enumerate(kept)]
    return {"added": added, "history": history, "watched_frac": watched}


# ----------------------------------------------------------------------
# The whole answer, in the same shape the page already understands
# ----------------------------------------------------------------------

def compass(degrees):
    return COMPASS[int(round((degrees % 360) / 45)) % 8]


def where(twin, pos) -> str:
    """'north-east part of the floor', plus 'near the server room' when a protected spot is close."""
    minx, miny, maxx, maxy = twin.boundary.bounds
    fx, fy = (pos[0] - minx) / (maxx - minx), (pos[1] - miny) / (maxy - miny)
    words = [w for w in ("north" if fy > 2 / 3 else "south" if fy < 1 / 3 else "",
                         "east" if fx > 2 / 3 else "west" if fx < 1 / 3 else "") if w]
    text = ("-".join(words) + " part of the floor") if words else "middle of the floor"
    near = _near_asset(twin, pos[0], pos[1])
    return text + (f", near the {near.replace('_', ' ')}" if near else "")


def _pct(x):
    return round(x * 100)


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def make_plan(twin, cameras, risk_weight) -> dict:
    """
    Everything the page needs in one go:
      now          the floor as it is
      best_existing  turn the cameras you have (changes + result)
      more_cameras   add cameras (additions + result)
    Floor coverage is geometry, so it needs no held-out set; the attacker
    figures come from the held-out intruders (seed 99).
    """
    _, held_out = two_seed_sets(twin, 200)

    now_rep = floor_report(twin, cameras)
    now_rates = asset_rates(twin, cameras, held_out, risk_weight)

    # 1. turn what you have
    rot = best_rotations(twin, cameras)
    turned = apply_angles(cameras, rot["angles"])
    turned_rep = floor_report(twin, turned)
    improved = turned_rep["watched_frac"] > now_rep["watched_frac"] + 0.001
    changes = []
    if improved:
        for name, a in rot["angles"].items():
            changes.append({"camera": name, "degrees": abs(int(a)),
                            "direction": "counter-clockwise" if a > 0 else "clockwise", "angle": int(a)})
    else:
        turned, turned_rep = cameras, now_rep
    turned_rates = asset_rates(twin, turned, held_out, risk_weight) if improved else now_rates

    if changes:
        turn_sentence = (f"Turn {_join([f'{c['camera']} {c['degrees']}° {c['direction']}' for c in changes])}. "
                         f"Watched floor goes from {_pct(now_rep['watched_frac'])}% to {_pct(turned_rep['watched_frac'])}%.")
    else:
        turn_sentence = ("The cameras you have are already pointed about as well as they can be. "
                         "Turning them does not watch any more of the floor.")

    best_existing = {
        "changes": changes,
        "rotate_param": ",".join(f"{c['camera']}:{c['angle']}" for c in changes),
        "watched_before": now_rep["watched_frac"], "watched_after": turned_rep["watched_frac"],
        "doors": [{"door": d, "before": now_rates[d], "after": turned_rates[d]} for d in now_rates],
        "sentence": turn_sentence,
    }

    # 2. add cameras
    needed = turned_rep["watched_frac"] < GOAL
    additions, final_cams, final_rep, final_rates = [], turned, turned_rep, turned_rates
    if needed:
        found = plan_additions(twin, turned)
        gm = twin.grid_m
        proposed = [make_new_camera(a["name"], a["position"], a["heading_deg"], a["fov_deg"], a["range_m"], grid_m=gm) for a in found["added"]]
        if proposed:
            final_cams = turned + proposed
            final_rep = floor_report(twin, final_cams)
            final_rates = asset_rates(twin, final_cams, held_out, risk_weight)
        for a in found["added"]:
            additions.append({**a, "position": list(a["position"]), "compass": compass(a["heading_deg"]),
                              "where": where(twin, a["position"])})
    reached = final_rep["watched_frac"] >= GOAL

    if not needed:
        more_sentence = f"No. After turning the cameras you have, {_pct(turned_rep['watched_frac'])}% of the floor is watched."
    elif not additions:
        more_sentence = ("Adding a camera does not help much here. What is left is hidden behind "
                         "furniture or walls; look at the layout or at locks on the protected spots.")
    else:
        n = len(additions)
        spots = [f"{a['name']} in the {a['where']}, facing {a['compass']}" for a in additions]
        tail = (f" That watches {_pct(final_rep['watched_frac'])}% of the floor." if reached else
                f" That watches {_pct(final_rep['watched_frac'])}% of the floor. The rest is hidden behind "
                "furniture or walls, so look at the layout as well.")
        more_sentence = f"Yes. Add {n} {'camera' if n == 1 else 'cameras'}: {_join(spots)}.{tail}"

    more_cameras = {
        "needed": needed, "reached_target": reached, "additions": additions,
        "add_param": ",".join(f"{a['position'][0]}:{a['position'][1]}:{int(a['heading_deg'])}" for a in additions),
        "watched_before": turned_rep["watched_frac"], "watched_after": final_rep["watched_frac"],
        "doors": [{"door": d, "before": turned_rates[d], "after": final_rates[d]} for d in turned_rates],
        "sentence": more_sentence,
    }
    return {
        "mode": "indoor", "starts_tested": len(held_out), "goal": GOAL,
        "now": public(now_rep), "now_rates": [{"door": d, "rate": r} for d, r in now_rates.items()],
        "best_existing": best_existing, "more_cameras": more_cameras,
        "final": public(final_rep), "final_rates": [{"door": d, "rate": r} for d, r in final_rates.items()],
    }
