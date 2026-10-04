"""
coverage.py

Answers: "for every cell in the grid, how likely is it that a camera
sees a person standing there?"

Two things must both be true for a camera to see a cell:
  1. The cell is inside the camera's footprint (from calibration.py)
  2. There's a clear line of sight — no wall in between

We store the result as "risk" instead of plain probability, because
risk can be ADDED UP along a walking route. Probability can't be
added the same way (you'd have to multiply survival chances instead,
which is fiddly). The formula risk = -ln(1 - p) makes addition work:
adding risks along a route is mathematically the same as multiplying
"chance of not being seen" at each step.
"""

import numpy as np
from twin import Twin


def line_of_sight_clear(twin: Twin, row_a: int, col_a: int,
                         row_b: int, col_b: int) -> bool:
    """
    Check whether a straight line from cell (row_a, col_a) to
    (row_b, col_b) passes through any blocked cell (a wall).

    NOTE: this is a simple "walk the line and check each step"
    approach (a version of Bresenham's line algorithm). It's not the
    fastest possible method, but it's easy to understand and fast
    enough for a hackathon-sized grid (tens of thousands of cells).
    """
    steps = max(abs(row_b - row_a), abs(col_b - col_a))
    if steps == 0:
        return True  # same cell, nothing in the way

    for i in range(steps + 1):
        # NOTE: we walk from A to B in small increments, checking
        # every cell we pass through along the way.
        t = i / steps
        r = round(row_a + (row_b - row_a) * t)
        c = round(col_a + (col_b - col_a) * t)

        if twin.blocked[r, c]:
            return False  # a wall is in the way — camera can't see past it

    return True


def falloff(distance_m: float, max_range_m: float) -> float:
    """
    How confident is the camera's detection at this distance?

    NOTE: real cameras don't detect equally well at 1m and at 20m —
    detection gets weaker with distance. This is a simple straight-
    line falloff (linear), which is good enough for an MVP. It could
    be replaced with a curve later if real Ring data suggests a
    different shape.
    """
    if distance_m >= max_range_m:
        return 0.0
    return 1.0 - (distance_m / max_range_m)


def camera_probability_grid(twin: Twin,
                             camera_position: tuple[float, float],
                             footprint_points: np.ndarray,
                             max_range_m: float,
                             p_max: float = 0.9) -> np.ndarray:
    """
    Build a grid the same shape as twin.blocked, where each cell holds
    the PROBABILITY (0 to 1) that this one camera detects a person
    standing there.

    p_max: even at point-blank range, we cap detection confidence
    below 100% — cameras aren't perfect. 0.9 is a reasonable default;
    this number can later be tuned using real Ring event history.
    """
    prob = np.zeros((twin.n_rows, twin.n_cols))

    # NOTE: convert the footprint (a scattered list of map points from
    # calibration.py) into a set of grid cells, so we can quickly look
    # up "is this cell inside the footprint?"
    footprint_cells = set()
    for x, y in footprint_points:
        r, c = twin.cell_of(x, y)
        if 0 <= r < twin.n_rows and 0 <= c < twin.n_cols:
            footprint_cells.add((r, c))

    cam_row, cam_col = twin.cell_of(*camera_position)

    for (r, c) in footprint_cells:
        if twin.blocked[r, c]:
            continue  # can't detect a person standing inside a wall

        if not line_of_sight_clear(twin, cam_row, cam_col, r, c):
            continue  # a wall blocks this specific line of sight

        cx, cy = twin._cell_center(r, c)
        dist = np.hypot(cx - camera_position[0], cy - camera_position[1])

        prob[r, c] = p_max * falloff(dist, max_range_m)

    return prob


def combine_cameras(probability_grids: list[np.ndarray]) -> np.ndarray:
    """
    Combine several cameras' probability grids into one overall
    "chance of being seen by AT LEAST ONE camera" grid.

    NOTE: this is NOT simple addition. If camera A has 60% chance and
    camera B has 50% chance of seeing the same cell, the combined
    chance is NOT 110% (impossible) — it's:
        1 - (chance A misses) x (chance B misses)
      = 1 - (1 - 0.6) x (1 - 0.5)
      = 1 - 0.4 x 0.5
      = 1 - 0.2 = 0.8  (80%)
    This is standard probability maths for independent events.
    """
    combined_miss_chance = np.ones_like(probability_grids[0])
    for p in probability_grids:
        combined_miss_chance *= (1.0 - p)

    combined_prob = 1.0 - combined_miss_chance
    return combined_prob


def risk_grid(combined_probability: np.ndarray) -> np.ndarray:
    """
    Convert probability into "risk" — the value we actually use for
    route planning, because it can be added along a path.

    NOTE: np.clip stops us from ever computing log(0), which would
    error out. A cell with 100% detection becomes a very large but
    finite risk instead of infinity.
    """
    safe_prob = np.clip(combined_probability, 0, 0.999)
    return -np.log(1 - safe_prob)


if __name__ == "__main__":
    # ------------------------------------------------------------
    # SELF-TEST: one camera, positioned in open ground, looking
    # toward a small area. We check that:
    #   1. cells near the camera have higher probability than far ones
    #   2. cells behind the house have ZERO probability (blocked)
    # ------------------------------------------------------------
    twin = Twin.from_file("../scenarios/demo_house.json")

    # NOTE: normally this footprint would come from calibration.py's
    # build_camera_footprint(). Here we fake a simple circular
    # footprint by hand, just to test coverage.py on its own first —
    # this is the same "test each piece independently" approach we
    # used for twin.py.
    camera_position = (11.0, 1.0)  # near the front, in the garden

    # NOTE (corrected): the previous version only sampled points on
    # the far EDGE of an 8m circle (using np.linspace on the angle
    # only, with a FIXED radius of 8). That meant every point was at
    # distance ~8m from the camera — right at the range limit, where
    # falloff() returns almost 0. To properly test falloff, we need
    # points scattered THROUGHOUT the circle: some very close to the
    # camera, some near the edge, and everything in between.
    rng = np.random.default_rng(seed=42)
    n_points = 2000
    radii = rng.uniform(0, 8, n_points)       # distances from 0 to 8m
    angles = rng.uniform(0, 2 * np.pi, n_points)

    fake_footprint = np.array([
        (camera_position[0] + r * np.cos(a), camera_position[1] + r * np.sin(a))
        for r, a in zip(radii, angles)
    ])

    prob_grid = camera_probability_grid(
        twin, camera_position, fake_footprint, max_range_m=8.0
    )

    print(f"Max probability in grid: {prob_grid.max():.3f}")
    print(f"Cells with non-zero probability: {(prob_grid > 0).sum()}")

    # NOTE: check a cell we KNOW should be blocked — right behind the
    # house, on the far side from the camera.
    behind_house_row, behind_house_col = twin.cell_of(11.0, 16.0)
    print(f"Probability directly behind the house: "
          f"{prob_grid[behind_house_row, behind_house_col]:.3f} (should be 0.0)")

    risk = risk_grid(prob_grid)
    print(f"Max risk value: {risk.max():.3f}")