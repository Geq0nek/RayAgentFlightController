import time
import datetime
import random
from flight_states import FlightState
from aircraft_generator import AircraftGenerator
from topology import AdjacencyMatrix

class FlightSimulator:
    def __init__(self, adjacency_matrix, max_flights=16, simulation_speed=60):
        """
        Initialize the real-time flight simulator.
        :param adjacency_matrix: Instance of AdjacencyMatrix with airport coordinates.
        :param max_flights: Max allowed concurrent flights.
        :param simulation_speed: 1 = real-time, 60 = 1s is 1min, 3600 = 1s is 1h.
        """
        self.matrix = adjacency_matrix
        self.max_flights = max_flights
        self.simulation_speed = simulation_speed
        
        self.all_flights = []
        self.generator = AircraftGenerator()
        self.available_airports = list(self.matrix.airports_data.keys())
        
        # Track the last time positions were updated
        self.last_update_time = time.time()

    @property
    def total_flights(self):
        """Returns the number of aircraft currently in the system."""
        return len(self.all_flights)

    def generate_random_connected_flight(self):
        """
        Creates a flight between two adjacent voivodeships using a loop 
        to avoid recursion errors.
        """
        if self.total_flights >= self.max_flights:
            return None

        neighbors = {}
        start_node = ""
        attempts = 0

        # Try to find a starting airport that actually has neighbors with airports
        while not neighbors and attempts < 50:
            start_node = random.choice(self.available_airports)
            start_voiv = self.matrix.airports_data[start_node]['voivodeship']
            neighbors = self.matrix.get_adjacent_airports(start_voiv)
            attempts += 1
        
        if neighbors:
            dest_node = random.choice(list(neighbors.keys()))
            return self.add_flight(start_node, dest_node)
        return None

    def add_flight(self, start, dest):
        """Initializes a new flight with unique ID and starting coordinates."""
        if self.total_flights < self.max_flights:
            # Generator ensures the ID is unique
            new_flight = self.generator.create_aircraft(start, dest)
            new_flight.state = FlightState.IN_FLIGHT
            
            # Initial GPS position
            start_coords = self.matrix.airports_data[start]
            new_flight.current_lat = start_coords['latitude']
            new_flight.current_lon = start_coords['longitude']
            
            self.all_flights.append(new_flight)
            return new_flight
        return None

    def update_positions(self):
        """
        Calculates movement based on the actual time passed multiplied by simulation speed.
        """
        current_time = time.time()
        # Delta in seconds (e.g., 1.0s)
        delta_seconds = current_time - self.last_update_time
        self.last_update_time = current_time
        
        # Convert to 'simulated' hours
        # (seconds * speed) / 3600 seconds in hour
        delta_hours = (delta_seconds * self.simulation_speed) / 3600

        for flight in self.all_flights:
            if flight.state != FlightState.IN_FLIGHT:
                continue

            dest_data = self.matrix.airports_data[flight.destination]
            dest_lat = dest_data['latitude']
            dest_lon = dest_data['longitude']

            # 1. Current distance to target in km
            dist_to_go = self.matrix._haversine_distance(
                flight.current_lat, flight.current_lon,
                dest_lat, dest_lon
            )

            # 2. Distance covered in this tick
            step_distance = flight.speed * delta_hours

            # 3. Handle Arrival
            if dist_to_go <= step_distance:
                flight.current_lat = dest_lat
                flight.current_lon = dest_lon
                flight.state = FlightState.ARRIVED
                flight.landing_date = datetime.datetime.now()
                print(f"\n[ATC] 🛬 {flight.id} landed safely at {flight.destination}")
            else:
                # 4. Movement (Linear interpolation of coordinates)
                ratio = step_distance / dist_to_go
                flight.current_lat += (dest_lat - flight.current_lat) * ratio
                flight.current_lon += (dest_lon - flight.current_lon) * ratio

    def cleanup_flights(self):
        """Removes arrived flights and frees their IDs."""
        for flight in self.all_flights[:]: # Iterate on a copy to allow removal
            if flight.state == FlightState.ARRIVED:
                self.generator.release_id(flight.id)
                self.all_flights.remove(flight)

    def get_active_flights(self):
        return [f for f in self.all_flights if f.state == FlightState.IN_FLIGHT]

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    try:
        # 1. Load your topology
        matrix = AdjacencyMatrix()
        
        # 2. Setup Simulator (simulation_speed=60 means 1s real = 1min sim)
        # Change speed to 1 for "True Real Life"
        sim = FlightSimulator(matrix, max_flights=10, simulation_speed=60)

        # 3. Spawn initial flights
        print("--- Initializing Radar ---")
        for _ in range(4):
            sim.generate_random_connected_flight()

        print(f"Simulation speed: {sim.simulation_speed}x (1s = {sim.simulation_speed}s of flight)")
        
        # 4. Main Loop
        while True:
            sim.update_positions()
            sim.cleanup_flights()
            
            # Print status in one line (Radar style)
            active = sim.get_active_flights()
            status_line = f"\r[FLIGHTS: {len(active)}] "
            
            for f in active:
                dest = matrix.airports_data[f.destination]
                d = matrix._haversine_distance(f.current_lat, f.current_lon, dest['latitude'], dest['longitude'])
                status_line += f"<---- {f.id}: {d:.2f}km to {f.destination} -> from {f.starting_point} -> started at: {f.start_date} -> speed: {f.speed} -> lat: {f.current_lat} - long: {f.current_lon} -> state: {f.state}\n"
            
            print(status_line, end="", flush=True)


            # Spawn new flight if there is space
            if sim.total_flights < sim.max_flights and random.random() > 0.6:
                sim.generate_random_connected_flight()

            time.sleep(1) # Refresh every second

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to close...")
    except KeyboardInterrupt:
        print("\nRadar shut down by operator.")