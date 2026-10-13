import numpy as np
from twin import Twin

def line_of_sight_clear(twin, row_a, col_a, row_b, col_b, strict=False):
    """
    Can a camera in cell A see cell B?  Walk the straight line between them
    and fail on the first blocked cell.

    strict=True is for INDOOR floor plans:
      - the camera's own cell is ignored (a camera sits on a wall, which is
        itself a blocked cell),
      - a view may not squeeze diagonally between two wall cells that only
        touch at their corners.
    The outdoor behaviour (strict=False) is unchanged.
    """
    steps = max(abs(row_b - row_a), abs(col_b - col_a))
    if steps == 0:
        return True
    prev = None
    for i in range(1 if strict else 0, steps + 1):
        t = i / steps
        r = round(row_a + (row_b - row_a) * t)
        c = round(col_a + (col_b - col_a) * t)
        if twin.blocked[r, c]:
            return False
        if strict and prev is not None and r != prev[0] and c != prev[1]:
            if twin.blocked[prev[0], c] and twin.blocked[r, prev[1]]:
                return False
        prev = (r, c)
    return True

def falloff(distance_m, max_range_m):
    if distance_m >= max_range_m:
        return 0.0
    return 1.0 - (distance_m / max_range_m)

def camera_probability_grid(twin, camera_position, footprint_points, max_range_m, p_max=0.9):
    prob = np.zeros((twin.n_rows, twin.n_cols))
    footprint_cells = set()
    for x, y in footprint_points:
        r, c = twin.cell_of(x, y)
        if 0 <= r < twin.n_rows and 0 <= c < twin.n_cols:
            footprint_cells.add((r, c))
    cam_row, cam_col = twin.cell_of(*camera_position)
    for (r, c) in footprint_cells:
        if twin.blocked[r, c]:
            continue
        if not line_of_sight_clear(twin, cam_row, cam_col, r, c, strict=(twin.mode == "indoor")):
            continue
        cx, cy = twin._cell_center(r, c)
        dist = np.hypot(cx - camera_position[0], cy - camera_position[1])
        prob[r, c] = p_max * falloff(dist, max_range_m)
    return prob

def combine_cameras(probability_grids):
    miss = np.ones_like(probability_grids[0])
    for p in probability_grids:
        miss *= (1.0 - p)
    return 1.0 - miss

def risk_grid(combined_probability):
    return -np.log(1 - np.clip(combined_probability, 0, 0.999))
