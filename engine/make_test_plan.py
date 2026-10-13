"""
make_test_plan.py

Draws a FAKE site plan whose true measurements we already know, so we can
check how accurate site_plan.py's extraction is.

Ground truth = exactly the demo house:
    boundary 24 x 18 m, house [[6,4],[16,4],[16,12],[6,12]],
    doors at (11,4) and (6,9).

NOTE: it deliberately adds things a real plan has and the detector must
IGNORE: a page frame, a title block, dimension text, a north arrow.
It also draws the boundary DASHED, to prove the gap-bridging works.
"""

import cv2
import numpy as np

PPM = 20          # pixels per metre in the drawing (truth, for checking)
MARGIN = 60       # page margin in pixels

TRUE_BOUNDARY = [(0, 0), (24, 0), (24, 18), (0, 18)]
TRUE_HOUSE = [(6, 4), (16, 4), (16, 12), (6, 12)]
TRUE_DOORS = {"front_door": (11, 4), "side_door": (6, 9)}
TRUE_W_M, TRUE_H_M = 24.0, 18.0


def to_px(x, y):
    """Plan metres -> image pixels (image y points down)."""
    return (int(round(MARGIN + x * PPM)), int(round(MARGIN + (TRUE_H_M - y) * PPM)))


def _dashed(img, a, b, colour, thickness, dash=14, gap=6):
    """Draw a dashed segment, to test the gap-bridging in _find_garden."""
    a, b = np.array(a, float), np.array(b, float)
    L = np.linalg.norm(b - a)
    u = (b - a) / L
    t = 0.0
    while t < L:
        p = a + u * t
        q = a + u * min(t + dash, L)
        cv2.line(img, tuple(p.astype(int)), tuple(q.astype(int)), colour, thickness)
        t += dash + gap


def make_plan(path="test_plan.png", dashed_boundary=True):
    w = MARGIN * 2 + int(TRUE_W_M * PPM)
    h = MARGIN * 2 + int(TRUE_H_M * PPM) + 70        # extra room for a title block
    img = np.full((h, w, 3), 255, np.uint8)

    # --- clutter the detector must ignore -----------------------------
    cv2.rectangle(img, (8, 8), (w - 9, h - 9), (40, 40, 40), 2)       # page frame
    cv2.rectangle(img, (MARGIN, h - 60), (MARGIN + 330, h - 18), (40, 40, 40), 1)
    cv2.putText(img, "SITE PLAN - 12 EXAMPLE ST", (MARGIN + 8, h - 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "SCALE 1:50   SHEET 1 OF 1", (MARGIN + 8, h - 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1, cv2.LINE_AA)
    nx, ny = w - 50, 50                                                # north arrow
    cv2.arrowedLine(img, (nx, ny + 28), (nx, ny - 8), (40, 40, 40), 2, tipLength=0.35)
    cv2.putText(img, "N", (nx - 5, ny + 46), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (40, 40, 40), 1, cv2.LINE_AA)

    # --- the property boundary (dark, closed, optionally dashed) ------
    pts = [to_px(*p) for p in TRUE_BOUNDARY]
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if dashed_boundary:
            _dashed(img, a, b, (30, 30, 30), 3)
        else:
            cv2.line(img, a, b, (30, 30, 30), 3)

    # --- the house (dark outline, light fill, like a real plan) -------
    hp = np.array([to_px(*p) for p in TRUE_HOUSE], np.int32)
    cv2.fillPoly(img, [hp], (235, 235, 235))
    cv2.polylines(img, [hp], True, (30, 30, 30), 3)
    cv2.putText(img, "DWELLING", (to_px(8.2, 8.4)), cv2.FONT_HERSHEY_SIMPLEX,
                0.5, (90, 90, 90), 1, cv2.LINE_AA)

    # --- dimension text (small dark marks the detector must drop) ----
    cv2.putText(img, "24.00", (to_px(11.2, -0.9)), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "18.00", (to_px(-2.6, 9.0)), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (40, 40, 40), 1, cv2.LINE_AA)

    # --- doors: GREEN marks on the building outline ------------------
    for (x, y) in TRUE_DOORS.values():
        cv2.circle(img, to_px(x, y), 6, (0, 170, 0), -1)

    cv2.imwrite(path, img)
    # scale clicks: the two ends of the south boundary, 24.00 m apart
    scale_pts = (to_px(0, 0), to_px(24, 0), 24.0)
    inside_px = to_px(2.0, 15.0)        # open ground, north-west corner
    return path, scale_pts, inside_px


if __name__ == "__main__":
    p, sc, ins = make_plan()
    print("wrote", p, "| scale pts", sc, "| inside", ins, "| true ppm", PPM)
