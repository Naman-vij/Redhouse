import json
import numpy as np
import shapely
from shapely.geometry import Polygon, Point

class Twin:
    def __init__(self, data):
        self.grid_m = data["grid_m"]
        self.boundary = Polygon(data["boundary"])
        self.obstacles = [Polygon(o["polygon"]) for o in data["obstacles"]]
        self.entrances = data["entrances"]
        # NOTE: two kinds of site.
        #   "outdoor" (default): a property with a house on it. The doors are
        #       what an intruder is trying to reach, and intruders start
        #       anywhere along the property edge.
        #   "indoor": a floor plan. `boundary` is the FLOOR (walls are carved
        #       out of it), `entrances` are the things to PROTECT (a server
        #       room, a freezer), and `entries` are the doors from outside,
        #       which is where intruders start.
        self.mode = data.get("mode", "outdoor")
        self.name = data.get("name") or data.get("site_id")
        self.entries = data.get("entries", [])
        minx, miny, maxx, maxy = self.boundary.bounds
        # NOTE: snap the grid to a fixed lattice (multiples of grid_m)
        # instead of starting it exactly at the boundary's corner.
        # Why: a plan traced from an image is never exact. Measured on the
        # demo house, a 5 cm change in width moved the grid half a cell and
        # swung the side-door result 63% -> 57% with nothing about the site
        # actually different. Snapping makes the answer depend on the site,
        # not on rounding in the outline.
        g = self.grid_m
        self.origin = (np.floor(minx / g) * g, np.floor(miny / g) * g)
        self.n_cols = int(np.ceil(maxx / g) - np.floor(minx / g))
        self.n_rows = int(np.ceil(maxy / g) - np.floor(miny / g))
        self.blocked = self._build_blocked_mask()

    def _build_blocked_mask(self):
        if self.mode == "indoor":
            return self._build_blocked_mask_indoor()
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

    def _build_blocked_mask_indoor(self):
        """
        Indoors a wall can be thinner than a grid cell, and a view or a
        walking route must not slip through it. So a cell only counts as
        FREE when the WHOLE cell lies on the floor and touches no obstacle.
        Walls therefore get at most one cell thicker, never thinner.
        """
        g = self.grid_m
        cols, rows = np.meshgrid(np.arange(self.n_cols), np.arange(self.n_rows))
        x0 = self.origin[0] + cols * g
        y0 = self.origin[1] + rows * g
        boxes = shapely.box(x0.ravel(), y0.ravel(), (x0 + g).ravel(), (y0 + g).ravel())
        shapely.prepare(self.boundary)
        free = shapely.contains(self.boundary, boxes)
        for ob in self.obstacles:
            shapely.prepare(ob)
            free &= ~shapely.intersects(ob, boxes)
        return (~free).reshape(self.n_rows, self.n_cols)

    def free_cell_near(self, x, y, max_radius_m=1.5):
        """(row, col) of the nearest walkable cell to a point, or None if none is that close."""
        r0, c0 = self.cell_of(x, y)
        reach = int(np.ceil(max_radius_m / self.grid_m))
        best, best_d = None, None
        for r in range(max(0, r0 - reach), min(self.n_rows, r0 + reach + 1)):
            for c in range(max(0, c0 - reach), min(self.n_cols, c0 + reach + 1)):
                if self.blocked[r, c]:
                    continue
                cx, cy = self._cell_center(r, c)
                d = float(np.hypot(cx - x, cy - y))
                if d <= max_radius_m and (best_d is None or d < best_d):
                    best, best_d = (r, c), d
        return best

    def _cell_center(self, row, col):
        return (self.origin[0] + (col + 0.5) * self.grid_m,
                self.origin[1] + (row + 0.5) * self.grid_m)

    def cell_of(self, x, y):
        col = int((x - self.origin[0]) / self.grid_m)
        row = int((y - self.origin[1]) / self.grid_m)
        return max(0, min(row, self.n_rows - 1)), max(0, min(col, self.n_cols - 1))

    @classmethod
    def from_file(cls, path):
        with open(path) as f:
            return cls(json.load(f))
