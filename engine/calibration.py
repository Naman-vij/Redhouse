"""
calibration.py

Turns a camera's snapshot into map coordinates.

The core idea: a homography is a formula built from 4 matching points
(same 4 real-world spots, clicked once in the photo, once on the map).
Once we have that formula, we can project ANY pixel from the photo onto
the map — that's how we work out what ground a camera actually covers,
without ever being told its angle or height.
"""

import cv2
import numpy as np


def fit_homography(img_pts: list[tuple[float, float]],
                    map_pts: list[tuple[float, float]]) -> np.ndarray:
    """
    Build the homography matrix H from matching points.

    img_pts: pixel coordinates in the snapshot, e.g. [(640, 480), ...]
    map_pts: the SAME real-world spots, in metres on our map, in the
             SAME ORDER as img_pts. Order matters — point 1 in img_pts
             must be the same physical spot as point 1 in map_pts.

    Returns: a 3x3 matrix H. This is the "formula" mentioned above.
    """
    # NOTE: cv2.findHomography needs at least 4 points, but works best
    # with more. For our MVP we ask the user to click exactly 4.
    img_arr = np.float32(img_pts)
    map_arr = np.float32(map_pts)

    H, _ = cv2.findHomography(img_arr, map_arr)
    # NOTE: the second return value is a "mask" of which points were
    # used cleanly — with only 4 points there's no outlier detection
    # to worry about, so we ignore it here.
    return H


def pixels_to_map(H: np.ndarray,
                   pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Project a batch of snapshot pixels onto the map, using H.

    pixels: an (N, 2) array of pixel coordinates.

    Returns:
      map_points: an (M, 2) array of the corresponding map coordinates
                   (in metres), where M <= N.
      keep_mask:  a boolean array saying which of the original N pixels
                   produced a valid map point (see NOTE below on why
                   some get dropped).
    """
    # NOTE: homography maths works in "homogeneous coordinates" — each
    # 2D point (x, y) is temporarily written as (x, y, 1) so the matrix
    # multiplication can also handle perspective (things further away
    # look smaller). This is standard computer-vision plumbing, not
    # something specific to our project.
    ones = np.ones((len(pixels), 1))
    homogeneous = np.hstack([pixels, ones])          # (N, 3)

    projected = homogeneous @ H.T                     # (N, 3)

    # NOTE: after projecting, the 3rd column ("w") tells us if the
    # point is valid ground or not. If w is zero or negative, that
    # pixel was ABOVE THE HORIZON in the photo (sky, or too far away
    # to make sense as "ground"), so we drop it.
    w = projected[:, 2]
    keep_mask = w > 1e-6

    map_points = projected[keep_mask, :2] / w[keep_mask, None]
    return map_points, keep_mask


def build_camera_footprint(H: np.ndarray,
                            image_width: int,
                            image_height: int,
                            camera_position: tuple[float, float],
                            max_range_m: float,
                            sample_every_px: int = 8) -> np.ndarray:
    """
    Work out which ground a camera can actually see, as a set of
    map points (its "footprint").

    We do this by sampling a grid of pixels across the whole snapshot
    (not every single pixel — that would be slow and pointless, since
    neighbouring pixels land almost on top of each other on the map),
    projecting each one, and keeping only the ones within max_range_m
    of the camera.
    """
    # NOTE: sample_every_px=8 means we check every 8th pixel in both
    # directions. For a 1280x720 snapshot that's roughly 160 x 90
    # sample points — plenty of detail, and fast to compute.
    xs = np.arange(0, image_width, sample_every_px)
    ys = np.arange(0, image_height, sample_every_px)
    grid_x, grid_y = np.meshgrid(xs, ys)
    pixels = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    map_points, _ = pixels_to_map(H, pixels)

    # NOTE: this is the "walls and obstacles don't matter yet" step —
    # we're only computing raw camera range here. Whether a wall
    # BLOCKS the view is handled later in coverage.py, using a
    # separate line-of-sight check against the twin's obstacles.
    cam = np.array(camera_position)
    distances = np.linalg.norm(map_points - cam, axis=1)
    within_range = map_points[distances <= max_range_m]

    return within_range


if __name__ == "__main__":
    # ------------------------------------------------------------
    # SELF-TEST: we build a homography we ALREADY KNOW the answer
    # to, so we can check our code is right before trusting it on
    # a real Ring snapshot. This mirrors the checklist item:
    # "Unit test with a synthetic camera where the true answer
    # is known."
    # ------------------------------------------------------------

    # Pretend: 4 points that form a 1-metre square on the ground,
    # and where those same 4 corners happen to land in a snapshot.
    # (These pixel numbers are made up but plausible for a photo.)
    img_pts = [(400, 500), (700, 500), (700, 300), (400, 300)]
    map_pts = [(0, 0), (1, 0), (1, 1), (0, 1)]

    H = fit_homography(img_pts, map_pts)
    print("Homography matrix:\n", H)

    # NOTE: if calibration worked, projecting the ORIGINAL 4 pixel
    # points should give us back very close to the original 4 map
    # points — this is the sanity check.
    check_points, kept = pixels_to_map(H, np.array(img_pts))
    print("\nRound-trip check (should match map_pts closely):")
    for original, result in zip(map_pts, check_points):
        print(f"  expected {original}  ->  got {tuple(round(v, 3) for v in result)}")