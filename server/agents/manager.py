import ray
import time
from actor import AirTrafficAgent
from topology import AdjacencyMatrix
from aircraft_generator import AircraftGenerator

class SimulationManager:
    def __init__(self):
        # Initialize Ray locally
        ray.init(ignore_reinit_error=True)
        self.matrix = AdjacencyMatrix()
        self.generator = AircraftGenerator()
        self.agent_handles = {} # {voiv_name: actor_ref}

    def initialize_network(self):
        """Create an actor for every voivodeship defined in the topology."""
        voiv_list = list(self.matrix.adjacent_voivodeships.keys())

        # 1. Spawn all actors
        for name in voiv_list:
            self.agent_handles[name] = AirTrafficAgent.remote(name, self.matrix)

        # 2. Inject neighbors into each actor (Wiring the graph)
        for name in voiv_list:
            neighbor_names = self.matrix.adjacent_voivodeships[name]
            # Filter handles for neighbors that actually exist
            neighbors = {n: self.agent_handles[n] for n in neighbor_names if n in self.agent_handles}
            self.agent_handles[name].set_neighbors.remote(neighbors)
            
        print(f"System initialized with {len(self.agent_handles)} sector agents.")

    def spawn_flight(self):
        """Create a new flight and assign it to the starting sector agent."""
        # Use your logic to pick start/destination
        flight = self.generator.create_aircraft("WAW", "KRK") 
        start_voiv = self.matrix.airports_data["WAW"]["voivodeship"]
        
        # Inject GPS start coordinates
        coords = self.matrix.airports_data["WAW"]
        flight.current_lat = coords['latitude']
        flight.current_lon = coords['longitude']

        # Send flight to the first agent
        self.agent_handles[start_voiv].receive_flight.remote(flight)

    def start_simulation(self, sim_speed=60):
        """Continuous loop to drive the distributed simulation."""
        delta_hours = (1 * sim_speed) / 3600 # 1 second tick
        
        while True:
            # Trigger concurrent updates across all Ray actors
            # This is non-blocking; all agents calculate in parallel
            for agent in self.agent_handles.values():
                agent.update_step.remote(delta_hours)
            
            # Chance to spawn new traffic
            if len(self.agent_handles) > 0:
                self.spawn_flight()
                
            time.sleep(1)

if __name__ == "__main__":
    manager = SimulationManager()
    manager.initialize_network()
    manager.start_simulation()