"""
sync.py

Carries Ring's real "is this camera online?" answer into the engine.

THE IDEA
    Ring tells us a device's name, id and whether it is online. Ring does
    NOT tell us where the camera is mounted or which way it points - that
    comes from the site plan and the user (see engine/site_plan.py).

    So the user LINKS each engine camera to a Ring device:

        C1  ->  "Playground Device"

    Linked cameras take their online/offline state from Ring. Cameras
    with no link stay SIMULATED and keep whatever state they were given.
    Every result says which cameras are live and which are simulated, so
    nothing here can be mistaken for more than it is.

NOTE: this file does no network calls and no maths of its own. It only
copies a flag across and builds a sentence from numbers the simulator
already produced - so it is quick to test and cannot invent a figure.
"""

from dataclasses import replace

from app import engine_bridge  # noqa: F401  (puts engine/ on the path)
from cameras import Camera

from app.ring.reader import RingCamera


class LinkError(ValueError):
    """The user's camera <-> Ring device links don't make sense.

    The message is written for the person who typed the links, so the
    route can pass it straight back as a 400."""


# ----------------------------------------------------------------------
# 1. Reading the links the user typed
# ----------------------------------------------------------------------

def parse_links(raw_links: list[str]) -> dict[str, str]:
    """
    ["C1:Playground Device", "C3:ava1.ring.device.AAAA"]
        -> {"C1": "Playground Device", "C3": "ava1.ring.device.AAAA"}

    The text before the FIRST colon is the engine camera name; everything
    after it is the Ring device (its id, or its name).
    """
    links: dict[str, str] = {}
    for raw in raw_links:
        name, sep, ref = raw.partition(":")
        name, ref = name.strip(), ref.strip()
        if not sep or not name or not ref:
            raise LinkError(f"Bad link '{raw}'. Use  CameraName:RingDeviceNameOrId  "
                            "for example  C1:Playground Device")
        if name in links:
            raise LinkError(f"Camera {name} is linked twice.")
        links[name] = ref
    return links


# ----------------------------------------------------------------------
# 2. Finding the Ring device a link points at
# ----------------------------------------------------------------------

def find_ring_device(ref: str, ring_cameras: list[RingCamera]) -> RingCamera:
    """
    Match `ref` against Ring devices: exact id first, then name.

    NOTE: names are for people and may repeat, so a name that matches
    two devices is an ERROR, not a guess. A wrong match would hand one
    camera's outage to a different camera.
    """
    for dev in ring_cameras:
        if dev.device_id == ref:
            return dev

    by_name = [d for d in ring_cameras if d.name.strip().lower() == ref.lower()]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise LinkError(f"More than one Ring device is named '{ref}'. "
                        "Link it by its device id instead.")
    available = ", ".join(f"'{d.name}'" for d in ring_cameras) or "none"
    raise LinkError(f"No Ring device matches '{ref}'. Devices on this account: {available}.")


# ----------------------------------------------------------------------
# 3. Applying Ring's status to the engine cameras
# ----------------------------------------------------------------------

def apply_ring_status(cameras: list[Camera], ring_cameras: list[RingCamera],
                      links: dict[str, str]) -> tuple[list[Camera], list[dict]]:
    """
    Returns (cameras_with_live_status, report).

    - a linked camera gets online=<what Ring says>
    - an unlinked camera is returned unchanged and marked "simulated"
    - the report has one entry per camera, so the UI can label each as
      live or simulated

    NOTE: replace() makes a COPY, so the caller's original camera list is
    never touched.
    """
    known = {c.name for c in cameras}
    for name in links:
        if name not in known:
            raise LinkError(f"There is no camera '{name}' on this site. "
                            f"Cameras: {', '.join(sorted(known))}.")

    resolved = {name: find_ring_device(ref, ring_cameras) for name, ref in links.items()}

    seen: dict[str, str] = {}
    for name, dev in resolved.items():
        if dev.device_id in seen:
            raise LinkError(f"{seen[dev.device_id]} and {name} are both linked to "
                            f"'{dev.name}'. One Ring device is one physical camera.")
        seen[dev.device_id] = name

    updated, report = [], []
    for cam in cameras:
        dev = resolved.get(cam.name)
        if dev is not None:
            updated.append(replace(cam, online=dev.online))
            report.append({"name": cam.name, "source": "ring", "online": dev.online,
                           "reported_at": dev.reported_at, "ring_device_name": dev.name,
                           "ring_device_id": dev.device_id})
        else:
            updated.append(cam)
            report.append({"name": cam.name, "source": "simulated", "online": cam.online,
                           "reported_at": None, "ring_device_name": None,
                           "ring_device_id": None})
    return updated, report


def all_online(cameras: list[Camera]) -> list[Camera]:
    """The 'everything is working' version of the camera list (the baseline)."""
    return [replace(c, online=True) for c in cameras]


# ----------------------------------------------------------------------
# 4. One plain sentence about the current state
# ----------------------------------------------------------------------

def describe_state(cameras: list[Camera], results: list[dict]) -> str:
    """
    A sentence about the cameras' CURRENT state, built only from `results`
    (the per-door numbers the simulator returned).

    NOTE: no free-form text from an AI is used here, so every figure in
    the sentence matches the simulator's output. Percentages are rounded
    ONCE and the 'points' figure is the difference of the rounded numbers,
    so the sentence never contradicts itself (e.g. 0% -> 23% is +23).
    """
    offline = [c.name for c in cameras if not c.online]
    if not offline:
        return f"All {len(cameras)} cameras are online. No door is more exposed than normal."

    who = (f"Camera {offline[0]} is offline" if len(offline) == 1
           else f"Cameras {', '.join(offline)} are offline")

    def pct(x):
        return int(round(x * 100))

    worse, better = [], []
    for r in results:
        b, a = pct(r["before"]), pct(r["after"])
        if a > b:
            worse.append((a - b, f"{r['door']} attacker success rises from {b}% to {a}% (+{a - b} points)"))
        elif a < b:
            better.append((b - a, f"{r['door']} attacker success falls from {b}% to {a}% ({a - b} points)"))

    if not worse and not better:
        return f"{who}. No door is affected."
    parts = [text for _, text in sorted(worse, reverse=True)] + \
            [text for _, text in sorted(better, reverse=True)]
    return f"{who}: " + "; ".join(parts) + "."