"""
make_test_lab.py

Draws a FAKE indoor floor plan (a small lab) whose true measurements we
know, so the indoor extraction and the floor analysis can be tested against
the truth.

THE LAB (metres; x east, y north; the floor is x 0..20, y 0..12)
    corridor    y 0..3  across the whole south side
    main lab    x 0..12, y 3..12     (one concrete column, three benches)
    store       x 12..20, y 3..7
    server room x 12..20, y 7..12    (reached only through the store)
  Doors between rooms are 1 m gaps in the walls. Two ways in from outside:
    main entrance  (9.5, 0)   south wall
    fire exit      (20, 1.6)  east wall, a 1.2 m gap
  Things worth protecting:  server_room (16, 9.5),  freezer (2.5, 10.8)
  Three cameras: corridor, main lab, store. NONE in the server room, so
  there is a real blind spot for the analysis to find.

Like a real plan it also contains clutter the detector must ignore: a page
frame, a title block, labels, a dimension note, door swing arcs.
"""

import cv2
import numpy as np

PPM = 40
MARGIN = 90
FLOOR_W, FLOOR_H = 20.0, 12.0

WALL = (30, 30, 30)

# Walls as (x0, y0, x1, y1) rectangles in metres. The FLOOR is everything
# inside x 0..20, y 0..12 that is not one of the interior walls.
OUTER_T, INNER_T = 0.3, 0.2
MAIN_ENTRANCE = (9.0, 10.0)           # gap in the south wall (x range)
FIRE_EXIT = (1.0, 2.2)                # gap in the east wall (y range)


def _split(lo, hi, gaps):
    """The pieces of lo..hi left after removing the gap ranges."""
    pieces, cur = [], lo
    for g0, g1 in sorted(gaps):
        if g0 > cur:
            pieces.append((cur, g0))
        cur = max(cur, g1)
    if cur < hi:
        pieces.append((cur, hi))
    return pieces


def wall_rects():
    t, h = INNER_T / 2, OUTER_T
    rects = []
    # outer walls (sit just OUTSIDE the floor)
    for a, b in _split(-h, FLOOR_W + h, [MAIN_ENTRANCE]):
        rects.append((a, -h, b, 0.0))                          # south, with the main entrance gap
    rects.append((-h, FLOOR_H, FLOOR_W + h, FLOOR_H + h))      # north
    rects.append((-h, 0.0, 0.0, FLOOR_H))                      # west
    for a, b in _split(0.0, FLOOR_H, [FIRE_EXIT]):
        rects.append((FLOOR_W, a, FLOOR_W + h, b))             # east, with the fire exit gap
    # interior walls
    for a, b in _split(0.0, FLOOR_W, [(4.0, 5.0), (14.0, 15.0)]):
        rects.append((a, 3.0 - t, b, 3.0 + t))                 # corridor wall y = 3
    for a, b in _split(3.0, FLOOR_H, [(4.0, 5.0)]):
        rects.append((12.0 - t, a, 12.0 + t, b))               # lab | store+server wall x = 12
    for a, b in _split(12.0, FLOOR_W, [(16.0, 17.0)]):
        rects.append((a, 7.0 - t, b, 7.0 + t))                 # store | server wall y = 7
    return rects


# truth, for the tests -------------------------------------------------
ENTRANCES = {"main_entrance": (9.5, 0.0), "fire_exit": (20.0, 1.6)}
ASSETS = {"server_room": (16.0, 9.5), "freezer": (2.5, 10.8)}
CAMERAS = [
    # name, position (m), heading, fov, range
    ("C1", (0.5, 1.5), 0.0, 90.0, 12.0),        # corridor, looking east
    ("C2", (0.5, 11.5), 315.0, 90.0, 12.0),     # main lab corner, looking south-east
    ("C3", (19.5, 5.0), 180.0, 90.0, 9.0),      # store, looking west
]
ROOMS = {
    "corridor": (0.1, 0.1, 19.9, 2.9),
    "main_lab": (0.1, 3.1, 11.9, 11.9),
    "store": (12.1, 3.1, 19.9, 6.9),
    "server_room": (12.1, 7.1, 19.9, 11.9),
}


def to_px(x, y):
    """Plan metres -> image pixels (image y points down)."""
    return (int(round(MARGIN + x * PPM)), int(round(MARGIN + (FLOOR_H - y) * PPM)))


def make_lab(path="test_lab.png"):
    w = MARGIN * 2 + int(FLOOR_W * PPM)
    h = MARGIN * 2 + int(FLOOR_H * PPM) + 70
    img = np.full((h, w, 3), 255, np.uint8)

    # --- clutter the detector must ignore
    cv2.rectangle(img, (8, 8), (w - 9, h - 9), (40, 40, 40), 2)
    cv2.rectangle(img, (MARGIN, h - 62), (MARGIN + 360, h - 18), (40, 40, 40), 1)
    cv2.putText(img, "LEVEL 2 - CHEMISTRY LAB", (MARGIN + 8, h - 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "SCALE 1:100   SHEET 3", (MARGIN + 8, h - 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1, cv2.LINE_AA)

    # --- walls (solid dark)
    for x0, y0, x1, y1 in wall_rects():
        (px0, py1), (px1, py0) = to_px(x0, y0), to_px(x1, y1)
        cv2.rectangle(img, (px0, py0), (px1, py1), WALL, -1)

    # --- door swing arcs: thin lines, drawn like a real plan
    for hx, hy in [(4.0, 3.0), (14.0, 3.0)]:
        cv2.line(img, to_px(hx, hy), to_px(hx, hy + 1.0), (60, 60, 60), 1)
        cv2.ellipse(img, to_px(hx, hy), (PPM, PPM), 0, -90, 0, (60, 60, 60), 1, cv2.LINE_AA)

    # --- furniture: three closed benches and a column in the main lab
    for x0, y0 in [(1.0, 9.0), (5.0, 9.0), (8.0, 5.0)]:
        (a, b), (c, d) = to_px(x0, y0 + 1.0), to_px(x0 + 3.0, y0)
        cv2.rectangle(img, (a, b), (c, d), (235, 235, 235), -1)
        cv2.rectangle(img, (a, b), (c, d), WALL, 2)
    (a, b), (c, d) = to_px(5.75, 7.75), to_px(6.25, 7.25)
    cv2.rectangle(img, (a, b), (c, d), WALL, -1)

    # --- labels and a dimension note
    for text, (x, y) in [("MAIN LAB", (3.0, 7.4)), ("CORRIDOR", (13.0, 1.3)),
                         ("STORE", (14.2, 4.8)), ("SERVER", (14.4, 10.4))]:
        cv2.putText(img, text, to_px(x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (90, 90, 90), 1, cv2.LINE_AA)
    cv2.putText(img, "20.00 m", to_px(9.0, -0.9), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (40, 40, 40), 1, cv2.LINE_AA)

    cv2.imwrite(path, img)
    return path


def user_clicks():
    """What a person would click on this image: scale, inside, entrances, assets, cameras."""
    return {
        # the two inner faces of the west and east outer walls, 20.00 m apart
        "scale": {"p1": to_px(0, 6), "p2": to_px(FLOOR_W, 6), "metres": FLOOR_W},
        "inside": to_px(6.0, 1.5),                      # open floor in the corridor
        "entrances": [{"name": "main_entrance", "point": to_px(*ENTRANCES["main_entrance"])},
                      {"name": "fire_exit", "point": to_px(*ENTRANCES["fire_exit"])}],
        "assets": [{"name": n, "point": to_px(*p)} for n, p in ASSETS.items()],
        "cameras": [{"name": n, "position": to_px(*pos), "heading_deg": hd, "fov_deg": fov, "range_m": rng}
                    for n, pos, hd, fov, rng in CAMERAS],
    }


if __name__ == "__main__":
    print("wrote", make_lab(), "| true ppm", PPM)
