"""
make_test_shop.py

A second FAKE floor plan for trying the upload wizard: a 16 x 10 m electronics workshop.
Writes two pictures:
  test_shop.png        the plan to upload
  test_shop_guide.png  the same plan with numbered markers showing what to click, in order

Layout (metres, x east, y north):
  office       x 0..5,  y 5..10   door gap in its south wall at x 2..3
  parts cage   x 11..16, y 6..10  door gap in its south wall at x 12..13   <- protect this
  shop floor   everything else (two long benches, one pillar)
  entrances    front door  (3.5, 0)  south wall      loading door (16, 2.6) east wall
  cameras      C1 front-left corner looking east along the shop, C2 on the east wall looking west
               (nothing watches the office or the parts cage, so there is real work to do)
"""
import cv2
import numpy as np

PPM, MARGIN, W, H = 45, 90, 16.0, 10.0
WALL = (30, 30, 30)
OT, IT = 0.3, 0.2

FRONT = (3.0, 4.0)          # gap in south wall (x range)
LOADING = (2.0, 3.2)        # gap in east wall (y range)


def to_px(x, y):
    return (int(round(MARGIN + x * PPM)), int(round(MARGIN + (H - y) * PPM)))


def _split(lo, hi, gaps):
    out, cur = [], lo
    for a, b in sorted(gaps):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        out.append((cur, hi))
    return out


def walls():
    r, t = [], IT / 2
    for a, b in _split(-OT, W + OT, [FRONT]):
        r.append((a, -OT, b, 0.0))                       # south
    r.append((-OT, H, W + OT, H + OT))                   # north
    r.append((-OT, 0.0, 0.0, H))                         # west
    for a, b in _split(0.0, H, [LOADING]):
        r.append((W, a, W + OT, b))                      # east
    for a, b in _split(0.0, 5.0, [(2.0, 3.0)]):
        r.append((a, 5.0 - t, b, 5.0 + t))               # office south wall
    r.append((5.0 - t, 5.0, 5.0 + t, H))                 # office east wall
    for a, b in _split(11.0, W, [(12.0, 13.0)]):
        r.append((a, 6.0 - t, b, 6.0 + t))               # cage south wall
    r.append((11.0 - t, 6.0, 11.0 + t, H))               # cage west wall
    return r


CLICKS = {
    "scale": [(0.0, 5.7), (W, 5.7)],                     # inner faces of west and east walls, 16 m
    "inside": (8.0, 3.0),
    "entrances": [("Front door", (3.5, 0.0)), ("Loading door", (16.0, 2.6))],
    "assets": [("Parts cage", (13.5, 8.0))],
    "cameras": [("C1", (0.5, 0.5), 25.0), ("C2", (15.5, 3.9), 180.0)],   # name, position, heading
}


def make_shop(path="test_shop.png", guide=False):
    w = MARGIN * 2 + int(W * PPM)
    h = MARGIN * 2 + int(H * PPM) + 70
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.rectangle(img, (8, 8), (w - 9, h - 9), (40, 40, 40), 2)
    cv2.rectangle(img, (MARGIN, h - 62), (MARGIN + 380, h - 18), (40, 40, 40), 1)
    cv2.putText(img, "ELECTRONICS WORKSHOP - GROUND FLOOR", (MARGIN + 8, h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 40, 40), 1, cv2.LINE_AA)
    cv2.putText(img, "SCALE 1:100   SHEET 1", (MARGIN + 8, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (40, 40, 40), 1, cv2.LINE_AA)
    for x0, y0, x1, y1 in walls():
        (a, b), (c, d) = to_px(x0, y1), to_px(x1, y0)
        cv2.rectangle(img, (a, b), (c, d), WALL, -1)
    for hx, hy in [(2.0, 5.0), (12.0, 6.0)]:             # door swings
        cv2.line(img, to_px(hx, hy), to_px(hx, hy + 1.0), (60, 60, 60), 1)
        cv2.ellipse(img, to_px(hx, hy), (PPM, PPM), 0, -90, 0, (60, 60, 60), 1, cv2.LINE_AA)
    for x0, y0, x1, y1 in [(6.0, 1.2, 10.0, 2.2), (6.0, 3.6, 10.0, 4.6)]:      # two benches
        (a, b), (c, d) = to_px(x0, y1), to_px(x1, y0)
        cv2.rectangle(img, (a, b), (c, d), (235, 235, 235), -1)
        cv2.rectangle(img, (a, b), (c, d), WALL, 2)
    (a, b), (c, d) = to_px(8.8, 7.2), to_px(9.2, 6.8)
    cv2.rectangle(img, (a, b), (c, d), WALL, -1)
    for text, (x, y) in [("OFFICE", (1.2, 7.6)), ("PARTS CAGE", (11.5, 9.4)), ("SHOP FLOOR", (6.3, 5.8))]:
        cv2.putText(img, text, to_px(x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (90, 90, 90), 1, cv2.LINE_AA)
    cv2.putText(img, "16.00 m", to_px(7.0, -0.9), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1, cv2.LINE_AA)

    if guide:
        n = [0]

        def mark(pt, label, col):
            n[0] += 1
            p = to_px(*pt)
            cv2.circle(img, p, 11, col, -1)
            cv2.putText(img, str(n[0]), (p[0] - 5, p[1] + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(img, label, (p[0] + 14, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)
        mark(CLICKS["scale"][0], "Scale A", (0, 120, 220))
        mark(CLICKS["scale"][1], "Scale B (16 m)", (0, 120, 220))
        mark(CLICKS["inside"], "Inside", (0, 140, 0))
        for nm, pt in CLICKS["entrances"]:
            mark(pt, nm, (160, 0, 160))
        for nm, pt in CLICKS["assets"]:
            mark(pt, nm, (0, 0, 200))
        for nm, pos, hd in CLICKS["cameras"]:
            mark(pos, f"{nm} position", (20, 20, 20))
            tip = (pos[0] + 2.5 * np.cos(np.radians(hd)), pos[1] + 2.5 * np.sin(np.radians(hd)))
            cv2.arrowedLine(img, to_px(*pos), to_px(*tip), (20, 20, 20), 2, tipLength=0.25)
            mark(tip, f"{nm} aim", (20, 20, 20))
    cv2.imwrite(path, img)
    return path


def user_clicks():
    c = CLICKS
    cams = []
    for nm, pos, hd in c["cameras"]:
        tip = (pos[0] + 3 * np.cos(np.radians(hd)), pos[1] + 3 * np.sin(np.radians(hd)))
        cams.append({"name": nm, "position": to_px(*pos), "aim": to_px(*tip), "fov_deg": 110, "range_m": 10})
    return {"scale": {"p1": to_px(*c["scale"][0]), "p2": to_px(*c["scale"][1]), "metres": 16.0},
            "inside": to_px(*c["inside"]),
            "entrances": [{"name": n, "point": to_px(*p)} for n, p in c["entrances"]],
            "assets": [{"name": n, "point": to_px(*p)} for n, p in c["assets"]],
            "cameras": cams}


if __name__ == "__main__":
    make_shop("test_shop.png"); make_shop("test_shop_guide.png", guide=True); print("wrote test_shop.png, test_shop_guide.png")
