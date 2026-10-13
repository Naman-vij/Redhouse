from dataclasses import dataclass
import numpy as np
from twin import Twin

@dataclass
class Archetype:
    name: str
    risk_weight: float
    target_least_covered: bool = False

ARCHETYPES = [
    Archetype("opportunist", 0.5),
    Archetype("camera_avoider", 5.0),
    Archetype("side_rear_entry", 5.0, True),
]

def boundary_start_points(twin, n_points=200, seed=42):
    """
    Where attackers start: spread around the property edge.

    TWO FIXES over the first version, both found while testing plans
    traced from images:

    1. INSET. We sample just INSIDE the boundary, not exactly on it. A
       point exactly on the line falls in a cell whose centre may be
       outside the property, so it was marked blocked and silently
       counted as a failed attacker. That made the headline number
       depend on rounding: on the demo house, changing the plan width by
       5 cm swung the side door from 63% to 57% with nothing about the
       site actually different. With the inset, all 200 starts are valid
       and the figure holds at 60.0% from 23.90 m through 24.50 m.

    2. STRATIFIED. Instead of n random distances along the perimeter, we
       cut the perimeter into n equal slices and jitter one point inside
       each. Same cost, but no clumps or bare stretches, so two seeds
       agree much more closely.
    """
    rng = np.random.default_rng(seed)
    ring = twin.boundary.buffer(-0.6 * twin.grid_m).exterior
    edges = np.linspace(0, ring.length, n_points + 1)
    step = edges[1] - edges[0]
    pts = []
    for d in edges[:-1] + rng.uniform(0, 1, n_points) * step:
        p = ring.interpolate(d)
        pts.append((p.x, p.y))
    return pts

def entry_start_points(twin, n_points=200, seed=42, radius_m=1.5):
    """
    INDOOR: where attackers start. They come in through the entrances (the
    doors from outside), so each start is a spot on the floor just inside
    one of them. Starts are shared out evenly between the entrances and
    spread over the free floor within `radius_m` of each door.

    Same fixed seeds as the outdoor version, so the answer never changes
    between runs.
    """
    if not twin.entries:
        raise ValueError("This floor plan has no entrances, so there is nowhere for an intruder to start.")
    rng = np.random.default_rng(seed)
    g = twin.grid_m
    per = [n_points // len(twin.entries) + (1 if i < n_points % len(twin.entries) else 0)
           for i in range(len(twin.entries))]
    pts = []
    for entry, n in zip(twin.entries, per):
        ex, ey = entry["point"]
        r0, c0 = twin.cell_of(ex, ey)
        reach = int(np.ceil(radius_m / g))
        cells = []
        for r in range(max(0, r0 - reach), min(twin.n_rows, r0 + reach + 1)):
            for c in range(max(0, c0 - reach), min(twin.n_cols, c0 + reach + 1)):
                if twin.blocked[r, c]:
                    continue
                cx, cy = twin._cell_center(r, c)
                if np.hypot(cx - ex, cy - ey) <= radius_m:
                    cells.append((cx, cy))
        if not cells:
            raise ValueError(f"Entrance '{entry['id']}' is not next to any open floor.")
        pick = rng.integers(0, len(cells), n)
        jitter = rng.uniform(-g / 2, g / 2, (n, 2)) * 0.9      # stay inside the cell
        pts += [(cells[i][0] + j[0], cells[i][1] + j[1]) for i, j in zip(pick, jitter)]
    return pts


def start_points(twin, n_points, seed):
    """Starts for this kind of site: along the property edge outdoors, at the entrances indoors."""
    if twin.mode == "indoor":
        return entry_start_points(twin, n_points, seed)
    return boundary_start_points(twin, n_points, seed)


def two_seed_sets(twin, n_points=200):
    return start_points(twin, n_points, 42), start_points(twin, n_points, 99)
