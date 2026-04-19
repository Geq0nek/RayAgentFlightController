from enum import Enum, auto

class FlightState(Enum):
    PARKED = auto()      
    TAKING_OFF = auto()  
    IN_FLIGHT = auto()   
    LANDING = auto()     
    ARRIVED = auto()     
    CANCELLED = auto()   
    DELAYED = auto()