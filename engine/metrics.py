"""
metrics.py

Runs a full audit: many attacker starting points, through the route
search, and boils the results down into a few numbers a person can
actually understand — success rate, how confident we are in that
number, and where the weak spots are.
"""

import numpy as np
from twin import Twin
from search import routes_to_target, reconstruct_route, STEPS
from attackers import ARCHETYPES, two_seed_sets


def risk_along_route(twin: Twin, route: list[tuple[int, int]],
                      risk: np.ndarray) -> float:
    """
    Given a route (a list of grid cells, from reconstruct_route),
    add up the risk at every cell along it.

    NOTE: this is deliberately SEPARATE from search.py's cost, which
    mixes distance and risk together to find the cheapest route. Here
    we only care about the risk PORTION, to judge detection, not the
    combined cost used for pathfinding.
    """
    total = 0.0
    for (r, c) in route:
        total += risk[r, c]
    return total


def run_one_attacker(twin: Twin,
                      start_point: tuple[float, float],
                      target_row: int, target_col: int,
                      risk: np.ndarray,
                      risk_weight: float,
                      cost_grid: np.ndarray,
                      came_from: dict) -> dict:
    """
    Check whether ONE attacker, starting at start_point, reaches the
    door — and if so, how risky that route was.

    NOTE: cost_grid and came_from are passed in already computed
    (from routes_to_target), since that search covers ALL starting
    points at once. We don't re-run the search per attacker — that
    would defeat the whole point of the backwards-search trick.
    """
    start_row, start_col = twin.cell_of(*start_point)

    if twin.blocked[start_row, start_col]:
        # NOTE: a seeded point occasionally lands exactly on a wall
        # cell due to rounding — treat as "no valid route" rather
        # than crashing.
        return {"reached": False, "risk": None, "route": []}

    if cost_grid[start_row, start_col] == np.inf:
        return {"reached": False, "risk": None, "route": []}

    route = reconstruct_route(came_from, (start_row, start_col),
                               (target_row, target_col))
    total_risk = risk_along_route(twin, route, risk)

    return {"reached": True, "risk": total_risk, "route": route}


def audit_door(twin: Twin,
                target_point: tuple[float, float],
                risk: np.ndarray,
                start_points: list[tuple[float, float]],
                risk_weight: float,
                detection_threshold: float = 0.5) -> dict:
    """
    Run every attacker in start_points against ONE door, and compute
    the success rate.

    detection_threshold: an attacker "succeeds" (isn't meaningfully
    detected) if their chance of NEVER being seen, exp(-total_risk),
    stays ABOVE this threshold. 0.5 means "more likely than not to
    slip through unseen."
    """
    target_row, target_col = twin.cell_of(*target_point)

    # NOTE: ONE search covers every starting point — this is the
    # backwards-Dijkstra trick from search.py paying off. We are not
    # looping the search itself, only looping the CHEAP lookup step
    # below.
    cost_grid, came_from = routes_to_target(
        twin, target_row, target_col, risk, risk_weight
    )

    results = []
    for start in start_points:
        result = run_one_attacker(
            twin, start, target_row, target_col, risk,
            risk_weight, cost_grid, came_from
        )
        results.append(result)

    reached = [r for r in results if r["reached"]]
    successes = [
        r for r in reached
        if np.exp(-r["risk"]) >= detection_threshold
    ]

    success_rate = len(successes) / len(start_points) if start_points else 0.0

    return {
        "target": target_point,
        "n_starts": len(start_points),
        "n_reached": len(reached),
        "n_successes": len(successes),
        "success_rate": success_rate,
        "results": results,
    }


def success_rate_with_range(twin: Twin,
                             target_point: tuple[float, float],
                             risk: np.ndarray,
                             risk_weight: float,
                             n_seed_sets: int = 5,
                             n_points: int = 200) -> dict:
    """
    Run the audit multiple times with DIFFERENT seed sets, so we can
    report a RANGE (e.g. "34% to 41%") instead of one single number
    that might just be luck.

    NOTE: this matches the build guide's instruction: "report a range
    by repeating with 5 different seed sets."
    """
    rates = []
    for seed in range(n_seed_sets):
        # NOTE: reuse the same start-point generator, but with a
        # different seed each time, to get genuinely different
        # samples rather than the same 200 points repeated.
        from attackers import boundary_start_points
        starts = boundary_start_points(twin, n_points, seed=seed + 1000)
        audit = audit_door(twin, target_point, risk, starts, risk_weight)
        rates.append(audit["success_rate"])

    return {
        "mean": float(np.mean(rates)),
        "min": float(np.min(rates)),
        "max": float(np.max(rates)),
        "all_rates": rates,
    }


if __name__ == "__main__":
    # ------------------------------------------------------------
    # SELF-TEST: run the "opportunist" archetype against the side
    # door, using a REAL risk grid this time (not the all-zero one
    # we used to test search.py in isolation).
    # ------------------------------------------------------------
    from coverage import camera_probability_grid, combine_cameras, risk_grid
    from calibration import build_camera_footprint

    twin = Twin.from_file("../scenarios/demo_house.json")

    # NOTE: we don't have real calibrated cameras yet (that needs an
    # actual Ring snapshot). For now, reuse the same fake circular
    # footprint approach from coverage.py's self-test, just to prove
    # metrics.py works end-to-end. This will be replaced once
    # calibration.py is wired up to real camera data.
    camera_position = (11.0, 1.0)
    rng = np.random.default_rng(seed=1)
    radii = rng.uniform(0, 8, 2000)
    angles = rng.uniform(0, 2 * np.pi, 2000)
    fake_footprint = np.array([
        (camera_position[0] + r * np.cos(a), camera_position[1] + r * np.sin(a))
        for r, a in zip(radii, angles)
    ])

    prob = camera_probability_grid(twin, camera_position, fake_footprint, max_range_m=8.0)
    combined = combine_cameras([prob])
    risk = risk_grid(combined)

    opportunist = ARCHETYPES[0]  # risk_weight = 0.5
    training, held_out = two_seed_sets(twin, n_points=200)

    audit = audit_door(
        twin, target_point=(6.0, 9.0),  # side door
        risk=risk, start_points=training,
        risk_weight=opportunist.risk_weight,
    )

    print(f"Archetype: {opportunist.name}")
    print(f"Starts tested: {audit['n_starts']}")
    print(f"Reached the door: {audit['n_reached']}")
    print(f"Succeeded (stayed hidden): {audit['n_successes']}")
    print(f"Success rate: {audit['success_rate']:.1%}")

    ranged = success_rate_with_range(twin, (6.0, 9.0), risk, opportunist.risk_weight)
    print(f"\nSuccess rate range over 5 seed sets: "
          f"{ranged['min']:.1%} to {ranged['max']:.1%} (mean {ranged['mean']:.1%})")
        # NOTE: quick comparison test — confirms that risk_weight
    # actually changes attacker behaviour. If camera_avoider's
    # success rate is LOWER than opportunist's, that proves the
    # risk-weighting mechanism works end to end.
    camera_avoider = ARCHETYPES[1]
    audit2 = audit_door(
        twin, target_point=(6.0, 9.0),
        risk=risk, start_points=training,
        risk_weight=camera_avoider.risk_weight,
    )
    print(f"\nArchetype: {camera_avoider.name}")
    print(f"Success rate: {audit2['success_rate']:.1%}")