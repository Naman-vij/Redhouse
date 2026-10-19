"""
make_sample_pictures.py - draws the "sample picture" for each demo camera.

Run once (python tools/make_sample_pictures.py); the results are committed:
    frontend/public/samples/C1.jpg, C2.jpg, C3.jpg
    frontend/src/lib/samplePictures.json   (the picture's path and the five matched spots)

Each picture is drawn with a pretend pinhole camera standing exactly where the demo plan puts that camera, so the
spots match the plan to the pixel and the measured view comes out right. It is a drawing, not a photograph, and
the pictures say so. Orange stakes mark the five spots so anyone clearing them can click them again.
"""
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
import calibration as C  # noqa: E402

W, H, SS = 1280, 720, 2
LENS = 90.0  # the demo cameras all have a 90 degree lens
HOUSE = ((6, 4, 0), (16, 12, 5.5))
FENCES = [((0, -0.05, 0), (24, 0.05, 1.8)), ((0, 17.95, 0), (24, 18.05, 1.8)),
          ((-0.05, 0, 0), (0.05, 18, 1.8)), ((23.95, 0, 0), (24.05, 18, 1.8))]

CAMERAS = {
    "C1": dict(pos=(11.0, 1.0), height=3.0, heading=90.0, tilt=40.0,
               spots=[(8.8, 3.5), (13.2, 3.5), (9.6, 2.6), (12.4, 2.6), (11.0, 3.0)]),
    "C2": dict(pos=(2.0, 2.0), height=3.0, heading=0.0, tilt=35.0,
               spots=[(4.5, 0.8), (4.5, 3.6), (8.0, 0.8), (8.5, 3.6), (6.2, 2.2)]),
    "C3": dict(pos=(21.0, 8.0), height=3.0, heading=180.0, tilt=40.0,
               spots=[(18.6, 5.6), (18.6, 10.4), (19.6, 6.4), (19.6, 9.6), (19.0, 8.0)]),
}


def make_rays(cam, w, h):
    hd, tl = np.radians(cam["heading"]), np.radians(cam["tilt"])
    fwd = np.array([np.cos(hd) * np.cos(tl), np.sin(hd) * np.cos(tl), -np.sin(tl)])
    right = np.array([np.sin(hd), -np.cos(hd), 0.0])
    up = np.cross(right, fwd)
    f = 0.5 / np.tan(np.radians(LENS) / 2)
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    aspect = w / h
    du = (U - 0.5) / f
    dv = -((V - 0.5) / aspect) / f
    d = fwd[None, None, :] + du[..., None] * right + dv[..., None] * up
    return d / np.linalg.norm(d, axis=-1, keepdims=True), V


def hit_box(o, d, lo, hi):
    lo, hi = np.array(lo, float), np.array(hi, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / np.where(np.abs(d) < 1e-9, 1e-9, d)
    t1 = (lo - o) * inv
    t2 = (hi - o) * inv
    tmin, tmax = np.minimum(t1, t2), np.maximum(t1, t2)
    enter = tmin.max(axis=-1)
    axis = tmin.argmax(axis=-1)
    exit_ = tmax.min(axis=-1)
    ok = (enter <= exit_) & (exit_ > 0) & (enter > 0)
    return np.where(ok, enter, np.inf), axis


def hashed(ix, iy):
    h = (ix.astype(np.int64) * 73856093) ^ (iy.astype(np.int64) * 19349663)
    return ((h % 1000) / 1000.0)


def render(name, cam):
    o = np.array([cam["pos"][0], cam["pos"][1], cam["height"]])
    d, V = make_rays(cam, W * SS, H * SS)
    with np.errstate(divide="ignore", invalid="ignore"):
        tg = np.where(d[..., 2] < -1e-6, -o[2] / d[..., 2], np.inf)
    gx, gy = o[0] + tg * d[..., 0], o[1] + tg * d[..., 1]

    best, kind, face = tg.copy(), np.zeros(tg.shape, np.int8), np.zeros(tg.shape, np.int8)  # 0 = ground
    for k, (lo, hi) in enumerate([HOUSE] + FENCES, start=1):
        t, axis = hit_box(o, d, lo, hi)
        closer = t < best
        best = np.where(closer, t, best)
        kind = np.where(closer, k, kind)
        face = np.where(closer, axis, face)
    px = o[0] + best * d[..., 0]
    py = o[1] + best * d[..., 1]
    pz = o[2] + best * d[..., 2]

    img = np.zeros((H * SS, W * SS, 3), np.float32)
    # sky
    sky = np.stack([235 - 60 * V, 215 - 40 * V, 170 - 10 * V], -1)  # BGR, pale blue near the top
    img[:] = sky
    # ground
    g = (kind == 0) & np.isfinite(best)
    inside = (gx > 0) & (gx < 24) & (gy > 0) & (gy < 18)
    n = hashed(np.floor(gx * 24), np.floor(gy * 24))
    stripe = 0.92 + 0.08 * (np.floor(gy / 1.2) % 2)
    lawn = np.stack([(52 + 14 * n) * stripe, (128 + 26 * n) * stripe, (66 + 14 * n) * stripe], -1)
    road = np.stack([95 + 10 * n, 98 + 10 * n, 102 + 10 * n], -1)
    verge = np.stack([84 + 10 * n, 138 + 14 * n, 112 + 10 * n], -1)
    base = np.where(inside[..., None], lawn, np.where((gy < 0)[..., None], road, verge))
    path = inside & (np.abs(gx - 11) < 0.6) & (gy < 4)
    joint = (np.abs(((gy * 2) % 1) - 0.5) > 0.47) | (np.abs(((gx * 2) % 1) - 0.5) > 0.47)
    pave = np.stack([190 + 8 * n, 196 + 8 * n, 202 + 8 * n], -1)
    pave = np.where(joint[..., None], pave * 0.78, pave)
    base = np.where(path[..., None], pave, base)
    # the ground in the distance fades into haze
    haze = np.clip((best - 25) / 60, 0, 0.7)[..., None]
    base = base * (1 - haze) + np.array([200, 205, 195]) * haze
    img = np.where(g[..., None], base, img)
    # stakes at the five spots
    for (sx, sy) in cam["spots"]:
        r2 = (gx - sx) ** 2 + (gy - sy) ** 2
        img = np.where((g & (r2 < 0.19 ** 2))[..., None], np.array([255, 255, 255]), img)
        img = np.where((g & (r2 < 0.13 ** 2))[..., None], np.array([0, 120, 255]), img)
    # house
    m = kind == 1
    south = m & (face == 1) & (d[..., 1] > 0)
    east = m & (face == 0) & (d[..., 0] < 0)
    west = m & (face == 0) & (d[..., 0] > 0)
    roof = m & (face == 2)
    wall = np.zeros(img.shape, np.float32)
    wall[south] = (205, 218, 232)
    wall[east] = (180, 192, 208)
    wall[west] = (170, 182, 198)
    wall[roof] = (70, 70, 80)
    wall[m & (pz < 0.4)] = (120, 125, 135)
    # doors and windows
    door = south & (np.abs(px - 11) < 0.45) & (pz < 2.1)
    side = west & (np.abs(py - 9) < 0.45) & (pz < 2.1)
    win_s = south & (((px > 7.2) & (px < 9.0)) | ((px > 13.0) & (px < 14.8))) & (((pz > 1.1) & (pz < 2.4)) | ((pz > 3.4) & (pz < 4.7)))
    win_s |= south & (np.abs(px - 11) < 0.7) & (pz > 3.4) & (pz < 4.7)
    win_e = east & (((py > 5.5) & (py < 7.0)) | ((py > 9.0) & (py < 10.5))) & (((pz > 1.1) & (pz < 2.4)) | ((pz > 3.4) & (pz < 4.7)))
    wall[win_s | win_e] = (215, 190, 150)
    wall[door] = (50, 50, 150)
    wall[side] = (120, 70, 40)
    img = np.where(m[..., None], wall, img)
    # fences
    f = kind >= 2
    plank = ((px + py) * 6.5 % 1) < 0.08
    fc = np.where(plank[..., None], np.array([55, 85, 125]), np.array([85, 125, 170])).astype(np.float32)
    fc = np.where((pz > 1.72)[..., None], fc * 0.8, fc)
    img = np.where(f[..., None], fc, img)

    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    label = f"{name}  sample picture drawn from the demo house"
    cv2.rectangle(img, (14, 14), (14 + 12 * len(label) // 1 - 40, 52), (30, 24, 20), -1)
    cv2.putText(img, label, (24, 41), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def spot_checks(name, cam):
    """Every spot must be well inside the picture and not hidden behind the house or a fence."""
    o = np.array([cam["pos"][0], cam["pos"][1], cam["height"]])
    cx, cy = cam["pos"]
    out = []
    for (sx, sy) in cam["spots"]:
        p = C.project_ground_to_picture((sx, sy), (cx, cy, cam["height"]), cam["heading"], cam["tilt"], LENS, aspect=W / H)
        assert p is not None, f"{name}: spot {(sx, sy)} is behind the camera"
        u, v = p
        assert 0.04 < u < 0.96 and 0.04 < v < 0.96, f"{name}: spot {(sx, sy)} lands at {(u, v)}, too close to the edge"
        target = np.array([sx, sy, 0.0])
        dirn = target - o
        dist = np.linalg.norm(dirn)
        dirn = dirn / dist
        for lo, hi in [HOUSE] + FENCES:
            t, _ = hit_box(o, dirn[None, None, :], lo, hi)
            assert not (np.isfinite(t[0, 0]) and t[0, 0] < dist - 1e-3), f"{name}: spot {(sx, sy)} is hidden behind a wall or fence"
        out.append({"img": [round(float(u), 4), round(float(v), 4)], "ground": [sx, sy]})
    us = [s["img"][0] for s in out]
    vs = [s["img"][1] for s in out]
    assert max(us) - min(us) > 0.35 and max(vs) - min(vs) > 0.12, f"{name}: spots too bunched in the picture ({min(us):.2f}-{max(us):.2f}, {min(vs):.2f}-{max(vs):.2f})"
    return out


def main():
    pics = os.path.join(ROOT, "frontend", "public", "samples")
    os.makedirs(pics, exist_ok=True)
    doc = {}
    for name, cam in CAMERAS.items():
        spots = spot_checks(name, cam)
        img = render(name, cam)
        cv2.imwrite(os.path.join(pics, f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 84])
        doc[name] = {"picture": f"/samples/{name}.jpg", "camera": list(cam["pos"]), "aspect": round(W / H, 4),
                     "lens_deg": LENS, "spots": spots}
        print(name, [s["img"] for s in spots])
    with open(os.path.join(ROOT, "frontend", "src", "lib", "samplePictures.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


if __name__ == "__main__":
    main()
