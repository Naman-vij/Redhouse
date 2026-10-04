"""
baselines.py

Compares our defender against simpler alternatives, so we can show
it beats guessing (and beats "just maximise coverage").

All four are judged the SAME way: attacker success rate on the
HELD-OUT starting points, which no method saw while choosing its fix.
"""

import numpy as np
from twin import Twin
from cameras import Camera
from coverage import camera_probability_grid, combine_cameras
from defender import (success_rate, with_rotation, build_risk,
                      search_best_fix)


def covered_area_score(twin: Twin, cameras: list[Camera]) -> float:
    """
    Total 'how much of the property is watched' score: the sum of
    detection probability over all walkable cells.

    NOTE: this is what a camera PLANNING tool optimises - maximise
    area covered. It never asks 'can someone sneak through?'. That
    difference is exactly what we want to test.
    """
    grids = [camera_probability_grid(twin, c.position, c.footprint, c.max_range_m)
             for c in cameras]
    combined = combine_cameras(grids)
    return float(combined[~twin.blocked].sum())


def coverage_maximising_fix(twin, cameras, angle_range=45, angle_step=5):
    """
    Pick the single camera rotation that covers the MOST area.
    Ignores attackers entirely.
    """
    best = {"camera": None, "angle": 0, "score": covered_area_score(twin, cameras)}
    for cam in cameras:
        for angle in range(-angle_range, angle_range + 1, angle_step):
            if angle == 0:
                continue
            score = covered_area_score(twin, with_rotation(cameras, cam.name, angle))
            if score > best["score"]:
                best = {"camera": cam.name, "angle": angle, "score": score}
    return best


def random_fix_rates(twin, cameras, target, held_out, risk_weight,
                     n_trials=100, angle_range=45, angle_step=5, seed=7):
    """
    Pick a random camera and a random angle, n_trials times, and
    record the held-out success rate each time.

    NOTE: this is 'what if we just guessed?'. Fixed seed so the
    numbers are the same on every run.
    """
    rng = np.random.default_rng(seed)
    angles = [a for a in range(-angle_range, angle_range + 1, angle_step) if a != 0]
    rates = []
    for _ in range(n_trials):
        cam = cameras[rng.integers(len(cameras))]
        angle = int(rng.choice(angles))
        rates.append(success_rate(twin, with_rotation(cameras, cam.name, angle),
                                  target, held_out, risk_weight))
    return rates


def compare_all(twin, cameras, target, training, held_out, risk_weight) -> dict:
    # 1. Do nothing
    no_change = success_rate(twin, cameras, target, held_out, risk_weight)

    # 2. Random guessing
    random_rates = random_fix_rates(twin, cameras, target, held_out, risk_weight)

    # 3. Coverage-maximising (what planning tools do)
    cov = coverage_maximising_fix(twin, cameras)
    cov_rate = no_change if cov["camera"] is None else success_rate(
        twin, with_rotation(cameras, cov["camera"], cov["angle"]),
        target, held_out, risk_weight)

    # 4. Our defender (chosen on TRAINING, scored on HELD-OUT)
    ours = search_best_fix(twin, cameras, target, training, risk_weight)
    our_rate = no_change if not ours["improved"] else success_rate(
        twin, with_rotation(cameras, ours["best_camera"], ours["best_angle"]),
        target, held_out, risk_weight)

    return {
        "no_change": no_change,
        "random_mean": float(np.mean(random_rates)),
        "random_best": float(np.min(random_rates)),
        "random_worst": float(np.max(random_rates)),
        "share_random_beating_ours": float(np.mean([r < our_rate for r in random_rates])),
        "coverage_max": cov_rate,
        "coverage_max_choice": (cov["camera"], cov["angle"]),
        "ours": our_rate,
        "ours_choice": (ours["best_camera"], ours["best_angle"]),
    }


if __name__ == "__main__":
    from attackers import two_seed_sets
    from cameras import make_demo_cameras

    twin = Twin.from_file("../scenarios/demo_house.json")
    training, held_out = two_seed_sets(twin, 200)
    cameras = make_demo_cameras()

    r = compare_all(twin, cameras, (6.0, 9.0), training, held_out, 0.5)

    print("Attacker success rate on HELD-OUT attackers (lower = safer)\n")
    print(f"  1. No change:               {r['no_change']:.1%}")
    print(f"  2. Random guess (mean):     {r['random_mean']:.1%}   "
          f"(range {r['random_best']:.1%} to {r['random_worst']:.1%})")
    print(f"  3. Maximise coverage:       {r['coverage_max']:.1%}   "
          f"(chose {r['coverage_max_choice']})")
    print(f"  4. Our defender:            {r['ours']:.1%}   "
          f"(chose {r['ours_choice']})")
    print(f"\n  Random guesses that beat our defender: "
          f"{r['share_random_beating_ours']:.0%}")