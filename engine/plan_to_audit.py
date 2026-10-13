"""
plan_to_audit.py

Proof of the WHOLE CHAIN on the test plan:

   plan image + scale + inside click + camera specs
        -> site_plan.extract_plan            (detect)
        -> accuracy check against known truth
        -> to_twin_dict -> Twin              (the engine's map)
        -> cameras_from_specs                (position/heading/fov/range)
        -> audit_door                        (how many attackers get in)
        -> search_best_fix + verify          (the smallest fix)
        -> outage_effect                     (what one camera dropping costs)

NOTE: the accuracy numbers printed here are the honest answer to
"how exact is the extracted plan?". They are measured, not claimed.
"""

import numpy as np
from shapely.geometry import Polygon

import make_test_plan as truth
from site_plan import (load_plan_image, scale_from_two_points, extract_plan,
                       to_twin_dict, cameras_from_specs, draw_overlay)
from twin import Twin
from cameras import make_wedge_footprint
from attackers import two_seed_sets
from defender import (build_risk, success_rate, search_best_fix,
                      verify_on_held_out, describe_fix)
from metrics import audit_door
from outage import outage_effect, describe_change, single_points_of_failure

RISK_WEIGHT = 0.5          # the "opportunist" archetype


def iou(a_pts, b_pts):
    """Overlap score for two polygons: 1.0 = identical."""
    a, b = Polygon(a_pts), Polygon(b_pts)
    return a.intersection(b).area / a.union(b).area


def main():
    # ---- 1. make the test plan and read it back ---------------------
    path, (p1, p2, dist_m), inside_px = truth.make_plan("test_plan.png")
    img = load_plan_image(path)
    ppm = scale_from_two_points(p1, p2, dist_m)
    print(f"scale: {ppm:.3f} px/m   (true {truth.PPM})   "
          f"error {abs(ppm - truth.PPM) / truth.PPM * 100:.2f}%")

    # ---- 2. DETECT --------------------------------------------------
    ex = extract_plan(img, ppm, inside_px=inside_px)
    for w in ex.warnings:
        print("  warning:", w)

    bw = max(x for x, _ in ex.boundary_m) - min(x for x, _ in ex.boundary_m)
    bh = max(y for _, y in ex.boundary_m) - min(y for _, y in ex.boundary_m)
    print(f"\nproperty: {bw:.2f} x {bh:.2f} m   (true 24.00 x 18.00)  "
          f"IoU {iou(ex.boundary_m, truth.TRUE_BOUNDARY):.4f}")

    print(f"buildings found: {len(ex.obstacles_m)}")
    if ex.obstacles_m:
        o = ex.obstacles_m[0]
        ow = max(x for x, _ in o) - min(x for x, _ in o)
        oh = max(y for _, y in o) - min(y for _, y in o)
        print(f"  house: {ow:.2f} x {oh:.2f} m   (true 10.00 x 8.00)  "
              f"IoU {iou(o, truth.TRUE_HOUSE):.4f}")

    # doors: match each detected door to its nearest true door
    true_doors = list(truth.TRUE_DOORS.items())
    names = []
    print(f"doors found: {len(ex.doors_m)}")
    for (dx, dy) in ex.doors_m:
        name, (tx, ty) = min(true_doors, key=lambda kv: np.hypot(kv[1][0] - dx, kv[1][1] - dy))
        names.append(name)
        print(f"  {name}: detected ({dx:.2f}, {dy:.2f})  true ({tx:.1f}, {ty:.1f})  "
              f"off by {np.hypot(tx - dx, ty - dy) * 100:.0f} cm")

    # ---- 3. CONFIRM step artefact -----------------------------------
    specs = [
        {"name": "C1", "position": {"m": [11.0, 1.0]}, "heading": 90,
         "fov_deg": 90, "range_m": 9.0},
        {"name": "C2", "position": {"m": [2.0, 2.0]}, "heading": 0,      # planted mistake
         "fov_deg": 90, "range_m": 9.0},
        {"name": "C3", "position": {"m": [21.0, 8.0]}, "heading": 180,
         "fov_deg": 90, "range_m": 9.0},
    ]
    cameras, cam_warnings = cameras_from_specs(specs, ex)
    for w in cam_warnings:
        print("  camera warning:", w)
    import cv2
    cv2.imwrite("test_plan_overlay.png", draw_overlay(img, ex, cameras, specs))
    print("\nwrote test_plan_overlay.png (the confirm-step picture)")

    # ---- 4. build the engine map from the EXTRACTED plan ------------
    data = to_twin_dict(ex, grid_m=0.5, site_id="test-plan", door_ids=names)
    twin = Twin(data)
    print(f"twin grid: {twin.n_cols} x {twin.n_rows} cells, "
          f"{int(twin.blocked.sum())} blocked")

    # ---- 5. AUDIT ---------------------------------------------------
    training, held_out = two_seed_sets(twin, n_points=200)
    risk = build_risk(twin, cameras)
    door = dict(zip(names, ex.doors_m))
    print("\n--- attacker success per door (opportunist, held-out) ---")
    for name, pt in door.items():
        r = audit_door(twin, pt, risk, held_out, RISK_WEIGHT)
        print(f"  {name} at ({pt[0]:.1f}, {pt[1]:.1f}): "
              f"{r['success_rate'] * 100:.1f}% of 200 attackers get through")

    # ---- 6. SMALLEST FIX for the weakest door -----------------------
    weakest = max(door, key=lambda n: audit_door(
        twin, door[n], risk, held_out, RISK_WEIGHT)["success_rate"])
    print(f"\n--- fix search for the weakest door ({weakest}) ---")
    res = search_best_fix(twin, cameras, door[weakest], training, RISK_WEIGHT)
    ver = verify_on_held_out(twin, cameras, door[weakest], held_out, RISK_WEIGHT,
                             res["best_camera"] or cameras[0].name, res["best_angle"])
    fix = describe_fix(res, ver)
    print(" ", fix["description"])
    print(f"  training {res['baseline_rate'] * 100:.1f}% -> {res['best_rate'] * 100:.1f}%"
          f"   held-out {ver['held_out_before'] * 100:.1f}% -> "
          f"{ver['held_out_after'] * 100:.1f}%  (real improvement: "
          f"{ver['genuinely_improved']})")

    # ---- 7. OUTAGE --------------------------------------------------
    # NOTE: outage_effect(twin, cameras, camera_name, start_points,
    # risk_weight) checks EVERY door in the twin and returns one entry per
    # door, so we don't loop over doors ourselves.
    print("\n--- if one camera goes offline (held-out) ---")
    for c in cameras:
        effect = outage_effect(twin, cameras, c.name, held_out, RISK_WEIGHT)
        print(" ", describe_change(c.name, effect))

    print("\nSingle points of failure (10+ points at any door):")
    for f in single_points_of_failure(twin, cameras, held_out, RISK_WEIGHT):
        print(f"  {f['camera']}: {f['door']} {f['before']:.0%} -> {f['after']:.0%}")


if __name__ == "__main__":
    main()