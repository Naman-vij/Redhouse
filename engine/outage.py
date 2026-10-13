"""
outage.py

What happens to security when a camera stops watching?

Ring tells us each camera's online status. An offline camera sees
nothing, so a door it used to cover can become reachable. This file
measures that, door by door, and writes it in plain words.

Everything is deterministic: same cameras and same attacker start
points always give the same numbers.
"""

from dataclasses import replace

from twin import Twin
from cameras import Camera
from defender import success_rate


def door_points(twin: Twin) -> dict[str, tuple[float, float]]:
    """{'front_door': (11.0, 4.0), ...} from the property's entrances."""
    return {e["id"]: tuple(e["point"]) for e in twin.entrances}


def with_online(cameras: list[Camera], camera_name: str, online: bool) -> list[Camera]:
    """
    A COPY of the list with one camera's online flag changed.

    NOTE: replace() copies the camera; the originals are never touched,
    so we can ask 'what if C1 failed?' without changing anything real.
    """
    return [replace(c, online=online) if c.name == camera_name else c for c in cameras]


def compare_states(twin: Twin, before: list[Camera], after: list[Camera],
                   start_points: list, risk_weight: float) -> list[dict]:
    """
    Attacker success rate at every door, under two camera states.

    Returns one entry per door, e.g.
      {"door": "front_door", "before": 0.0, "after": 0.25, "change": 0.25}
    """
    results = []
    for door_id, point in door_points(twin).items():
        b = success_rate(twin, before, point, start_points, risk_weight)
        a = success_rate(twin, after, point, start_points, risk_weight)
        results.append({"door": door_id, "before": b, "after": a, "change": a - b})
    return results


def outage_effect(twin: Twin, cameras: list[Camera], camera_name: str,
                  start_points: list, risk_weight: float) -> list[dict]:
    """What happens at each door if THIS camera goes offline."""
    after = with_online(cameras, camera_name, False)
    return compare_states(twin, cameras, after, start_points, risk_weight)


def single_points_of_failure(twin: Twin, cameras: list[Camera], start_points: list,
                             risk_weight: float, threshold: float = 0.10) -> list[dict]:
    """
    Which online cameras would leave a door noticeably more exposed if
    they failed? (A 'single point of failure'.)

    threshold: how many percentage points of extra attacker success
    counts as 'noticeably', as a fraction (0.10 = 10 points).
    Sorted worst first.
    """
    found = []
    for cam in cameras:
        if not cam.online:
            continue
        effects = outage_effect(twin, cameras, cam.name, start_points, risk_weight)
        worst = max(effects, key=lambda e: e["change"])
        if worst["change"] >= threshold:
            found.append({"camera": cam.name, "door": worst["door"],
                          "before": worst["before"], "after": worst["after"],
                          "change": worst["change"]})
    return sorted(found, key=lambda f: f["change"], reverse=True)


def describe_change(camera_name: str, results: list[dict], went_offline: bool = True) -> str:
    """
    One plain-English sentence built ONLY from the numbers in `results`.

    NOTE: nothing here is free-form text from an AI, so every figure in
    the sentence is guaranteed to match the simulator's output.

    NOTE: percentages are rounded ONCE, and the 'points' figure is the
    difference of those rounded numbers. Rounding each separately could
    print '62% to 94% (+31 points)', which doesn't add up.
    """
    verb = "went offline" if went_offline else "came back online"

    def pct(x: float) -> int:
        return round(x * 100)

    worse = [r for r in results if r["change"] > 0.0005]
    better = [r for r in results if r["change"] < -0.0005]
    if not worse and not better:
        return f"Camera {camera_name} {verb}. No door is affected."

    parts = []
    for r in sorted(worse, key=lambda r: r["change"], reverse=True):
        b, a = pct(r["before"]), pct(r["after"])
        parts.append(f"{r['door']} attacker success rises from {b}% to {a}% (+{a - b} points)")
    for r in sorted(better, key=lambda r: r["change"]):
        b, a = pct(r["before"]), pct(r["after"])
        parts.append(f"{r['door']} attacker success falls from {b}% to {a}% ({a - b} points)")
    return f"Camera {camera_name} {verb}: " + "; ".join(parts) + "."


if __name__ == "__main__":
    from attackers import two_seed_sets
    from cameras import make_demo_cameras

    twin = Twin.from_file("../scenarios/demo_house.json")
    training, _ = two_seed_sets(twin, 200)
    cameras = make_demo_cameras()

    print("What if each camera went offline?\n")
    for cam in cameras:
        effect = outage_effect(twin, cameras, cam.name, training, 0.5)
        print(" ", describe_change(cam.name, effect))

    print("\nSingle points of failure (10+ points at any door):")
    spofs = single_points_of_failure(twin, cameras, training, 0.5)
    if not spofs:
        print("  none")
    for f in spofs:
        print(f"  {f['camera']}: {f['door']} {f['before']:.0%} -> {f['after']:.0%}")
