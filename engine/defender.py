"""
defender.py  (multi-camera version)

Searches ALL cameras for the single rotation that most reduces
attacker success, then checks that fix on held-out attackers.

What changed from the one-camera version:
  - cameras are passed in as a LIST of Camera objects
  - the search loops over every camera AND every angle
  - the result says WHICH camera to adjust
"""

import numpy as np
from twin import Twin
from cameras import Camera
from coverage import camera_probability_grid, combine_cameras, risk_grid
from metrics import audit_door


def rotate_footprint(footprint, camera_position, angle_degrees):
    """Spin a camera's footprint around the camera's own position."""
    a = np.radians(angle_degrees)
    cos_a, sin_a = np.cos(a), np.sin(a)
    rel = footprint - np.array(camera_position)
    rotated = np.column_stack([
        rel[:, 0] * cos_a - rel[:, 1] * sin_a,
        rel[:, 0] * sin_a + rel[:, 1] * cos_a,
    ])
    return rotated + np.array(camera_position)


def build_risk(twin: Twin, cameras: list[Camera]) -> np.ndarray:
    """
    Turn a LIST of cameras into one risk grid.

    NOTE: this is the multi-camera step. Each camera gets its own
    probability grid, then combine_cameras() merges them (the
    "chance at least one camera sees you" maths from coverage.py).
    """
    grids = [camera_probability_grid(twin, c.position, c.footprint, c.max_range_m)
             for c in cameras]
    return risk_grid(combine_cameras(grids))


def success_rate(twin, cameras, target_point, start_points, risk_weight) -> float:
    risk = build_risk(twin, cameras)
    return audit_door(twin, target_point, risk, start_points, risk_weight)["success_rate"]


def with_rotation(cameras: list[Camera], camera_name: str, angle: float) -> list[Camera]:
    """
    Return a COPY of the camera list where one camera is rotated.

    NOTE: we never modify the original list. The search tries many
    "what if" versions, and we need the original untouched to
    compare against.
    """
    out = []
    for c in cameras:
        if c.name == camera_name:
            out.append(Camera(c.name, c.position,
                              rotate_footprint(c.footprint, c.position, angle),
                              c.max_range_m))
        else:
            out.append(c)
    return out


def search_best_fix(twin, cameras, target_point, training_points, risk_weight,
                    angle_range=45, angle_step=5) -> dict:
    """
    Try every camera x every angle. Keep the single best change.

    NOTE: 3 cameras x 18 angles = 54 audits. Each audit is one fast
    Dijkstra run, so this finishes in seconds.
    """
    baseline = success_rate(twin, cameras, target_point, training_points, risk_weight)
    best = {"camera": None, "angle": 0, "rate": baseline}

    for cam in cameras:
        for angle in range(-angle_range, angle_range + 1, angle_step):
            if angle == 0:
                continue
            candidate = with_rotation(cameras, cam.name, angle)
            rate = success_rate(twin, candidate, target_point, training_points, risk_weight)
            if rate < best["rate"]:
                best = {"camera": cam.name, "angle": angle, "rate": rate}

    return {
        "baseline_rate": baseline,
        "best_camera": best["camera"],
        "best_angle": best["angle"],
        "best_rate": best["rate"],
        "improved": best["camera"] is not None,
    }


def verify_on_held_out(twin, cameras, target_point, held_out_points,
                       risk_weight, best_camera, best_angle) -> dict:
    """Re-check the chosen fix on attackers the search never saw."""
    before = success_rate(twin, cameras, target_point, held_out_points, risk_weight)
    fixed = with_rotation(cameras, best_camera, best_angle)
    after = success_rate(twin, fixed, target_point, held_out_points, risk_weight)
    return {"held_out_before": before, "held_out_after": after,
            "genuinely_improved": after < before}


def describe_fix(search_result: dict, verify_result: dict) -> dict:
    """Turn the numbers into a recommendation a person can act on."""
    if not search_result["improved"]:
        return {
            "change_type": "none",
            "description": ("No single camera rotation helps this door. "
                            "An additional camera is likely needed."),
            "before_success_rate": search_result["baseline_rate"],
            "after_success_rate": search_result["baseline_rate"],
        }
    angle = search_result["best_angle"]
    # NOTE: positive angle = counter-clockwise, negative = clockwise,
    # as seen looking down at the map.
    direction = "counter-clockwise" if angle > 0 else "clockwise"
    return {
        "change_type": "rotate",
        "camera": search_result["best_camera"],
        "degrees": abs(angle),
        "direction": direction,
        "description": f"Rotate camera {search_result['best_camera']} "
                       f"by {abs(angle)} degrees {direction}.",
        "before_success_rate": search_result["baseline_rate"],
        "after_success_rate": search_result["best_rate"],
        "held_out_before": verify_result["held_out_before"],
        "held_out_after": verify_result["held_out_after"],
        "genuinely_improved": verify_result["genuinely_improved"],
    }


if __name__ == "__main__":
    from attackers import two_seed_sets
    from cameras import make_demo_cameras

    twin = Twin.from_file("../scenarios/demo_house.json")
    training, held_out = two_seed_sets(twin, n_points=200)
    cameras = make_demo_cameras()

    side_door = (6.0, 9.0)
    risk_weight = 0.5

    print("Cameras:", [c.name for c in cameras])
    print("Searching all cameras and angles...")
    result = search_best_fix(twin, cameras, side_door, training, risk_weight)

    verify = verify_on_held_out(twin, cameras, side_door, held_out, risk_weight,
                                result["best_camera"] or cameras[0].name,
                                result["best_angle"])

    fix = describe_fix(result, verify)
    print("\n--- Recommended fix ---")
    for k, v in fix.items():
        print(f"  {k}: {v}")