import json
import numpy as np
from shapely.geometry import Polygon, Point, box


class Twin:
    def __init__(self, data: dict):
        self.grid_m = data["grid_m"]
        self.boundary = Polygon(data["boundary"])
        self.obstacles = [Polygon(o["polygon"]) for o in data["obstacles"]]
        self.entrances = data["entrances"]

        minx, miny, maxx, maxy = self.boundary.bounds
        self.origin = (minx, miny)
        self.n_cols = int(np.ceil((maxx - minx) / self.grid_m))
        self.n_rows = int(np.ceil((maxy - miny) / self.grid_m))

        self.blocked = self._build_blocked_mask()

    def _build_blocked_mask(self) -> np.ndarray:
        blocked = np.zeros((self.n_rows, self.n_cols), dtype=bool)
        for r in range(self.n_rows):
            for c in range(self.n_cols):
                cx, cy = self._cell_center(r, c)
                pt = Point(cx, cy)
                if not self.boundary.contains(pt):
                    blocked[r, c] = True
                    continue
                for ob in self.obstacles:
                    if ob.contains(pt):
                        blocked[r, c] = True
                        break
        return blocked

    def _cell_center(self, row: int, col: int) -> tuple[float, float]:
        x = self.origin[0] + (col + 0.5) * self.grid_m
        y = self.origin[1] + (row + 0.5) * self.grid_m
        return x, y

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        """
                Convert a real-world (x, y) point in metres into a (row, col)
                grid cell.

                NOTE: points exactly on the outer edge of the property (e.g. a
                boundary-sampled point at the maximum y value) can compute to
                one cell PAST the last valid row/column, due to plain division
                rounding up to the next cell. We clamp the result so it never
                goes out of bounds — this doesn't change the geometry, it just
                keeps an edge point in the last valid cell instead of the
                nonexistent one just past it.
        """
        col = int((x - self.origin[0]) / self.grid_m)
        row = int((y - self.origin[1]) / self.grid_m)

        col = max(0, min(col, self.n_cols - 1))
        row = max(0, min(row, self.n_rows - 1))

        return row, col

    @classmethod
    def from_file(cls, path: str) -> "Twin":
        with open(path) as f:
            return cls(json.load(f))


if __name__ == "__main__":
    twin = Twin.from_file("../scenarios/demo_house.json")
    print(f"Grid size: {twin.n_rows} rows x {twin.n_cols} cols")
    print(f"Blocked cells: {twin.blocked.sum()} of {twin.blocked.size}")
