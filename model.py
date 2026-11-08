from space import (
    CityRoadGrid,
    GRID_WIDTH,
    GRID_HEIGHT,
)
from typing import Optional
from agents import BikeStation, Bike, RebalancingVans, Person
import mesa

class BikeSharingModel(mesa.Model):
    """Central simulation coordinator"""

    def __init__(self, seed: Optional[int] = 42):
        super().__init__(rng=seed)
        self.width = GRID_WIDTH
        self.height = GRID_HEIGHT
        self.running = True #?

        # Initializing Grid
        self.grid = CityRoadGrid(width=self.width, height=self.height)

        # Clock (07:00 to 19:00, 720 mins)
        self.current_tick = 0
        self.shift_ticks = 720

        # Station, bike, and van entities will be plugged in here
        self.stations = []
        self.bikes = []
        self.vans = []

    #refactoring
    def spawn_users(self):
            hour = (self.steps // 60) % 24
            n = self.rng.poisson(self.arrival_rate * demand_factor(hour))
    
            if 7 <= hour <= 9:
                o_key, d_key = "generation", "attraction"   # casa -> centro
            elif 17 <= hour <= 19:
                o_key, d_key = "attraction", "generation"   # centro -> casa
            else:
                o_key, d_key = "generation", "attraction"
    
            for _ in range(n):
                origin, o_zone = self.pick_cell(o_key)
                dest, _ = self.pick_cell(d_key)
                self.requests[o_zone] += 1
    
                o_st = self.nearest_station(
                    origin, lambda s: s.bikes > 0
                    and manhattan(origin, s.xy) <= self.max_walk)
                d_st = self.nearest_station(dest)
                if o_st is None:               # longe de estações ou sem bicicletas
                    self.unmet += 1
                    self.unmet_by_zone[o_zone] += 1
                    self.add_flash(origin, RED)
                    continue
                if o_st is d_st:               # origem e destino na mesma estação
                    continue
    
                self.walk_minutes += self.walk_time(origin, o_st.xy)
                Person(self, origin, dest, o_st, d_st, o_zone)
    
    def step(self):
        """1-minute simulation step"""
        self.spawn_users()
        self.agents_by_type[Person].shuffle_do("step")
        #self.agents_by_type[RebalancingVans].do("step")
        if self.steps >= self.shift_ticks:
            self.running = False
    
