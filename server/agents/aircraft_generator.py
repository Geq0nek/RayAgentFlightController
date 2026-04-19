import random
import datetime
import json
import os
from flight_states import FlightState


class Aircraft:
    def __init__(self, aircraft_id, starting_point, destination):
        self.id = aircraft_id  # Unique ID (e.g., SP-ABC or LOT123)
        self.starting_point = starting_point
        self.destination = destination
        self.position = starting_point
        self.speed = random.randint(400, 500)
        self.state = FlightState.PARKED
        self.start_date = datetime.datetime.now()
        self.landing_date = None
        self.height = random.randint(7000, 12000)
        self.actual_voivodeship = None

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
        self.voivodeship_boundaries = self._load_voivodeship_boundaries()

    def _load_voivodeship_boundaries(self):
        """Load voivodeship geometries from GeoJSON and cache them in memory."""
        geojson_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), '..', 'pol_admin_boundaries', 'pol_admin1_em.geojson')
        )

        try:
            with open(geojson_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            boundaries = []
            for feature in data.get('features', []):
                properties = feature.get('properties', {})
                geometry = feature.get('geometry', {})
                name = properties.get('adm1_name')
                if name and geometry:
                    boundaries.append((name, geometry))
            return boundaries
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _point_on_segment(px, py, x1, y1, x2, y2, eps=1e-12):
        """Return True if point lies on the segment defined by two vertices."""
        cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
        if abs(cross) > eps:
            return False

        dot = (px - x1) * (px - x2) + (py - y1) * (py - y2)
        return dot <= eps

    def _point_in_ring(self, lon, lat, ring):
        """Ray-casting test for a single linear ring."""
        inside = False
        if not ring:
            return False

        n = len(ring)
        for i in range(n):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % n]

            if self._point_on_segment(lon, lat, x1, y1, x2, y2):
                return True

            intersects = ((y1 > lat) != (y2 > lat))
            if intersects:
                xinters = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
                if lon < xinters:
                    inside = not inside

        return inside

    def _point_in_polygon(self, lon, lat, polygon_coords):
        """Return True if point is inside polygon (with optional holes)."""
        if not polygon_coords:
            return False

        outer_ring = polygon_coords[0]
        if not self._point_in_ring(lon, lat, outer_ring):
            return False

        for hole_ring in polygon_coords[1:]:
            if self._point_in_ring(lon, lat, hole_ring):
                return False

        return True

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

    def actual_voivodeship(self, aircraft):
        """Update and return current voivodeship based on aircraft GPS and GeoJSON boundaries."""

        lat = aircraft.current_lat
        lon = aircraft.current_lon
        aircraft.actual_voivodeship = None

        for voivodeship_name, geometry in self.voivodeship_boundaries:
            geometry_type = geometry.get('type')
            coordinates = geometry.get('coordinates', [])

            if geometry_type == 'Polygon':
                if self._point_in_polygon(lon, lat, coordinates):
                    aircraft.actual_voivodeship = voivodeship_name
                    return aircraft.actual_voivodeship

            elif geometry_type == 'MultiPolygon':
                for polygon_coords in coordinates:
                    if self._point_in_polygon(lon, lat, polygon_coords):
                        aircraft.actual_voivodeship = voivodeship_name
                        return aircraft.actual_voivodeship

        return aircraft.actual_voivodeship
