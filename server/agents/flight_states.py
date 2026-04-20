from enum import Enum, auto

class FlightState(Enum):
    PARKED = auto()      
    IN_FLIGHT = auto()     
    ARRIVED = auto()     