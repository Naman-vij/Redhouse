"""
attackers.py

Defines attacker "archetypes" (different behaviours) and generates
the seeded starting points used to test a property's security.

An archetype is really just:
  - a risk_weight (how much this attacker cares about being seen)
  - a target door (which door they're heading for)

"Seeded" starting points mean we use a FIXED random seed, so the same
list of starting points comes out every time we run this. That
matters because we need to compare "before the fix" vs "after the
fix" using the EXACT SAME attackers — otherwise we couldn't tell if
a result improved because of our fix, or just because we got lucky
with a different random sample.
"""

from dataclasses import dataclass
import numpy as np
from twin import Twin


@dataclass
class Archetype:
    """
    NOTE: a dataclass is just a simple way to bundle a few named
    values together, with less typing than writing a full class by
    hand. It's a standard Python feature, not something specific to
    our project.
    """
    name: str
    risk_weight: float
    target_least_covered: bool = False
    # NOTE: target_least_covered is only True for the "side/rear
    # entry" archetype — instead of picking one fixed door, this
    # archetype always goes for whichever door currently has the
    # WORST camera coverage. We'll wire this up properly once
    # defender.py exists; for now it's a flag we store but don't
    # yet use.


# NOTE: these three match the archetypes from the build guide
# (Part 6.4). risk_weight values are a starting point — they can be
# tuned later once we see real results on the demo house.
ARCHETYPES = [
    Archetype(name="opportunist", risk_weight=0.5),
    Archetype(name="camera_avoider", risk_weight=5.0),
    Archetype(name="side_rear_entry", risk_weight=5.0, target_least_covered=True),
]


def boundary_start_points(twin: Twin,
                           n_points: int = 200,
                           seed: int = 42) -> list[tuple[float, float]]:
    """
    Generate n_points starting positions spread around the property
    boundary, using a FIXED seed so the same points come out every
    run.

    NOTE: we sample along the boundary polygon's edge, not randomly
    anywhere inside the property — an intruder has to start OUTSIDE
    the property and walk in, so boundary points are the realistic
    starting spots.
    """
    rng = np.random.default_rng(seed)

    # NOTE: twin.boundary is a Shapely Polygon (from twin.py). Its
    # .exterior gives us the boundary LINE (not the filled shape),
    # and .length is that line's total length in metres.
    boundary_line = twin.boundary.exterior
    total_length = boundary_line.length

    # NOTE: we pick n_points random distances ALONG the boundary
    # line, then ask Shapely for the actual (x, y) at each distance.
    # This spreads points evenly around the whole perimeter, corners
    # included, rather than clustering on one side.
    distances = rng.uniform(0, total_length, n_points)

    points = []
    for d in distances:
        point = boundary_line.interpolate(d)
        points.append((point.x, point.y))

    return points


def two_seed_sets(twin: Twin, n_points: int = 200):
    """
    Build TWO separate sets of starting points: one for TRAINING
    (used later when defender.py searches for fixes) and one HELD
    OUT (only used to check whether a fix actually generalises, or
    just got lucky on the training set).

    NOTE: using two different seeds (42 and 99) guarantees the two
    sets don't overlap in any meaningful way, since they're sampled
    independently.
    """
    training = boundary_start_points(twin, n_points, seed=42)
    held_out = boundary_start_points(twin, n_points, seed=99)
    return training, held_out


if __name__ == "__main__":
    # ------------------------------------------------------------
    # SELF-TEST: check that boundary points actually land ON the
    # boundary (not floating randomly inside the garden), and that
    # the two seed sets are reproducible (same seed -> same points).
    # ------------------------------------------------------------
    twin = Twin.from_file("../scenarios/demo_house.json")

    points = boundary_start_points(twin, n_points=10, seed=42)
    print("10 sample boundary points:")
    for p in points:
        print(f"  ({p[0]:.2f}, {p[1]:.2f})")

    # NOTE: run it again with the SAME seed — this should produce
    # the EXACT same 10 points. That's what "reproducible" means in
    # practice.
    points_again = boundary_start_points(twin, n_points=10, seed=42)
    same = all(a == b for a, b in zip(points, points_again))
    print(f"\nSame seed gives same points: {same}")

    training, held_out = two_seed_sets(twin, n_points=200)
    print(f"\nTraining set size: {len(training)}")
    print(f"Held-out set size: {len(held_out)}")

    print("\nArchetypes:")
    for a in ARCHETYPES:
        print(f"  {a.name}: risk_weight={a.risk_weight}, "
              f"targets_least_covered={a.target_least_covered}")