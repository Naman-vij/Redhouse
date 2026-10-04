"""
cameras.py

Holds the Camera data type, and a builder for the 3-camera DEMO setup
used for testing until real Ring cameras are connected.
"""

from dataclasses import dataclass
import numpy as np


@dataclass
class Camera:
    """
    One camera. NOTE: footprint is the set of map points (in metres)
    this camera can see. In the real app it comes from calibration.py;
    for now the demo builder below fakes it with a wedge shape.
    """
    name: str
    position: tuple[float, float]
    footprint: np.ndarray
    max_range_m: float


def make_wedge_footprint(position, heading_deg, fov_deg, max_range_m,
                          n_points=3000, seed=0):
    """
    Fake a camera footprint as a pie-slice (wedge) pointing in one
    direction.

    heading_deg: direction the camera faces. 0 = east (+x),
                 90 = north (+y), 180 = west, 270 = south.
    fov_deg:     how wide the slice is (90 = a quarter circle).

    NOTE: unlike the filled circle we used before, a wedge has a
    DIRECTION. That's what makes rotating it meaningful - a circle
    looks identical when rotated, a wedge doesn't.
    """
    rng = np.random.default_rng(seed)
    radii = rng.uniform(0, max_range_m, n_points)
    angles = np.radians(heading_deg) + rng.uniform(
        -np.radians(fov_deg) / 2, np.radians(fov_deg) / 2, n_points)
    return np.column_stack([
        position[0] + radii * np.cos(angles),
        position[1] + radii * np.sin(angles),
    ])


def make_demo_cameras() -> list[Camera]:
    """
    Three cameras around the demo house, with ONE planted mistake:
    C2 is mounted at the south-west corner but faces east, so it
    watches the front garden and misses the west strip that leads to
    the side door. Rotating it counter-clockwise should fix that.

    NOTE: planting a known weakness is deliberate - it gives the
    defender something it SHOULD find. If it can't find a fix we
    planted ourselves, we know the search is broken.
    """
    rng_range = 9.0
    specs = [
        # name, position,   heading, fov
        ("C1", (11.0, 1.0),  90,  90),   # front, faces north at front door
        ("C2", (2.0, 2.0),    0,  90),   # south-west, WRONGLY faces east
        ("C3", (21.0, 8.0), 180,  90),   # east side, faces west at house
    ]
    cams = []
    for i, (name, pos, heading, fov) in enumerate(specs):
        fp = make_wedge_footprint(pos, heading, fov, rng_range, seed=i)
        cams.append(Camera(name, pos, fp, rng_range))
    return cams