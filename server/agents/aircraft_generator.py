import random
import datetime
from flight_states import FlightState

class Aircraft:
    def __init__(self, aircraft_id, starting_point, destination):
        self.id = aircraft_id  # Unique ID (e.g., SP-ABC or LOT123)
        self.starting_point = starting_point
        self.destination = destination
        self.position = starting_point  
        self.speed = random.choice([400, 500])
        self.state = FlightState.PARKED
        self.start_date = datetime.datetime.now()
        self.landing_date = None
        
        # GPS coordinates for real-time tracking
        self.current_lat = 0.0
        self.current_lon = 0.0

    @property
    def name(self):
        """Dynamic generated name for logs."""
        return f"{self.id}: {self.starting_point} -> {self.destination}"

    def __repr__(self):
        # Using .name to show "PARKED" instead of Enum object
        return f"<{self.name} | State: {self.state.name}>"


class AircraftGenerator:
    def __init__(self):
        # Prefixes for different airlines
        self.prefixes = ['LOT', 'ENT', 'WZZ', 'RYR', 'LHT']
        # Set to keep track of IDs currently in use
        self.active_ids = set()

    def generate_unique_id(self):
        """Generates a flight ID that is not currently in use."""
        while True:
            prefix = random.choice(self.prefixes)
            number = random.randint(100, 9999)
            new_id = f"{prefix}{number}"
            
            if new_id not in self.active_ids:
                self.active_ids.add(new_id)
                return new_id

    def release_id(self, aircraft_id):
        """Call this when a flight finishes to make the ID available again."""
        if aircraft_id in self.active_ids:
            self.active_ids.remove(aircraft_id)

    def create_aircraft(self, start, dest):
        """Factory method to create an Aircraft with a guaranteed unique ID."""
        unique_id = self.generate_unique_id()
        return Aircraft(unique_id, start, dest)