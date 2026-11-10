import mesa  # Mesa 3.x
from dataclasses import dataclass
from enum import Enum, auto
from constants import WALKING_SPEED, CYCLING_SPEED, VAN_SPEED, BATTERY_CONSUMPTION_RATE

class StationState(Enum):
    FULL = auto()   
    AVAILABLE = auto()             

class PersonState(Enum):
    IDLE_UNSPAWNED = auto()
    WALKING_TO_ORIGIN = auto()   
    WAITING_AT_STATION = auto()             
    RIDING = auto()        
    REROUTING_RIDING = auto()
    WALKING_TO_DEST = auto()      
    #rerouting bike?, satisfied   
    BALKED = auto()     

GREY, GREEN, RED = "#888888", "#2ca02c", "#d62728" #provosorio

#provosorio
STATE_COLORS = {
    PersonState.IDLE_UNSPAWNED: GREY,
    PersonState.WALKING_TO_ORIGIN: GREY,
    PersonState.WAITING_AT_STATION: GREY,
    PersonState.RIDING: GREEN,
    PersonState.REROUTING_RIDING: GREY,
    PersonState.WALKING_TO_DEST: GREEN, #satisfied
    PersonState.BALKED: RED,
}
         
class BikeStation(mesa.Agent):
    def __init__(self, model, zone_type, capacity, occupied_docks):
        super().__init__(model)
        self.capacity = capacity
        self.zone_type = zone_type
        self.occupied_docks = occupied_docks #passar para list e currentoad ser len(list)

    @property
    def free_docks(self):
        return self.capacity - self.occupied_docks

    @property
    def state(self):
        return StationState.FULL if self.occupied_docks == self.capacity else StationState.AVAILABLE

    @property
    def fill_ratio(self):
        return self.occupied_docks / self.capacity

    def take_bike(self):
        if self.occupied_docks > 0:
            self.occupied_docks -= 1
            return True
        return False

    def return_bike(self):
        if self.free_docks > 0:
            self.occupied_docks += 1
            return True
        return False

class Bike(mesa.Agent):
    def __init__(self, model, battery):
        super().__init__(model)
        self.battery = battery

    def use(self, distance):
        self.battery = max(0.0, self.battery - distance * BATTERY_CONSUMPTION_RATE)


class RebalancingVans(mesa.Agent):
    def __init__(self, model, max_capacity, current_load):
        super().__init__(model)
        self.max_capacity = max_capacity
        self.current_load = current_load  #passar para list e currentoad ser len(list)?

    @property
    def free_space(self):
        return self.max_capacity - self.current_load

    def pick_up(self, station, n):
        n = min(n, self.free_space, station.capacity)
        station.occupied_docks -= n
        self.current_load += n
        return n

    def drop_off(self, station, n):
        n = min(n, self.current_load, station.free_docks)
        station.occupied_docks += n
        self.current_load -= n
        return n

        #policy rebalancing
        #step

class Person(mesa.Agent):
    def __init__(self, model, origin, dest, o_station, d_station, patience_threshold, timer):
        super().__init__(model)
        self.origin = origin
        self.dest = dest
        self.o_station = o_station
        self.d_station = d_station
        self.patience_threshold = patience_threshold
        self.walking_distance = 0.0
        self.waiting_time = 0
        self.state = PersonState.IDLE_UNSPAWNED 
        self.timer = timer 

    @property
    def color(self): 
        return STATE_COLORS[self.state]

    @property
    def target(self):
        return {
            PersonState.WALKING_TO_ORIGIN: self.o_station.pos,
            PersonState.RIDING: self.d_station.pos,
            PersonState.REROUTING_RIDING: self.d_station.pos, #?
            PersonState.WALKING_TO_DEST: self.dest,
        }.get(self.state)

    @property
    def speed(self):
        return CYCLING_SPEED if self.state == PersonState.RIDING else WALKING_SPEED

    #refactor
    def _step(self, target):
        if self.pos != self.target:
            #self.model.grid.move_agent(self, self.model.next_cell(self.pos, self.target)) #nao usa speed
            return False
        return True
      
    def step(self):
        match self.state:
            case PersonState.IDLE_UNSPAWNED:
                #function
                self.state = PersonState.WALKING_TO_ORIGIN 
            case PersonState.WALKING_TO_ORIGIN:
                if self._step(self.target):
                    if self.o_station.take_bike():
                        self.state = PersonState.RIDING
                    else:        
                        self.state = PersonState.WAITING_AT_STATION             
            case PersonState.WAITING_AT_STATION:
                if self.o_station.take_bike():
                    self.state = PersonState.RIDING
                else:
                    self.waiting_time += 1
                    if self.waiting_time >= self.patience_threshold:
                        self.state = PersonState.BALKED
                        #balked +=1
            case PersonState.RIDING: #falta recompensa
                if self._step(self.target):
                    if self.d_station.return_bike():
                        self.state = PersonState.WALKING_TO_DEST
                    else:   
                        #waiting?
                        self.state = PersonState.REROUTING_RIDING
            case PersonState.REROUTING_RIDING:
                if self._step(self.target):
                    if self.d_station.return_bike():
                        self.state = PersonState.WALKING_TO_DEST
                    else:
                        #rerouting usar func de mais perto
                        self.state = PersonState.BALKED
                        #balked +=1
            case PersonState.WALKING_TO_DEST:             
                if self._step(self.target):
                    self.remove()      
            case PersonState.BALKED:
                self.remove()  #?