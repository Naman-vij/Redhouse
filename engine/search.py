"""
search.py

Finds the lowest-risk route from anywhere on the property to a door.

Key idea: instead of searching once per starting point (slow — we'd
need to repeat this 200+ times for all our seeded attacker starts),
we run ONE search backwards from the door. This single search finds
the cheapest route from the door to EVERY cell in the grid at once.
Then checking "what's the best route from point X" is just a lookup,
not a new search.

The algorithm is Dijkstra's algorithm — a standard, well-established
shortest-path method. We're applying it with our own "cost" (distance
+ risk), not inventing new pathfinding maths.
"""

import heapq
import numpy as np
from twin import Twin


# NOTE: the 8 directions a step can move — up/down/left/right and
# the 4 diagonals. Diagonal steps are slightly longer in real
# distance (Pythagoras: sqrt(2) ≈ 1.414), so we record that too.
STEPS = [
    (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
    (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414),
]


def routes_to_target(twin: Twin,
                      target_row: int,
                      target_col: int,
                      risk: np.ndarray,
                      risk_weight: float) -> tuple[np.ndarray, dict]:
    """
    Run Dijkstra's algorithm BACKWARDS from one target cell (a door),
    finding the cheapest route from that door to every other cell.

    risk_weight (often called lambda): how much an attacker CARES
    about avoiding cameras vs. just taking the shortest route.
      - risk_weight = 0   -> attacker ignores cameras entirely,
                              just takes the shortest path
      - risk_weight = high -> attacker will walk a much longer route
                              if it means staying hidden

    Returns:
      cost: a grid the same shape as twin.blocked, where cost[r, c]
            is the cheapest total cost to reach the door FROM (r, c).
      came_from: a dictionary letting us reconstruct the actual route
                 (which cell leads to which), used later to draw the
                 path on screen.
    """
    cost = np.full((twin.n_rows, twin.n_cols), np.inf)
    came_from = {}

    cost[target_row, target_col] = 0.0

    # NOTE: a "priority queue" (heapq) always gives us the
    # cheapest-so-far cell to process next. This is what makes
    # Dijkstra efficient — we never waste time reprocessing a cell
    # once we know its true cheapest cost.
    queue = [(0.0, (target_row, target_col))]

    while queue:
        current_cost, (r, c) = heapq.heappop(queue)

        # NOTE: because a cell can be added to the queue more than
        # once (if we find a cheaper way to it later), we skip stale
        # entries — ones where a cheaper cost was already found since
        # this entry was queued.
        if current_cost > cost[r, c]:
            continue

        for dr, dc, step_length in STEPS:
            nr, nc = r + dr, c + dc

            if not (0 <= nr < twin.n_rows and 0 <= nc < twin.n_cols):
                continue  # off the edge of the grid
            if twin.blocked[nr, nc]:
                continue  # can't walk through a wall

            # NOTE: this is the actual cost formula — real distance
            # walked, PLUS a risk penalty scaled by how much the
            # attacker cares about being seen.
            step_cost = (step_length * twin.grid_m) + (risk_weight * risk[nr, nc])
            new_cost = current_cost + step_cost

            if new_cost < cost[nr, nc]:
                cost[nr, nc] = new_cost
                came_from[(nr, nc)] = (r, c)
                heapq.heappush(queue, (new_cost, (nr, nc)))

    return cost, came_from


def reconstruct_route(came_from: dict,
                       start: tuple[int, int],
                       target: tuple[int, int]) -> list[tuple[int, int]]:
    """
    Walk the came_from dictionary from a starting cell back to the
    target, to get the actual list of cells the attacker would cross.

    NOTE: came_from was built walking backwards FROM the target, so
    each entry says "to reach the target cheaply, from here, go to
    THIS neighbour next." Following it from `start` naturally walks
    forward toward the target.
    """
    route = [start]
    current = start

    while current != target:
        if current not in came_from:
            # NOTE: this means no route exists from `start` to the
            # target at all (e.g. start is unreachable, walled off).
            return []
        current = came_from[current]
        route.append(current)

    return route


if __name__ == "__main__":
    # ------------------------------------------------------------
    # SELF-TEST: with NO risk at all (risk_weight = 0), the cheapest
    # route should just be a straight line (or close to it) around
    # the house. We check this basic case works before trusting the
    # more complex risk-weighted version.
    # ------------------------------------------------------------
    twin = Twin.from_file("../scenarios/demo_house.json")

    # NOTE: a flat (all-zero) risk grid, since we're only testing the
    # PATHFINDING here, not coverage. We tested coverage separately
    # already, in coverage.py.
    zero_risk = np.zeros((twin.n_rows, twin.n_cols))

    front_door_row, front_door_col = twin.cell_of(11.0, 4.0)

    cost, came_from = routes_to_target(
        twin, front_door_row, front_door_col, zero_risk, risk_weight=0.0
    )

    # NOTE: pick a start point on the far side of the garden, away
    # from the door, so the route has to go AROUND the house.
    start_row, start_col = twin.cell_of(2.0, 16.0)

    print(f"Cost to reach front door from (2.0, 16.0): {cost[start_row, start_col]:.2f} m")

    route = reconstruct_route(came_from, (start_row, start_col),
                               (front_door_row, front_door_col))
    print(f"Route length: {len(route)} steps")
    print(f"First few cells: {route[:5]}")
    print(f"Last few cells: {route[-5:]}")