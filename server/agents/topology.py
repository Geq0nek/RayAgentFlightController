import networkx as nx
import yaml
import os
from math import radians, sin, cos, sqrt, atan2

class AdjacencyMatrix:
    def __init__(self):
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        self.yaml_file = os.path.join(base_dir, 'configuration', 'airports_informations.yaml')
        self.voivodeships_data = self._load_voivodeships()
        self.airports_data = self._flatten_airports()
        self.adjacent_voivodeships = self._find_adjacent_voivodeships()

    def _load_voivodeships(self):
        """Load voivodeship data from YAML file."""
        if not os.path.exists(self.yaml_file):
            raise FileNotFoundError(f"YAML file not found: {self.yaml_file}")
        
        with open(self.yaml_file, 'r') as f:
            yaml_data = yaml.load(f, Loader=yaml.FullLoader)
        
        return yaml_data.get('voivodeships', {})

    def _flatten_airports(self):
        """Create a flat list of all airports with their voivodeship info."""
        airports = {}
        for voiv_name, voiv_airports in self.voivodeships_data.items():
            for airport in voiv_airports:
                airport_code = airport.get('iata_code', 'UNKNOWN')
                airports[airport_code] = {
                    'name': airport.get('name', 'Unknown'),
                    'latitude': airport['location']['latitude'],
                    'longitude': airport['location']['longitude'],
                    'voivodeship': voiv_name
                }
        return airports

    @staticmethod
    def _haversine_distance(lat1, lon1, lat2, lon2):
        """Calculate distance between two points in km using Haversine formula."""
        R = 6371  # Earth's radius in km
        
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        
        return R * c

    def _find_adjacent_voivodeships(self):
        """Return static adjacency map of Polish voivodeships."""
        # Static adjacency based on geographical borders in Poland
        return {
            'dolnoslaskie': ['opolskie', 'slaskie', 'wielkopolskie', 'lubuskie'],
            'kujawsko_pomorskie': ['pomorskie', 'warminsko_mazurskie', 'mazowieckie', 'wielkopolskie'],
            'lubelskie': ['mazowieckie', 'podlaskie', 'swietokrzyskie', 'podkarpackie'],
            'lubuskie': ['wielkopolskie', 'dolnoslaskie', 'zachodniopomorskie'],
            'lodzkie': ['mazowieckie', 'swietokrzyskie', 'opolskie', 'wielkopolskie'],
            'malopolskie': ['slaskie', 'swietokrzyskie', 'podkarpackie'],
            'mazowieckie': ['podlaskie', 'warminsko_mazurskie', 'kujawsko_pomorskie', 'lodzkie', 'swietokrzyskie', 'lubelskie'],
            'opolskie': ['slaskie', 'dolnoslaskie', 'lodzkie', 'swietokrzyskie'],
            'podkarpackie': ['malopolskie', 'swietokrzyskie', 'lubelskie', 'podlaskie'],
            'podlaskie': ['warminsko_mazurskie', 'mazowieckie', 'lubelskie', 'podkarpackie'],
            'pomorskie': ['zachodniopomorskie', 'kujawsko_pomorskie', 'warminsko_mazurskie'],
            'slaskie': ['opolskie', 'malopolskie', 'swietokrzyskie', 'lodzkie', 'dolnoslaskie'],
            'swietokrzyskie': ['mazowieckie', 'lodzkie', 'opolskie', 'slaskie', 'malopolskie', 'podkarpackie', 'lubelskie'],
            'warminsko_mazurskie': ['podlaskie', 'mazowieckie', 'kujawsko_pomorskie', 'pomorskie'],
            'wielkopolskie': ['zachodniopomorskie', 'lubuskie', 'dolnoslaskie', 'lodzkie', 'kujawsko_pomorskie'],
            'zachodniopomorskie': ['wielkopolskie', 'lubuskie', 'pomorskie']
        }

    def get_adjacent_airports(self, voivodeship_name):
        """
        Get all airports in adjacent voivodeships with their distances from airports in given voivodeship.
        Returns: {airport_code: {'name': ..., 'distances': {neighbor_airport: km, ...}, 'voivodeship': ...}}
        """
        if voivodeship_name not in self.adjacent_voivodeships:
            return {}
        
        result = {}
        
        # Get airports in the given voivodeship
        source_airports = self.voivodeships_data.get(voivodeship_name, [])
        source_codes = [a.get('iata_code') for a in source_airports]
        
        # Get adjacent voivodeships
        adjacent_voivs = self.adjacent_voivodeships[voivodeship_name]
        
        # For each adjacent voivodeship, get its airports
        for adj_voiv in adjacent_voivs:
            adj_airports = self.voivodeships_data.get(adj_voiv, [])
            
            for adj_airport in adj_airports:
                adj_code = adj_airport.get('iata_code', 'UNKNOWN')
                adj_lat = adj_airport['location']['latitude']
                adj_lon = adj_airport['location']['longitude']
                
                # Calculate distances from all source airports to this adjacent airport
                distances = {}
                for source_airport in source_airports:
                    src_code = source_airport.get('iata_code', 'UNKNOWN')
                    src_lat = source_airport['location']['latitude']
                    src_lon = source_airport['location']['longitude']
                    
                    dist = self._haversine_distance(src_lat, src_lon, adj_lat, adj_lon)
                    distances[src_code] = round(dist, 2)
                
                result[adj_code] = {
                    'name': adj_airport.get('name', 'Unknown'),
                    'voivodeship': adj_voiv,
                    'latitude': adj_lat,
                    'longitude': adj_lon,
                    'distances_from_source': distances  # distances from each airport in source voiv
                }
        
        return result

    def print_adjacency_for_voivodeship(self, voivodeship_name):
        """Pretty print adjacency info for a voivodeship."""
        print(f"\n{'='*70}")
        print(f"Voivodeship: {voivodeship_name}")
        print(f"{'='*70}")
        
        adjacent_voivs = self.adjacent_voivodeships.get(voivodeship_name, [])
        print(f"Adjacent voivodeships: {', '.join(adjacent_voivs) if adjacent_voivs else 'None'}")
        
        source_airports = self.voivodeships_data.get(voivodeship_name, [])
        print(f"Airports in {voivodeship_name}:")
        for airport in source_airports:
            code = airport.get('iata_code', 'UNKNOWN')
            name = airport.get('name', 'Unknown')
            print(f"  • {code}: {name}")
        
        adjacent_airports = self.get_adjacent_airports(voivodeship_name)
        
        if not adjacent_airports:
            print(f"\nNo adjacent voivodeships with airports.")
            return
        
        print(f"\nAdjacent airports (with distances from {voivodeship_name} airports):")
        for adj_code, info in sorted(adjacent_airports.items()):
            print(f"\n  [{info['voivodeship']}] {adj_code}: {info['name']}")
            for src_code, distance in info['distances_from_source'].items():
                print(f"      → From {src_code}: {distance} km")


if __name__ == "__main__":
    print("Topology & Adjacency Matrix initialized")
    print(f"networkx {nx.__version__}\n")
    
    try:
        matrix = AdjacencyMatrix()
        
        print(f"Total airports loaded: {len(matrix.airports_data)}")
        print(f"Total voivodeships: {len(matrix.voivodeships_data)}")
        
        # Print adjacency for selected voivodeships
        for voiv_name in sorted(matrix.voivodeships_data.keys())[:3]:  # Show first 3
            matrix.print_adjacency_for_voivodeship(voiv_name)
    
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    
    input("\nPress ENTER to exit...")