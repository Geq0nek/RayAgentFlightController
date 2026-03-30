from random import choice
import datetime

class Aircraft:
    def __init__(self, aircraft_id, starting_point, destination):
        self.id = aircraft_id
        self.starting_point = starting_point
        self.destination = destination
        self.position = None
        self.speed = choice([300, 400])
        self.state = None
        self.staring_date = datetime.datetime.now()
        self.landing_date = None
        self.name = f'{self.id}_{self.starting_point}_{self.destination}_{self.staring_date}'

    def get_name(self):
        return self.name
