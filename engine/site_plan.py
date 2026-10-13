"""
site_plan.py

Turns a SITE PLAN IMAGE (PNG / JPG / PDF) plus a little user input into
the two things the engine needs:
    1. a twin dict   (boundary, house/obstacles, doors, in metres)
    2. a list of Camera objects (position, heading, field of view, range)

NO machine learning here. It is plain OpenCV + shapely:
    - the plan is just lines and fills, so colour/threshold rules work
    - every number it finds is shown back to the user to CONFIRM
      (detect -> confirm -> run), because "exact" depends on the image.

WHAT THE USER MUST PROVIDE (the plan alone cannot tell us these):
    1. SCALE      : click two points on the plan + say the real distance
                    between them (see scale_from_two_points)
    2. INSIDE     : click once inside the property (so we know which
                    outline is the property boundary, not the page frame)
    3. CAMERAS    : for each camera: where it is, which way it points,
                    field of view, range

DRAWING CONVENTIONS THE DETECTOR EXPECTS (a one-line legend for users):
    - property boundary : a CLOSED dark line (dashes are OK if gaps are small)
    - house / buildings : dark outline (fill is optional)
    - doors             : GREEN marks placed on the building outline
    - text, dimensions, north arrow, title block: ignored if small

INDOOR FLOOR PLANS (mode="indoor") follow the same idea with three changes:
    - the "property" is the FLOOR: the open area inside the outer walls, with
      every interior wall carved out of it at its drawn thickness
    - doors from outside are NOT drawn green. The user clicks each one and the
      gap in the wall is plugged, so the floor does not leak into the street
    - thin lines (door swing arcs, dimension lines) are ignored, otherwise a
      swing arc looks like a wall across the doorway

COORDINATES:
    - Plan metres: origin = SOUTH-WEST corner of the boundary's bounding
      box, x grows east, y grows north (image "up" = north).
    - Headings: 0 = east, 90 = north, 180 = west, 270 = south
      (counter-clockwise, same as the engine's make_wedge_footprint).
"""

from dataclasses import dataclass, field
from pathlib import Path

import re

import cv2
import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

from cameras import Camera, make_wedge_footprint, points_needed


class PlanError(Exception):
    """Raised when the plan can't be read safely. The message says why."""


# ----------------------------------------------------------------------
# 1. Loading + scale
# ----------------------------------------------------------------------

def load_plan_image(path, pdf_dpi=200):
    """
    Read a plan as a BGR numpy image.
    NOTE: for a PDF we render page 1 to pixels. Pixels-per-metre is then
    unknown (it depends on the drawing scale), so the user still gives
    the two-point scale below.
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        import pymupdf  # only needed for PDFs
        doc = pymupdf.open(str(path))
        pix = doc[0].get_pixmap(dpi=pdf_dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        return cv2.cvtColor(np.ascontiguousarray(img), cv2.COLOR_RGB2BGR)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise PlanError(f"Could not read image: {path}")
    return img


def scale_from_two_points(p1_px, p2_px, real_distance_m):
    """
    Pixels-per-metre from two clicked points and the real distance
    between them (e.g. both ends of a wall labelled '24 m').
    NOTE: this is the biggest accuracy lever. 1% error in scale = 1%
    error in every distance on the map. Click far-apart points.
    """
    d_px = float(np.hypot(p2_px[0] - p1_px[0], p2_px[1] - p1_px[1]))
    if d_px < 20 or real_distance_m <= 0:
        raise PlanError("Scale points are too close together (or distance <= 0).")
    return d_px / real_distance_m


# ----------------------------------------------------------------------
# 2. Frame (pixel <-> metre)
# ----------------------------------------------------------------------

@dataclass
class PlanFrame:
    ppm: float      # pixels per metre
    x0_px: float    # pixel column of the plan's west edge
    y0_px: float    # pixel row of the plan's south edge (image y points DOWN)

    def to_m(self, px, py):
        return ((px - self.x0_px) / self.ppm, (self.y0_px - py) / self.ppm)

    def to_px(self, x, y):
        return (self.x0_px + x * self.ppm, self.y0_px - y * self.ppm)


@dataclass
class PlanExtraction:
    frame: PlanFrame
    boundary_m: list          # [(x, y), ...]
    obstacles_m: list         # [[(x, y), ...], ...]
    doors_m: list             # [(x, y), ...]  snapped onto the building edge
    warnings: list = field(default_factory=list)


# ----------------------------------------------------------------------
# 3. Detection (pixels)
# ----------------------------------------------------------------------

def _colour_masks(img, dark_max):
    """Split the image into a GREEN mask (doors) and a DARK mask (lines)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, (35, 80, 50), (90, 255, 255))
    # NOTE: JPEG smears colour at edges, so grow green a little before
    # removing it from the dark mask.
    green_grown = cv2.dilate(green, np.ones((5, 5), np.uint8))
    dark = ((gray < dark_max) & (green_grown == 0)).astype(np.uint8)
    # NOTE: door marks sit ON a wall. If we simply removed green from the
    # dark mask, each door would punch a HOLE in the wall and the building
    # interior would leak into the garden. So for the geometry pass we put
    # the green marks back in as solid wall.
    dark_walls = (dark | (green > 0)).astype(np.uint8)
    return dark_walls, green


def _half_line_px(dark, ring_pts, band_px, dt=None):
    """
    Estimate half the drawn line thickness (pixels) NEAR ONE OUTLINE.

    Why this matters: the flood-filled open area stops at the line's
    INNER EDGE, but the real wall/boundary is the line's CENTRE. We have
    to move outward by half the line thickness to land on the centre.

    How: the distance transform of the dark mask peaks at the middle of a
    drawn line, and that peak IS half the thickness. We take the 95th
    percentile of the peaks found in a band around this outline, so a
    thick boundary line and a thinner wall each get their own figure.
    """
    if dt is None:
        dt = cv2.distanceTransform(dark, cv2.DIST_L2, 5)
    band = np.zeros_like(dark)
    cv2.polylines(band, [ring_pts.reshape(-1, 1, 2).astype(np.int32)], True, 1,
                  thickness=int(2 * band_px + 1))
    vals = dt[(band > 0) & (dark > 0)]
    if vals.size == 0:
        return 1.0
    return max(0.5, float(np.percentile(vals, 95)))


def _find_garden(dark, inside_px, gap_px):
    """
    Flood-fill idea: the property is the BRIGHT region that contains the
    user's 'inside' click, fenced in by the dark boundary line.
    - gap_px bridges small gaps in dashed lines.
    - if that region touches the image edge, the boundary is not closed.
    Returns (exterior_pts, [hole_pts...]) in pixels, measured AFTER the
    gap-bridging growth (the caller undoes it).
    """
    k = 2 * gap_px + 1
    grown = cv2.dilate(dark, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bright = (grown == 0).astype(np.uint8)
    n, labels = cv2.connectedComponents(bright, connectivity=4)

    x, y = int(round(inside_px[0])), int(round(inside_px[1]))
    h, w = bright.shape
    if not (0 <= x < w and 0 <= y < h):
        raise PlanError("The 'inside' click is outside the image.")
    lab = labels[y, x]
    if lab == 0:                       # clicked on a dark pixel: look nearby
        found = False
        for r in range(1, 16):
            patch = labels[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
            vals = patch[patch > 0]
            if vals.size:
                lab = np.bincount(vals).argmax()
                found = True
                break
        if not found:
            raise PlanError("The 'inside' click landed on a line; click on open ground.")

    region = (labels == lab).astype(np.uint8)
    # A broken boundary lets the open area spill outwards. It may run all
    # the way to the image edge, or - more often - only as far as the page
    # frame or border the plan is drawn inside. Both look like "the open
    # area reaches the edge of the sheet", so we test proximity to the
    # border rather than contact with it.
    h, w = region.shape
    ys, xs = np.nonzero(region)
    spans_sheet = ((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
                   >= 0.85 * h * w)
    inset = max(gap_px + 4, int(0.02 * min(h, w)))
    border = np.zeros_like(region)
    cv2.rectangle(border, (inset, inset), (w - 1 - inset, h - 1 - inset), 1, 1)
    if spans_sheet or (region & border).any():
        raise PlanError(
            "The property outline is not closed - the open area spills out to "
            "the edge of the sheet. Close any gap in the boundary line (or "
            "raise gap_px if it is drawn with long dashes). If the plan really "
            "is cropped right up to the boundary, add a white margin first.")

    contours, hier = cv2.findContours(region, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    hier = hier[0]
    outers = [i for i in range(len(contours)) if hier[i][3] == -1]
    outer = max(outers, key=lambda i: cv2.contourArea(contours[i]))
    holes = [contours[i][:, 0, :].astype(float)
             for i in range(len(contours)) if hier[i][3] == outer]
    return contours[outer][:, 0, :].astype(float), holes


def _plug_gap(dark, click_px, width_px):
    """
    Close a doorway in an outer wall so the floor does not leak outside.

    The user clicked the doorway. We find which way the wall runs there (the
    main direction of the dark pixels around the click) and draw a bar along
    it, as thick as the wall, across the gap. Falls back to a disk when the
    wall direction is unclear.
    """
    h, w = dark.shape
    x, y = int(round(click_px[0])), int(round(click_px[1]))
    R = int(max(12, 1.6 * width_px))
    y0, y1, x0, x1 = max(0, y - R), min(h, y + R + 1), max(0, x - R), min(w, x + R + 1)
    win = dark[y0:y1, x0:x1]
    ys, xs = np.nonzero(win)
    half = 0.6 * width_px
    if len(xs) < 20:
        cv2.circle(dark, (x, y), int(half), 1, -1)
        return
    pts = np.column_stack([xs, ys]).astype(float)
    pts -= pts.mean(axis=0)
    vals, vecs = np.linalg.eigh(np.cov(pts.T))
    if vals[1] < 2.0 * max(vals[0], 1e-6):          # no clear wall direction
        cv2.circle(dark, (x, y), int(half), 1, -1)
        return
    d = vecs[:, 1]
    dt = cv2.distanceTransform(win, cv2.DIST_L2, 5)
    thick = int(np.clip(2 * np.percentile(dt[win > 0], 90), 3, 40))
    a = (int(round(x - d[0] * half)), int(round(y - d[1] * half)))
    b = (int(round(x + d[0] * half)), int(round(y + d[1] * half)))
    cv2.line(dark, a, b, 1, thickness=thick)


def detect_geometry(img, inside_px, gap_px=3, dark_max=110,
                    min_obstacle_px2=400, simplify_px=2.0,
                    indoor=False, thin_px=0, plugs=()):
    """
    Find the property outline and building outlines IN PIXELS.
    Returns (boundary_polygon, [obstacle_polygons], half_line_px, green_mask).

    indoor   floor-plan mode (see the module note). The outline is placed on
             the INNER FACE of the walls, not their centre, and interior walls
             keep their drawn thickness.
    thin_px  ignore lines thinner than this many pixels (door swing arcs,
             dimension lines). 3 removes lines of 1-2 px. 0 = keep everything.
    plugs    [(x_px, y_px, width_px), ...] doorways in outer walls to close.
    """
    dark, green = _colour_masks(img, dark_max)
    if thin_px >= 3:
        k = np.ones((thin_px, thin_px), np.uint8)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, k)
    for px, py, wpx in plugs:
        _plug_gap(dark, (px, py), wpx)
    ext, holes = _find_garden(dark, inside_px, gap_px)
    dt = None if indoor else cv2.distanceTransform(dark, cv2.DIST_L2, 5)

    def centreline(ring_pts, outward):
        """
        Traced pixel outline -> polygon on the drawn line's CENTRE.

        NOTE: simplify BEFORE buffering. A traced outline is jagged (and
        a dashed line makes it worse); buffering a jagged ring with mitre
        corners throws the corners far out. Straightening the edges first
        measured 0.99 overlap against truth instead of 0.97.
        """
        if indoor:
            # undo only the gap-bridging growth: the floor ends at the wall's inner face
            grow = gap_px * (1 if outward else -1)
            return Polygon(ring_pts).simplify(simplify_px).buffer(grow, join_style=2), 0.0
        half = _half_line_px(dark, ring_pts, band_px=gap_px + 6, dt=dt)
        grow = (gap_px + half) * (1 if outward else -1)
        return Polygon(ring_pts).simplify(simplify_px).buffer(grow, join_style=2), half

    boundary, half = centreline(ext, outward=True)
    obstacles = []
    for hp in holes:
        if len(hp) < 4:
            continue
        poly, _ = centreline(hp, outward=False)     # a building shrinks inward
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        # NOTE: tiny holes are text letters, dimension labels, etc.
        if poly.area >= min_obstacle_px2:
            obstacles.append(poly)
    return boundary, obstacles, half, green


def detect_doors(green, min_area_px=12):
    """Centres of the green door marks, in pixels (left-to-right order)."""
    mask = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    doors = [tuple(cents[i]) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area_px]
    return sorted(doors)


# ----------------------------------------------------------------------
# 4. Putting it together
# ----------------------------------------------------------------------

def extract_plan(img, ppm, inside_px=None, gap_px=3, dark_max=110,
                 min_obstacle_m2=None, door_snap_m=1.0, indoor=False,
                 thin_px=None, plugs_m=()):
    """
    Image + scale + 'inside' click  ->  PlanExtraction (everything in metres).
    NOTE: this is the DETECT step. Show the result to the user (overlay)
    and let them fix it before running any audit.
    """
    h, w = img.shape[:2]
    if inside_px is None:
        inside_px = (w / 2.0, h / 2.0)       # fallback guess = image centre
    # NOTE: indoors a bench or a column is worth keeping at 0.2 m2; outdoors only a building is.
    if min_obstacle_m2 is None:
        min_obstacle_m2 = 0.2 if indoor else 2.0
    if thin_px is None:
        thin_px = 3 if indoor else 0

    warnings = []
    boundary_px, obstacles_px, half, green = detect_geometry(
        img, inside_px, gap_px=gap_px, dark_max=dark_max,
        min_obstacle_px2=min_obstacle_m2 * ppm * ppm, simplify_px=2.0,
        indoor=indoor, thin_px=thin_px,
        plugs=[(px, py, wm * ppm) for (px, py, wm) in plugs_m])

    minx, miny, maxx, maxy = boundary_px.bounds
    frame = PlanFrame(ppm=ppm, x0_px=minx, y0_px=maxy)

    def to_m_poly(poly):
        return [frame.to_m(x, y) for x, y in list(poly.exterior.coords)[:-1]]

    boundary_m = to_m_poly(boundary_px)
    obstacles_m = [to_m_poly(p) for p in obstacles_px]

    # Sanity checks -> warnings the user sees in the confirm step.
    b_area = Polygon(boundary_m).area
    if b_area < (8 if indoor else 20):
        warnings.append(f"The {'floor' if indoor else 'property'} area is only {b_area:.0f} m2 - is the scale right?")
    if not obstacles_m and not indoor:
        warnings.append("No building outline found inside the property.")
    if inside_px == (w / 2.0, h / 2.0):
        warnings.append("No 'inside' click given; assumed the image centre.")

    # Doors: green marks, snapped onto the nearest building edge.
    doors_m = []
    building = [Polygon(o) for o in obstacles_m]
    for px, py in ([] if indoor else detect_doors(green)):
        x, y = frame.to_m(px, py)
        if building:
            p = Point(x, y)
            edge_pt = min((nearest_points(b.exterior, p)[0] for b in building),
                          key=lambda q: q.distance(p))
            d = edge_pt.distance(p)
            if d > door_snap_m:
                warnings.append(f"Door mark at ({x:.1f}, {y:.1f}) m is {d:.1f} m "
                                f"from any building edge; snapped anyway.")
            x, y = edge_pt.x, edge_pt.y
        doors_m.append((x, y))
    if not doors_m and not indoor:
        warnings.append("No green door marks found.")

    return PlanExtraction(frame, boundary_m, obstacles_m, doors_m, warnings)


def to_twin_dict(ex, grid_m=0.5, site_id="from-plan", door_ids=None):
    """PlanExtraction -> the dict that engine/twin.py's Twin(data) takes."""
    r = lambda pts: [[round(x, 2), round(y, 2)] for x, y in pts]
    ids = door_ids or [f"door_{i + 1}" for i in range(len(ex.doors_m))]
    if len(ids) != len(ex.doors_m):
        raise PlanError(f"Got {len(ids)} door names for {len(ex.doors_m)} doors.")
    return {
        "site_id": site_id,
        "grid_m": grid_m,
        "boundary": r(ex.boundary_m),
        "obstacles": [{"type": "building", "polygon": r(o)} for o in ex.obstacles_m],
        "entrances": [{"id": i, "point": [round(x, 2), round(y, 2)]}
                      for i, (x, y) in zip(ids, ex.doors_m)],
    }


# ----------------------------------------------------------------------
# 5. Cameras from what the user tells us
# ----------------------------------------------------------------------

def heading_from_aim(cam_px, aim_px):
    """
    Heading from two clicks: the camera, then a point it looks toward.
    NOTE: image y points down, so we flip it to get a normal map angle.
    """
    dx = aim_px[0] - cam_px[0]
    dy = -(aim_px[1] - cam_px[1])
    if dx == 0 and dy == 0:
        raise PlanError("Camera and aim points are the same pixel.")
    return float(np.degrees(np.arctan2(dy, dx)) % 360)


def cameras_from_specs(specs, ex):
    """
    specs: list of dicts, one per camera:
        name       : "C1"
        position   : {"m": [x, y]}  OR  {"px": [x, y]}   (where it is mounted)
        heading    : degrees  OR  {"aim_px": [x, y]}     (which way it looks)
        fov_deg    : field of view, e.g. 90
        range_m    : how far it sees, e.g. 9
        online     : optional, default True
    Returns (cameras, warnings).
    """
    cams, warns = [], []
    boundary = Polygon(ex.boundary_m)
    buildings = [Polygon(o) for o in ex.obstacles_m]

    for i, s in enumerate(specs):
        pos = s["position"]
        if "m" in pos:
            x, y = float(pos["m"][0]), float(pos["m"][1])
            pos_px = ex.frame.to_px(x, y)
        else:
            pos_px = tuple(pos["px"])
            x, y = ex.frame.to_m(*pos_px)

        hd = s["heading"]
        heading = (heading_from_aim(pos_px, tuple(hd["aim_px"]))
                   if isinstance(hd, dict) else float(hd) % 360)

        fov, rng = float(s["fov_deg"]), float(s["range_m"])
        if not (0 < fov <= 360) or rng <= 0:
            raise PlanError(f"Camera {s['name']}: fov must be 1-360 and range > 0.")

        p = Point(x, y)
        if not boundary.buffer(0.5).contains(p):
            warns.append(f"{s['name']} at ({x:.1f}, {y:.1f}) m is outside the property.")
        for b in buildings:
            if b.buffer(-0.5).contains(p):
                warns.append(f"{s['name']} at ({x:.1f}, {y:.1f}) m is inside a building; "
                             "cameras are normally on a wall.")

        # NOTE: seed=i keeps the demo cameras identical to make_demo_cameras().
        fp = make_wedge_footprint((x, y), heading, fov, rng, seed=i)
        cams.append(Camera(s["name"], (x, y), fp, rng, s.get("online", True)))
    return cams, warns


# ----------------------------------------------------------------------
# 6. Overlay for the CONFIRM step
# ----------------------------------------------------------------------

def draw_overlay(img, ex, cameras=None, specs=None):
    """
    Draw what we detected on top of the plan so the user can check it.
    blue = property, orange = buildings, green = doors, red = cameras.
    """
    out = img.copy()
    px = lambda pts: np.array([ex.frame.to_px(x, y) for x, y in pts], np.int32).reshape(-1, 1, 2)
    cv2.polylines(out, [px(ex.boundary_m)], True, (255, 90, 0), 2)
    for o in ex.obstacles_m:
        cv2.polylines(out, [px(o)], True, (0, 140, 255), 2)
    for i, (x, y) in enumerate(ex.doors_m):
        cx, cy = map(int, ex.frame.to_px(x, y))
        cv2.circle(out, (cx, cy), 8, (0, 200, 0), 2)
        cv2.putText(out, f"door_{i + 1}", (cx + 10, cy - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 120, 0), 1, cv2.LINE_AA)
    for c in cameras or []:
        cx, cy = map(int, ex.frame.to_px(*c.position))
        col = (0, 0, 220) if c.online else (150, 150, 150)
        cv2.circle(out, (cx, cy), 5, col, -1)
        cv2.putText(out, c.name, (cx + 7, cy + 14), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, col, 1, cv2.LINE_AA)
    # field-of-view wedges (only when we know heading/fov from the specs)
    for c, s in zip(cameras or [], specs or []):
        pos_px = ex.frame.to_px(*c.position)
        hd = s["heading"]
        heading = (heading_from_aim(pos_px, tuple(hd["aim_px"]))
                   if isinstance(hd, dict) else float(hd))
        fov, rng = float(s["fov_deg"]), float(s["range_m"])
        pts = [c.position]
        for a in np.linspace(heading - fov / 2, heading + fov / 2, 24):
            pts.append((c.position[0] + rng * np.cos(np.radians(a)),
                        c.position[1] + rng * np.sin(np.radians(a))))
        col = (0, 0, 220) if c.online else (150, 150, 150)
        cv2.polylines(out, [px(pts)], True, col, 1, cv2.LINE_AA)
    return out


# ----------------------------------------------------------------------
# 7. The whole upload in one call: build_site
# ----------------------------------------------------------------------
#
# Everything the website's "use your own plan" flow sends arrives as one
# `settings` dict plus the image, and comes back as ONE result the backend can
# store. Keeping it in the engine (not in the web route) means it can be
# tested without a web server.
#
#   settings = {
#     "name": "Chemistry lab, level 2",
#     "mode": "indoor" | "outdoor",
#     "scale": {"p1": [x, y], "p2": [x, y], "metres": 20.0},   # pixels of the ORIGINAL image
#     "inside": [x, y],                      # a click on open floor / open ground
#     "entrances": [{"name": "Main entrance", "point": [x, y], "width_m": 1.2}],   # indoor
#     "doors":     [{"name": "Front door",    "point": [x, y]}],                   # outdoor
#     "assets":    [{"name": "Server room",   "point": [x, y]}],                   # indoor
#     "cameras":   [{"name": "C1", "position": [x, y], "aim": [x, y] or "heading_deg": 90,
#                    "fov_deg": 90, "range_m": 9, "online": true}],
#   }

MAX_SIDE_PX = 3000          # bigger images are shrunk before analysis (clicks are scaled to match)
MAX_ENTRY_RADIUS_M = 1.5    # an entrance must be this close to open floor


@dataclass
class SiteBuild:
    twin_dict: dict
    camera_specs: list      # plain dicts (metres) the backend stores and rebuilds cameras from
    meta: dict
    warnings: list
    overlay: dict           # everything in ORIGINAL image pixels, for the confirm step


def clean_id(raw, fallback, seen=()):
    """'Server room #2' -> 'server_room_2'. Unique within `seen`."""
    base = re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")[:32] or fallback
    out, n = base, 2
    while out in seen:
        out, n = f"{base}_{n}", n + 1
    return out


def clean_camera_name(raw, fallback, seen=()):
    """Camera names travel in web addresses as C2:45,C3:-75, so keep them simple."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "", str(raw or "").strip())[:12] or fallback
    out, n = base, 2
    while out in seen:
        out, n = f"{base}{n}", n + 1
    return out


def auto_grid_m(area_m2, indoor):
    """Finer grid for small floors, so a thin wall or a narrow door still shows up."""
    if not indoor:
        return 0.5
    return 0.25 if area_m2 <= 600 else 0.35 if area_m2 <= 1500 else 0.5


def cameras_from_records(records, grid_m):
    """
    [{"name","position":[x,y],"heading_deg","fov_deg","range_m","online"}] -> [Camera].
    seed=i, like cameras_from_specs, so a camera is rebuilt identically every time.
    """
    cams = []
    for i, r in enumerate(records):
        pos = (float(r["position"][0]), float(r["position"][1]))
        fov, rng, heading = float(r["fov_deg"]), float(r["range_m"]), float(r["heading_deg"])
        fp = make_wedge_footprint(pos, heading, fov, rng, n_points=points_needed(fov, rng, grid_m), seed=i)
        cams.append(Camera(r["name"], pos, fp, rng, bool(r.get("online", True)),
                           heading_deg=heading % 360, fov_deg=fov))
    return cams


def _snap_to_buildings(frame, buildings, px_pt, door_snap_m):
    """A clicked door -> the nearest point on a building outline (metres), and how far it moved."""
    x, y = frame.to_m(*px_pt)
    if not buildings:
        return x, y, 0.0
    p = Point(x, y)
    q = min((nearest_points(b.exterior, p)[0] for b in buildings), key=lambda c: c.distance(p))
    return q.x, q.y, q.distance(p)


def build_site(img, settings):
    from twin import Twin     # local import: twin.py needs shapely only, but keep module load light

    mode = settings.get("mode", "outdoor")
    if mode not in ("indoor", "outdoor"):
        raise PlanError("mode must be 'indoor' or 'outdoor'.")
    indoor = mode == "indoor"

    # --- shrink very large images; every click is scaled the same way
    h0, w0 = img.shape[:2]
    f = min(1.0, MAX_SIDE_PX / max(h0, w0))
    if f < 1.0:
        img = cv2.resize(img, (int(round(w0 * f)), int(round(h0 * f))), interpolation=cv2.INTER_AREA)
    work = lambda pt: (float(pt[0]) * f, float(pt[1]) * f)
    orig = lambda pt: [round(float(pt[0]) / f, 1), round(float(pt[1]) / f, 1)]

    # --- scale and the 'inside' click
    sc = settings.get("scale") or {}
    try:
        metres = float(sc["metres"])
        ppm = scale_from_two_points(work(sc["p1"]), work(sc["p2"]), metres)
    except (KeyError, TypeError, ValueError):
        raise PlanError("Set the scale first: click two points and say how far apart they are.")
    if not (0 < metres <= 2000):
        raise PlanError("The distance for the scale must be between 0 and 2000 metres.")
    if not (1.0 <= ppm <= 3000.0):
        raise PlanError(f"That scale works out to {ppm:.2f} pixels per metre, which is not plausible. "
                        "Check the two points and the distance.")
    if "inside" not in settings:
        raise PlanError("Click once on open floor" + ("" if indoor else " or ground") + " so the app knows which outline to use.")
    inside = work(settings["inside"])
    gap_px = int(np.clip(int(settings.get("gap_px", 3)), 1, 20))

    # --- the outline
    entrances = list(settings.get("entrances") or [])
    if indoor and not entrances:
        raise PlanError("Mark at least one entrance, a door from outside. Intruders start there.")
    plugs = []
    if indoor:
        for e in entrances:
            x, y = work(e["point"])
            plugs.append((x, y, float(np.clip(float(e.get("width_m", 1.2)), 0.5, 4.0))))
    try:
        ex = extract_plan(img, ppm, inside_px=inside, gap_px=gap_px, indoor=indoor,
                          thin_px=settings.get("thin_px"), plugs_m=plugs)
    except PlanError as exc:
        if plugs and "not closed" in str(exc):
            # an entrance clicked on a wall (not in the doorway gap) can break the outline we just sealed
            raise PlanError(str(exc) + " If the outline looked closed before you marked the entrances, one "
                            "entrance may be on a wall instead of in the doorway gap of the outer wall.") from None
        raise
    warnings = list(ex.warnings)

    boundary = Polygon(ex.boundary_m)
    floor_area = boundary.area - sum(Polygon(o).area for o in ex.obstacles_m)
    minx, miny, maxx, maxy = boundary.bounds
    grid_m = float(settings.get("grid_m") or auto_grid_m(floor_area, indoor))

    r2 = lambda pts: [[round(x, 2), round(y, 2)] for x, y in pts]
    doc = {
        "site_id": "uploaded", "name": str(settings.get("name") or "My site")[:60], "mode": mode,
        "grid_m": grid_m, "boundary": r2(ex.boundary_m),
        "obstacles": [{"type": "obstacle" if indoor else "building", "polygon": r2(o)} for o in ex.obstacles_m],
        "entrances": [], "entries": [],
    }

    # --- doors / entrances / protected spots
    frame = ex.frame
    buildings = [Polygon(o) for o in ex.obstacles_m]
    seen = set()
    overlay = {"entrances": [], "assets": [], "doors": [], "cameras": []}
    if indoor:
        twin0 = Twin({**doc, "entrances": [{"id": "x", "point": [0, 0]}]})     # only for the free-floor test
        for i, e in enumerate(entrances):
            xy = frame.to_m(*work(e["point"]))
            if twin0.free_cell_near(xy[0], xy[1], MAX_ENTRY_RADIUS_M) is None:
                raise PlanError(f"Entrance {i + 1} is not next to open floor. Click on the doorway itself, "
                                "in the gap of the outer wall.")
            eid = clean_id(e.get("name"), f"entrance_{i + 1}", seen)
            seen.add(eid)
            doc["entries"].append({"id": eid, "point": [round(xy[0], 2), round(xy[1], 2)]})
            overlay["entrances"].append({"id": eid, "point": orig(work(e["point"]))})
        assets = list(settings.get("assets") or [])
        if not assets:
            raise PlanError("Mark at least one protected spot, such as a server room, a safe or a freezer.")
        seen = set()
        for i, a in enumerate(assets):
            xy = frame.to_m(*work(a["point"]))
            cell = twin0.free_cell_near(xy[0], xy[1], 1.0)
            if cell is None:
                raise PlanError(f"'{a.get('name') or 'Protected spot ' + str(i + 1)}' is not on open floor. "
                                "Click inside the room, not on a wall or a bench.")
            cx, cy = twin0._cell_center(*cell)
            aid = clean_id(a.get("name"), f"spot_{i + 1}", seen)
            seen.add(aid)
            doc["entrances"].append({"id": aid, "point": [round(cx, 2), round(cy, 2)]})
            overlay["assets"].append({"id": aid, "point": orig(work(a["point"]))})
    else:
        doors = list(settings.get("doors") or [])
        door_pts = []
        for d in doors:
            x, y, moved = _snap_to_buildings(frame, buildings, work(d["point"]), 1.0)
            door_pts.append((clean_id(d.get("name"), "", seen), x, y))
            seen.add(door_pts[-1][0])
        for i, (x, y) in enumerate(ex.doors_m):                  # green marks, if the plan has them
            if any(np.hypot(x - cx, y - cy) < 1.5 for _, cx, cy in door_pts):
                continue                                          # the same door, already clicked
            did = clean_id(f"door_{i + 1}", "door", seen)
            seen.add(did)
            door_pts.append((did, x, y))
        if not door_pts:
            raise PlanError("Mark at least one door on the building, or paint the doors green on the plan.")
        for i, (did, x, y) in enumerate(door_pts):
            did = did or clean_id(f"door_{i + 1}", "door")
            doc["entrances"].append({"id": did, "point": [round(x, 2), round(y, 2)]})
            overlay["doors"].append({"id": did, "point": orig(frame.to_px(x, y))})

    # --- cameras
    records, cseen = [], set()
    for i, c in enumerate(settings.get("cameras") or []):
        pos_px = work(c["position"])
        x, y = frame.to_m(*pos_px)
        if "aim" in c and c["aim"] is not None:
            heading = heading_from_aim(pos_px, work(c["aim"]))
        elif c.get("heading_deg") is not None:
            heading = float(c["heading_deg"]) % 360
        else:
            raise PlanError(f"Camera {i + 1}: click where it points, or give a heading.")
        fov, rng = float(c.get("fov_deg", 90)), float(c.get("range_m", 9))
        if not (10 <= fov <= 360) or not (1 <= rng <= 80):
            raise PlanError(f"Camera {i + 1}: field of view must be 10 to 360 degrees and range 1 to 80 m.")
        name = clean_camera_name(c.get("name"), f"C{i + 1}", cseen)
        cseen.add(name)
        if not boundary.buffer(0.6).contains(Point(x, y)):
            warnings.append(f"Camera {name} is outside the {'floor' if indoor else 'property'} outline.")
        records.append({"name": name, "position": [round(x, 2), round(y, 2)], "heading_deg": round(heading, 1),
                        "fov_deg": fov, "range_m": rng, "online": bool(c.get("online", True))})
    if len(records) > 40:
        raise PlanError("At most 40 cameras per plan.")

    # --- the confirm-step overlay, in the ORIGINAL image's pixels
    def ring_px(pts):
        return [orig(frame.to_px(x, y)) for x, y in pts]
    overlay["boundary"] = ring_px(ex.boundary_m)
    overlay["obstacles"] = [ring_px(o) for o in ex.obstacles_m]
    for r in records:
        wedge = [tuple(r["position"])] + [
            (r["position"][0] + r["range_m"] * np.cos(np.radians(a)),
             r["position"][1] + r["range_m"] * np.sin(np.radians(a)))
            for a in np.linspace(r["heading_deg"] - r["fov_deg"] / 2, r["heading_deg"] + r["fov_deg"] / 2, 24)]
        overlay["cameras"].append({"name": r["name"], "position": orig(frame.to_px(*r["position"])),
                                   "wedge": ring_px(wedge), "heading_deg": r["heading_deg"]})

    meta = {"name": doc["name"], "mode": mode, "width_m": round(maxx - minx, 1), "height_m": round(maxy - miny, 1),
            "area_m2": round(floor_area, 1), "grid_m": grid_m, "n_cameras": len(records),
            "pixels_per_metre": round(ppm / f, 2)}
    return SiteBuild(doc, records, meta, warnings, overlay)
