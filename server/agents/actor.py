import ray
import asyncio
from flight_states import FlightState

@ray.remote
class AirTrafficAgent:
    def __init__(self, voivodeship_name, adjacency_matrix):
        """
        Initialize the Sector Agent.
        :param voivodeship_name: The territory this agent is responsible for.
        :param adjacency_matrix: Reference to the global topology.
        """
        self.voivodeship = voivodeship_name
        self.matrix = adjacency_matrix
        self.neighbors = {}  # Dictionary to store {name: actor_handle}
        self.local_flights = {}  # Local state: {flight_id: aircraft_object}

    def set_neighbors(self, neighbor_handles):
        """Link this actor to its surrounding neighbors."""
        self.neighbors = neighbor_handles

    async def receive_flight(self, flight):
        """
        RPC Method: Accept an incoming flight from a neighbor or manager.
        Fulfills the requirement: 'Agent source transfers info to agent target'.
        """
        flight.state = FlightState.IN_FLIGHT
        self.local_flights[flight.id] = flight
        print(f"[{self.voivodeship}] Aircraft {flight.id} entered sector. Destination: {flight.destination}")
        return True

    async def update_step(self, delta_hours):
        """
        Main logic: Update positions and check if aircraft are leaving the sector.
        Fulfills the requirement: 'Concurrent handling of neighbor connections'.
        """
        transfers = []
        landed = []

        for f_id, flight in list(self.local_flights.items()):
            # 1. Update GPS position using the shared haversine logic
            dest_coords = self.matrix.airports_data[flight.destination]
            dist_to_go = self.matrix._haversine_distance(
                flight.current_lat, flight.current_lon,
                dest_coords['latitude'], dest_coords['longitude']
            )
            
            step_dist = flight.speed * delta_hours
            
            # 2. Movement interpolation
            if dist_to_go > 0:
                ratio = step_dist / dist_to_go
                flight.current_lat += (dest_coords['latitude'] - flight.current_lat) * ratio
                flight.current_lon += (dest_coords['longitude'] - flight.current_lon) * ratio

            # 3. Check for landing
            if dist_to_go <= step_dist:
                landed.append(f_id)
                continue

            # 4. Sector Handover Logic
            # Check which voivodeship the aircraft is currently over
            current_sector = self._get_sector_at(flight.current_lat, flight.current_lon)
            
            if current_sector != self.voivodeship and current_sector in self.neighbors:
                transfers.append((f_id, current_sector))

        # Perform Handover: Remove from local state and send to neighbor
        for f_id, target_sector in transfers:
            flight_obj = self.local_flights.pop(f_id)
            target_agent = self.neighbors[target_sector]
            # Async RPC call to the neighbor
            target_agent.receive_flight.remote(flight_obj)
            print(f"[{self.voivodeship}] HANDOVER: {f_id} sent to {target_sector}")

        # Cleanup landed flights
        for f_id in landed:
            self.local_flights.pop(f_id)
            print(f"[{self.voivodeship}] TERMINATED: {f_id} reached destination.")

    def _get_sector_at(self, lat, lon):
        """Helper to determine which agent owns these coordinates."""
        # This calls your AdjacencyMatrix to find the nearest airport's voivodeship
        return self.matrix.get_voivodeship_by_coords(lat, lon)

    async def query_flight_location(self, flight_id, requester_name=None):
        """
        RPC Method: Search for a flight. If not found locally, ask neighbors.
        Fulfills the requirement: 'If agent doesn't know -> asks neighbors'.
        """
        if flight_id in self.local_flights:
            return f"Confirmed: {flight_id} is in {self.voivodeship}"
        
        # Prevent infinite loops in neighbor-to-neighbor queries
        print(f"[{self.voivodeship}] Query: {flight_id} not here. Asking neighbors...")
        
        # Async gathering of neighbor responses
        tasks = [
            n.query_flight_location.remote(flight_id, self.voivodeship) 
            for name, n in self.neighbors.items() if name != requester_name
        ]
        
        if not tasks: return "Not Found"
        
        results = await asyncio.gather(*tasks)
        for r in results:
            if "Confirmed" in r: return r
            
        return "Unknown"