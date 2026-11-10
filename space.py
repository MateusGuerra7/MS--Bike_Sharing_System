"""
space.py - Grid, Zoning and Road Network
"""

from collections import deque
import random
from typing import Dict, List, Optional, Set, Tuple
import numpy as np
from mesa.space import MultiGrid
from constants import GRID_WIDTH, GRID_HEIGHT, ROAD_INTERVAL, BLOCK_SIZE

#passar o resto para constants
# Zones
ZONE_CENTRAL = "CENTRAL"
ZONE_UNIVERSITY = "UNIVERSITY"
ZONE_RESIDENTIAL = "RESIDENTIAL"

ZONE_NAMES = [ZONE_CENTRAL, ZONE_UNIVERSITY, ZONE_RESIDENTIAL]

ZONE_COLORS = {
    ZONE_CENTRAL: "#f4c76b",
    ZONE_UNIVERSITY: "#729fcf",
    ZONE_RESIDENTIAL: "#a8d5a2",
}

COLOR_ROAD = "#e0e0e0"
COLOR_INTERSECTION = "#9e9e9e"

def is_road(x: int, y: int) -> bool:
    """Returns True if the coordinate lies on a road corridor."""
    return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT and ((x % ROAD_INTERVAL == 0) or (y % ROAD_INTERVAL == 0))

def is_intersection(x: int, y: int) -> bool:
    """Returns True if the coordinate is a road intersection (station / depot anchor)."""
    return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT and (x % ROAD_INTERVAL == 0) and (y % ROAD_INTERVAL == 0)

def is_block(x: int, y: int) -> bool:
    """Returns True if the coordinate is an interior building block parcel."""
    return 0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT and not is_road(x, y)

def get_zone_at(x: int, y: int) -> str:
    """Returns the urban zone for any coordinate on the grid."""
    # University (FCUP and FAUP)
    if x <= 8 and 12 <= y <= 20:
        return ZONE_UNIVERSITY

    # University (Polo Universitário)
    if (x >= 28 and y >= 20) or (x >= 24 and y >= 24):
        return ZONE_UNIVERSITY

    # Central (Baixa, Aliados, São Bento, Cordoaria, Trindade)
    if (y <= 8 and 4 <= x <= 32) or (y <= 12 and 8 <= x <= 28) or (y <= 16 and 12 <= x <= 20):
        return ZONE_CENTRAL

    # Residential (Paranhos, Ramalde, Bonfim, Prelada, Foz)
    return ZONE_RESIDENTIAL
class CityRoadGrid(MultiGrid):
    """Porto Spatial Road Network with Cardinal Zoning."""

    def __init__(self, width: int = GRID_WIDTH, height: int = GRID_HEIGHT, torus: bool = False):
        super().__init__(width, height, torus)
        self.width = width
        self.height = height

        self.road_cells: Set[Tuple[int, int]] = set()
        self.intersection_cells: List[Tuple[int, int]] = []
        self.block_cells: Set[Tuple[int, int]] = set()

        self.zone_cells: Dict[str, List[Tuple[int, int]]] = {
            ZONE_CENTRAL: [],
            ZONE_UNIVERSITY: [],
            ZONE_RESIDENTIAL: [],
        }

        self.zone_map = np.zeros((height, width), dtype=object)
        self.rgb_matrix = np.zeros((height, width, 3), dtype=np.float32)

        for y in range(height):
            for x in range(width):
                zone = get_zone_at(x, y)
                self.zone_map[y, x] = zone
                self.zone_cells[zone].append((x, y))

                if is_road(x, y):
                    self.road_cells.add((x, y))
                    if is_intersection(x, y):
                        self.intersection_cells.append((x, y))
                    hex_col = COLOR_ROAD
                else:
                    self.block_cells.add((x, y))
                    hex_col = ZONE_COLORS[zone]

                self.rgb_matrix[y, x] = [
                    int(hex_col[1:3], 16) / 255.0,
                    int(hex_col[3:5], 16) / 255.0,
                    int(hex_col[5:7], 16) / 255.0,
                ]

        self.intersection_cells.sort(key=lambda pos: (pos[1], pos[0]))

        # Road network adjacency graph (links cells in the same road)
        self.road_adjacency: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for rx, ry in self.road_cells:
            neighbors = []
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = rx + dx, ry + dy
                if (nx, ny) in self.road_cells:
                    neighbors.append((nx, ny))
            self.road_adjacency[(rx, ry)] = neighbors


    # NOTE: until here, the methods and functions were solely to define the grid
    # NOTE: from here, the methods and functions are to be used when modelling the behaviour of agents 

    def get_road_neighbors(self, pos: Tuple[int, int]) -> List[Tuple[int, int]]:
        """For an agent, answers: what are the next possible cells (up, down, left, right)?"""
        return self.road_adjacency.get(pos, [])

    def snap_to_road(self, pos: Tuple[int, int]) -> Tuple[int, int]:
        """Snaps any coordinate (e.g. commuter spawn inside a block) to the nearest road cell."""
        x, y = pos
        if (x, y) in self.road_cells:
            return (x, y)

        rem_x, rem_y = x % ROAD_INTERVAL, y % ROAD_INTERVAL
        dist_left, dist_right = rem_x, ROAD_INTERVAL - rem_x
        dist_down, dist_up = rem_y, ROAD_INTERVAL - rem_y

        min_d = min(dist_left, dist_right, dist_down, dist_up)

        if min_d == dist_left and x - dist_left >= 0:
            return (x - dist_left, y)
        if min_d == dist_right and x + dist_right < self.width:
            return (x + dist_right, y)
        if min_d == dist_down and y - dist_down >= 0:
            return (x, y - dist_down)
        return (x, min(self.height - 1, y + dist_up))

    def get_shortest_road_path(self, start: Tuple[int, int], target: Tuple[int, int]) -> List[Tuple[int, int]]:
        """For an agent, answers: what's the shortest path from origin to destination?"""
        start_road = self.snap_to_road(start)
        target_road = self.snap_to_road(target)

        if start_road == target_road:
            return [start_road]

        queue = deque([start_road])
        came_from: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {start_road: None}

        while queue:
            current = queue.popleft()
            if current == target_road:
                break
            for neighbor in self.road_adjacency.get(current, []):
                if neighbor not in came_from:
                    came_from[neighbor] = current
                    queue.append(neighbor)

        if target_road not in came_from:
            return [start_road]

        path = []
        curr = target_road
        while curr is not None:
            path.append(curr)
            curr = came_from[curr]
        path.reverse()
        return path

    def get_manhattan_distance(self, a: Tuple[int, int], b: Tuple[int, int]) -> int:
        """Computes standard Manhattan distance (|x1 - x2| + |y1 - y2|)."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def get_zone_spawn_cell(self, zone_name: str, rng=None) -> Tuple[int, int]:
        """Samples a random cell within the requested urban zone."""
        cells = self.zone_cells.get(zone_name, [])
        if not cells:
            raise ValueError(f"Unknown zone: {zone_name}")
        if rng is not None and hasattr(rng, "choice"):
            return rng.choice(cells)
        return random.choice(cells)