"""
calibration.py

Works out what a camera REALLY sees, from one picture taken by that camera.

THE IDEA
    A camera looking at flat ground sees it through a "perspective" view: a
    patch of ground is squashed into the picture. If you tell us four spots
    that you can find both in the picture (a fence corner, the edge of the
    path, the door step) and on the plan, four pairs of points are enough to
    work out that squashing exactly. It is a 3x3 matrix called a homography.
    With it we can map any picture pixel to a spot on the ground, so the
    whole picture becomes a patch of ground: the camera's footprint.

    The footprint replaces the guessed wedge for that camera. Everything
    downstream (intruder routes, fixes, outage table, report) already works
    from a footprint, so nothing else has to change.

WHAT THIS DOES AND DOES NOT KNOW
    - It measures WHERE the camera looks (which ground is inside its picture).
    - It does not measure how WELL it sees there. The fall-off with distance is
      still the same model as for every other camera.
    - It assumes the four spots lie on one flat plane (the ground). A step, a
      slope or a raised deck breaks that assumption, so choose flat ground.
    - Walls and the house still block the view, as before (coverage.py does that).

HOW WE KNOW IT IS RIGHT (OR WARN IF NOT)
    - 5 or more pairs: each pair in turn is left out, the matrix is fitted on
      the rest, and we see how many metres off it lands that pair on the plan.
      The worst of those is reported, and a bad fit is refused.
    - Pairs that contradict each other (matched in a different order, or on
      both sides of the horizon) are refused with a sentence saying so.
    - 4 pairs fit perfectly by construction, so they prove nothing; the result
      says "not checked".

Pure maths (numpy, OpenCV, Shapely). No network, no files.
"""

from itertools import combinations

import cv2
import numpy as np
from shapely import contains_xy
from shapely.geometry import Point, Polygon, box


class CalibrationError(ValueError):
    """The points cannot be used. The message is written for the person who clicked them."""


MIN_PAIRS = 4
MAX_PAIRS = 8
WARN_ERROR_M = 1.0        # a check point landing this far off is worth a warning
REFUSE_ERROR_M = 3.0      # ...and this far off means the pairs do not agree
MIN_AREA_M2 = 1.0         # a footprint smaller than this is not a view of anything
MAX_RANGE_M = 80.0
MAX_PLAUSIBLE_FOV_DEG = 200.0   # a view wider than this, seen from the camera, is not a real camera picture


# ----------------------------------------------------------------------
# 1. Checking what was clicked
# ----------------------------------------------------------------------

def _as_points(raw, name):
    try:
        pts = np.asarray(raw, dtype=float)
    except (TypeError, ValueError):
        raise CalibrationError(f"The {name} points are not numbers.") from None
    if pts.ndim != 2 or pts.shape[1] != 2:
        raise CalibrationError(f"The {name} points must each be an x and a y.")
    if not np.all(np.isfinite(pts)):
        raise CalibrationError(f"The {name} points are not numbers.")
    return pts


def _tri_area(a, b, c):
    return 0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def check_pairs(image_pts, ground_pts):
    """
    Refuse point sets that cannot describe one flat view. Returns the two arrays.

    image_pts : picture points, each 0..1 across and down (0,0 = top-left)
    ground_pts: the same spots on the plan, in metres
    """
    img = _as_points(image_pts, "picture")
    gnd = _as_points(ground_pts, "plan")
    if len(img) != len(gnd):
        raise CalibrationError("Every spot needs a point on the picture AND a point on the plan.")
    if not (MIN_PAIRS <= len(img) <= MAX_PAIRS):
        raise CalibrationError(f"Use {MIN_PAIRS} to {MAX_PAIRS} matching spots (a 5th lets us check the result).")
    if np.any(img < 0) or np.any(img > 1):
        raise CalibrationError("A picture point is outside the picture.")

    for i, j in combinations(range(len(img)), 2):
        if np.hypot(*(img[i] - img[j])) < 0.02:
            raise CalibrationError(f"Spots {i + 1} and {j + 1} are almost on top of each other in the picture. Spread them out.")
        if np.hypot(*(gnd[i] - gnd[j])) < 0.3:
            raise CalibrationError(f"Spots {i + 1} and {j + 1} are almost on top of each other on the plan. Spread them out.")

    gspan = float(np.ptp(gnd, axis=0).max()) or 1.0
    signs = []
    # the check spot (the last one, when there are 5+) is left out of the order test: a misplaced check spot
    # should be reported as "the check spot does not agree", which fit_homography does
    fitting = len(img) - 1 if len(img) >= 5 else len(img)
    for i, j, k in combinations(range(fitting), 3):
        ai, ag = _tri_area(img[i], img[j], img[k]), _tri_area(gnd[i], gnd[j], gnd[k])
        if abs(ai) < 0.0003 or abs(ag) / gspan ** 2 < 0.002:
            raise CalibrationError(f"Spots {i + 1}, {j + 1} and {k + 1} are nearly in a straight line. "
                                   "Pick spots that form a wide shape, not a line.")
        signs.append(np.sign(ai) * np.sign(ag))
    # A real view keeps the same left-to-right order of the spots (or mirrors all of them). A mix means
    # two spots were matched to the wrong places.
    if len(set(signs)) > 1:
        raise CalibrationError("The spots on the picture and on the plan do not match up: their order is "
                               "different. Check that spot 1, 2, 3 ... are the same places in both.")
    return img, gnd


# ----------------------------------------------------------------------
# 2. The matrix
# ----------------------------------------------------------------------

def _fit(img, gnd, centre):
    """picture -> ground (centred on `centre`). Exact for 4 pairs, least squares for more."""
    src = img.astype(np.float32)
    dst = (gnd - centre).astype(np.float32)
    if len(img) == 4:
        h = cv2.getPerspectiveTransform(src, dst)
    else:
        h, _ = cv2.findHomography(src, dst, 0)
    if h is None or not np.all(np.isfinite(h)):
        raise CalibrationError("Those spots do not give a usable view. Try spots that are further apart.")
    return h.astype(float)


def _apply(h, pts):
    """Map picture points through h. Returns (ground xy, w) where w < 0 means 'behind the horizon' side."""
    p = np.column_stack([pts, np.ones(len(pts))]) @ h.T
    w = p[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = p[:, :2] / w[:, None]
    return xy, w


def fit_homography(image_pts, ground_pts):
    """
    -> dict(h, centre, errors_m, max_error_m, checked, w_sign)

    errors_m holds, for each pair, how far the matrix fitted WITHOUT that pair lands it from where it was
    clicked on the plan. Only filled in when there are 5 or more pairs.
    """
    img, gnd = check_pairs(image_pts, ground_pts)
    centre = gnd.mean(axis=0)
    h = _fit(img, gnd, centre)

    _, w = _apply(h, img)
    if not (np.all(w > 0) or np.all(w < 0)):
        raise CalibrationError("Some spots are above the horizon line of the picture and some below it. "
                               "Pick spots on the ground only.")

    # NOTE: the LAST spot is the check spot. The view is worked out from the others alone, and we measure how far
    # from the check spot it lands. (Leaving out each spot in turn was tried first: with only 4 left, a spot on
    # the edge is extrapolated, and normal click wobble of a few pixels swung the answer by metres.)
    error = None
    if len(img) >= 5:
        try:
            hc = _fit(img[:-1], gnd[:-1], centre)
            xy, wc = _apply(hc, img[-1:])
            _, wfit = _apply(hc, img[:-1])
            same_side = np.all(np.sign(wfit) == np.sign(wc[0]))      # the check spot is on the ground side too
            error = float(np.hypot(*(xy[0] + centre - gnd[-1]))) if np.isfinite(xy).all() and same_side else float("inf")
        except CalibrationError:
            error = float("inf")
        if not np.isfinite(error) or error > REFUSE_ERROR_M:
            raise CalibrationError(
                "The check spot does not agree with the others: they place it "
                f"{'far' if not np.isfinite(error) else f'{error:.1f} m'} from where you marked it. "
                "Check that each spot is the same place in the picture and on the plan, and that they all lie on flat ground.")
    return {"h": h, "centre": centre, "errors_m": [] if error is None else [error],
            "max_error_m": error,
            "checked": len(img) >= 5, "w_sign": 1.0 if np.all(w > 0) else -1.0, "w_min": float(np.abs(w).min())}


# ----------------------------------------------------------------------
# 3. The patch of ground the picture covers
# ----------------------------------------------------------------------

def _clip_halfplane(poly, a, b, c, s, eps):
    """Keep the part of a convex polygon where s*(a*u + b*v + c) >= eps (Sutherland-Hodgman, one edge)."""
    f = lambda p: s * (a * p[0] + b * p[1] + c) - eps
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        fp, fq = f(p), f(q)
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def ground_polygon(fit, camera_position, range_m):
    """
    The ground the whole picture shows, up to `range_m` from the camera, as a Shapely polygon in metres.

    The top of a picture often reaches the horizon, which is infinitely far away. So the picture is first cut
    to the part well below the horizon, then mapped to the ground, then cut to the range.
    """
    h, centre = fit["h"], fit["centre"]
    a, b, c = h[2]
    s = fit["w_sign"]
    eps = 0.05 * fit["w_min"]                                    # about 20 times further than the nearest spot
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    part = _clip_halfplane(square, a, b, c, s, eps)
    if len(part) < 3:
        raise CalibrationError("Nothing in the picture is on the ground. Pick spots lower in the picture.")
    xy, _ = _apply(h, np.array(part))
    poly = Polygon(xy + centre)
    if not poly.is_valid:
        poly = poly.buffer(0)
    reach = Point(camera_position).buffer(min(float(range_m), MAX_RANGE_M), 48)
    view = poly.intersection(reach)
    if view.is_empty or view.area < MIN_AREA_M2:
        raise CalibrationError("The picture's view does not reach any ground near the camera on the plan. "
                               "Check that the camera is marked in the right place, and the spots match.")
    if view.geom_type != "Polygon":                              # keep the largest piece of a split result
        view = max(view.geoms, key=lambda g: g.area)
    return view


# ----------------------------------------------------------------------
# 4. From polygon to what the engine needs
# ----------------------------------------------------------------------

def footprint_points(polygon, grid_m, seed=0, per_cell=6, floor=3000):
    """Evenly scattered points inside the polygon (the same style of footprint as a wedge's), as an (n, 2) array."""
    minx, miny, maxx, maxy = polygon.bounds
    cells = polygon.area / grid_m ** 2
    n = int(max(floor, per_cell * cells))
    rng = np.random.default_rng(seed)
    out = np.empty((0, 2))
    while len(out) < n:
        cand = np.column_stack([rng.uniform(minx, maxx, 4 * n), rng.uniform(miny, maxy, 4 * n)])
        out = np.vstack([out, cand[contains_xy(polygon, cand[:, 0], cand[:, 1])]])
    return out[:n]


def view_from_polygon(polygon, camera_position):
    """
    The wedge-style numbers the rest of the app draws and plans with: (heading_deg, fov_deg).
    heading = from the camera towards the middle of the footprint; fov = how wide it spreads as seen from the camera.
    """
    cx, cy = polygon.centroid.x - camera_position[0], polygon.centroid.y - camera_position[1]
    heading = float(np.degrees(np.arctan2(cy, cx)) % 360)
    xs, ys = np.array(polygon.exterior.coords).T
    ang = (np.degrees(np.arctan2(ys - camera_position[1], xs - camera_position[0])) - heading + 180) % 360 - 180
    fov = float(np.clip(ang.max() - ang.min(), 10.0, 360.0))
    return heading, fov


def calibrate(image_pts, ground_pts, camera_position, range_m):
    """
    The whole job. Returns a plain dict that can be saved with the camera:

      ground        footprint polygon, [[x, y], ...] in metres
      heading_deg   which way the footprint lies from the camera
      fov_deg       how wide it is, seen from the camera
      area_m2
      checked       True when there were 5+ pairs to check against
      max_error_m   worst leave-one-out miss in metres (None when not checked)
      warnings      sentences for the person
      image_points, ground_points   what was clicked (so it can be redrawn and redone)
    """
    fit = fit_homography(image_pts, ground_pts)
    img, gnd = check_pairs(image_pts, ground_pts)
    poly = ground_polygon(fit, camera_position, range_m)
    heading, fov = view_from_polygon(poly, camera_position)
    # A camera looks one way: the ground in its picture lies in front of it, in a wedge. A "view" that goes all the
    # way round the camera cannot come from a real picture. It means the spots are crowded together in the picture
    # but far apart on the plan (or matched to the wrong places), which makes the maths stretch the picture over a
    # huge area. Refuse it rather than draw a circle.
    if poly.contains(Point(camera_position)) or fov > MAX_PLAUSIBLE_FOV_DEG:
        raise CalibrationError(
            "These spots would make the camera see all the way round itself, which no camera does. Usually the spots are "
            "bunched together in the picture but far apart on the plan, or matched to the wrong places. Spread the spots "
            "across the picture (near the bottom, the middle and the sides), and put each one on the plan at the same place.")

    warnings = []
    if not fit["checked"]:
        warnings.append("Only 4 spots were used, so the result could not be checked. Add a 5th spot to test it.")
    elif fit["max_error_m"] > WARN_ERROR_M:
        warnings.append(f"The check spot landed {fit['max_error_m']:.1f} m from where you marked it. "
                        "The view is probably a little off; re-place the spots that look least certain.")
    gap = poly.distance(Point(camera_position))
    if gap > 6.0:
        warnings.append(f"The view starts {gap:.0f} m from the camera. If the camera looks at ground close to "
                        "itself, check the spots and the camera's position on the plan.")
    if poly.area < 4.0:
        warnings.append("The ground this camera sees is very small. Check the spots.")

    coords = [[round(float(x), 3), round(float(y), 3)] for x, y in poly.exterior.coords[:-1]]
    return {"ground": coords, "heading_deg": round(heading, 1), "fov_deg": round(fov, 1),
            "area_m2": round(float(poly.area), 1), "checked": fit["checked"],
            "max_error_m": None if fit["max_error_m"] is None else round(fit["max_error_m"], 2),
            "warnings": warnings,
            "image_points": [[round(float(u), 4), round(float(v), 4)] for u, v in img],
            "ground_points": [[round(float(x), 3), round(float(y), 3)] for x, y in gnd]}


def polygon_of(calibration) -> Polygon:
    """The footprint polygon from a saved calibration."""
    poly = Polygon(calibration["ground"])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


# ----------------------------------------------------------------------
# 5. Where the camera is, and which way it points
# ----------------------------------------------------------------------
#
# The same matched spots also say where the camera must be standing. A spot on the ground, the way it appears
# in the picture, fixes the direction of a line from the camera; four such lines meet at the camera. That needs the
# camera's lens angle (how wide it sees), which Ring does not tell us, so we assume one and report how much the answer
# moves if the lens is 15 degrees wider or narrower. The honest result is "about here, give or take X metres".

DEFAULT_LENS_DEG = 100.0      # horizontal field of view of a typical wide-angle home camera
LENS_WOBBLE_DEG = 15.0


def _pose_for_lens(img, gnd, aspect, lens_deg):
    """One pose for one assumed lens angle: (x, y, height_m, heading_deg, tilt_deg, reprojection_error_px_in_widths)."""
    f = 0.5 / np.tan(np.radians(lens_deg) / 2)                  # focal length, in picture widths
    k = np.array([[f, 0, 0.5], [0, f, 0.5 / aspect], [0, 0, 1.0]])
    centre = gnd.mean(axis=0)
    obj = np.column_stack([gnd - centre, np.zeros(len(gnd))]).astype(np.float64)
    pix = np.column_stack([img[:, 0], img[:, 1] / aspect]).astype(np.float64)
    ok, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, pix, k, None, flags=cv2.SOLVEPNP_IPPE)
    best = None
    for rv, tv in zip(rvecs or [], tvecs or []):
        rot, _ = cv2.Rodrigues(rv)
        cam = -rot.T @ tv.reshape(3)                              # camera position in the centred world
        if cam[2] <= 0.05:                                        # below the ground: the mirror-image solution
            continue
        proj, _ = cv2.projectPoints(obj, rv, tv, k, None)
        err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - pix) ** 2, axis=1))))
        if best is None or err < best[0]:
            best = (err, rv, tv, cam, rot)
    if best is None:
        return None
    err, rv, tv, cam, rot = best
    rv, tv = cv2.solvePnPRefineLM(obj, pix, k, None, rv, tv)
    rot, _ = cv2.Rodrigues(rv)
    cam = -rot.T @ tv.reshape(3)
    fwd = rot.T @ np.array([0.0, 0.0, 1.0])
    heading = float(np.degrees(np.arctan2(fwd[1], fwd[0])) % 360)
    tilt = float(np.degrees(np.arcsin(np.clip(-fwd[2], -1, 1))))
    return (float(cam[0] + centre[0]), float(cam[1] + centre[1]), float(cam[2]), heading, tilt, err)


def place_camera(image_pts, ground_pts, aspect, lens_deg=DEFAULT_LENS_DEG):
    """
    Where the camera is, from the same matched spots. Returns a dict:

      position       [x, y] metres on the plan
      height_m       how high the camera is above the ground
      heading_deg    which way it faces (0 = east, 90 = north)
      tilt_deg       how far it points down from level
      lens_deg       the lens angle assumed
      uncertainty_m  how far the position moves if the lens is 15 degrees wider or narrower
      warnings

    aspect = picture width / height (16/9 for a normal camera picture).
    """
    img, gnd = check_pairs(image_pts, ground_pts)
    if not (0.3 <= aspect <= 4.0):
        raise CalibrationError("The picture's shape is not usable.")
    lens = float(np.clip(lens_deg, 40.0, 150.0))
    mid = _pose_for_lens(img, gnd, aspect, lens)
    if mid is None:
        raise CalibrationError("Those spots do not give a believable camera position (it comes out underground). "
                               "Check that the spots are on the ground and match, or spread them further apart.")
    x, y, h, heading, tilt, err = mid
    moved = 0.0
    for d in (-LENS_WOBBLE_DEG, LENS_WOBBLE_DEG):
        other = _pose_for_lens(img, gnd, aspect, float(np.clip(lens + d, 40.0, 150.0)))
        if other is not None:
            moved = max(moved, float(np.hypot(other[0] - x, other[1] - y)))
    warnings = []
    if moved > 2.0:
        warnings.append(f"The camera's spot moves by about {moved:.1f} m if its lens is a bit wider or narrower than assumed. "
                        "Adjust the lens width, or drag the camera on the plan if you know where it is.")
    if h > 12 or tilt < -5 or tilt > 85:
        warnings.append("That height or tilt looks unusual for a wall camera. Check the spots.")
    return {"position": [round(x, 2), round(y, 2)], "height_m": round(h, 2), "heading_deg": round(heading, 1),
            "tilt_deg": round(tilt, 1), "lens_deg": round(lens, 1), "uncertainty_m": round(moved, 2),
            "warnings": warnings}


# ----------------------------------------------------------------------
# 6. Used by tests and the demo: make a pretend camera picture
# ----------------------------------------------------------------------

def project_ground_to_picture(ground_xy, camera_xyz, heading_deg, tilt_deg, fov_h_deg, aspect=16 / 9):
    """
    A pinhole camera, for testing: where does a ground point (x, y, 0) land in the picture (0..1, 0..1)?
    Returns None for a point behind the camera. heading is the compass-style map angle (0 = +x, 90 = +y),
    tilt is how far the camera is pitched DOWN from level.
    """
    cx, cy, cz = camera_xyz
    hd, tl = np.radians(heading_deg), np.radians(tilt_deg)
    fwd = np.array([np.cos(hd) * np.cos(tl), np.sin(hd) * np.cos(tl), -np.sin(tl)])
    right = np.array([np.sin(hd), -np.cos(hd), 0.0])
    up = np.cross(right, fwd)
    d = np.array([ground_xy[0] - cx, ground_xy[1] - cy, -cz])
    z = d @ fwd
    if z <= 1e-6:
        return None
    f = 0.5 / np.tan(np.radians(fov_h_deg) / 2)                 # focal length in picture-widths
    u = 0.5 + f * (d @ right) / z
    v = 0.5 / aspect + 0.0 - f * (d @ up) / z                   # picture height is 1/aspect widths; v then scaled below
    return float(u), float(v * aspect)
