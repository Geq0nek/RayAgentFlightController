"""
actor.py — Ray-based distributed ATC network for Polish voivodeships.

Architecture
------------
SimulationClock (Ray actor)
    Singleton clock shared across all voivodeship actors.
    Advances wall-time × simulation_speed and issues (sim_time, tick, delta_hours)
    on every call to advance().

VoivodeshipActor (Ray actor)
    One instance per voivodeship.  Acts as the ATC control tower for that region.
    On each tick:
      1. Receives (sim_time, tick, delta_hours) from the manager.
      2. Moves every tracked aircraft along its great-circle route.
      3. Detects boundary crossings via GeoJSON point-in-polygon.
      4. Hands off aircraft that crossed the boundary to the correct
         neighbouring actor using an awaited Ray call — the handoff carries
         the same (sim_time, tick) stamp to guarantee causal ordering.
      5. Returns a tick summary to the manager.

Communication contract
----------------------
  manager calls:   clock.advance()          → (sim_time, tick, delta_hours)
  manager calls:   actor.update.remote(...) → TickSummary   (all actors in parallel)
  actor A awaits:  actor_B.accept_aircraft.remote(aircraft, sim_time, tick)
                                             → bool          (confirmed within the tick)
"""

from __future__ import annotations

import datetime
import logging
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import ray

# ---------------------------------------------------------------------------
# GeoJSON → topology key mapping
# ---------------------------------------------------------------------------
# The GeoJSON boundary file uses Polish names with diacritics and Title Case,
# while the YAML topology (and actor names) use simplified ASCII-lowercase keys.
# This mapping is the single authoritative translation between the two systems.
_GEOJSON_TO_TOPOLOGY_KEY: Dict[str, str] = {
    "Dolnośląskie":       "dolnoslaskie",
    "Kujawsko-Pomorskie": "kujawsko_pomorskie",
    "Lubelskie":          "lubelskie",
    "Lubuskie":           "lubuskie",
    "Łódzkie":            "lodzkie",
    "Małopolskie":        "malopolskie",
    "Mazowieckie":        "mazowieckie",
    "Opolskie":           "opolskie",
    "Podkarpackie":       "podkarpackie",
    "Podlaskie":          "podlaskie",
    "Pomorskie":          "pomorskie",
    "Śląskie":            "slaskie",
    "Świętokrzyskie":     "swietokrzyskie",
    "Warmińsko-Mazurskie":"warminsko_mazurskie",
    "Wielkopolskie":      "wielkopolskie",
    "Zachodniopomorskie": "zachodniopomorskie",
}


# ---------------------------------------------------------------------------
# Simulation Clock
# ---------------------------------------------------------------------------

@ray.remote
class SimulationClock:
    """
    Centralised simulation clock shared by the entire voivodeship network.

    All time stamps injected into actor calls originate from this single
    source of truth, so every actor operates on the same logical time.
    """

    def __init__(self, simulation_speed: float = 60.0) -> None:
        """
        :param simulation_speed:
            Time multiplier.  1 = real-time, 60 = 1 real second equals
            1 simulated minute, 3600 = 1 real second equals 1 simulated hour.
        """
        self.simulation_speed: float = simulation_speed
        self.sim_time: float = 0.0   # accumulated simulated seconds
        self.tick: int = 0
        self._last_wall: float = time.time()

    # ------------------------------------------------------------------

    def advance(self) -> Tuple[float, int, float]:
        """
        Advance the clock by the elapsed real-world time × simulation_speed.

        :returns: (sim_time, tick, delta_hours)
            sim_time    – total simulated seconds since start
            tick        – monotonically increasing tick counter
            delta_hours – simulated hours elapsed since the previous advance()
        """
        now = time.time()
        delta_wall: float = now - self._last_wall
        self._last_wall = now

        delta_sim_seconds = delta_wall * self.simulation_speed
        self.sim_time += delta_sim_seconds
        self.tick += 1

        delta_hours = delta_sim_seconds / 3600.0
        return self.sim_time, self.tick, delta_hours

    def get_time(self) -> Tuple[float, int]:
        """Return (sim_time, tick) without advancing the clock."""
        return self.sim_time, self.tick

    def reset(self) -> None:
        """Reset the clock to zero (useful for tests)."""
        self.sim_time = 0.0
        self.tick = 0
        self._last_wall = time.time()


# ---------------------------------------------------------------------------
# Serialisable aircraft snapshot
# ---------------------------------------------------------------------------

@dataclass
class AircraftSnapshot:
    """
    Lightweight, fully serialisable snapshot of an aircraft's state.
    Used for API responses and cross-actor messages.
    """
    id: str
    starting_point: str
    destination: str
    current_lat: float
    current_lon: float
    speed: float
    height: int
    state: object            # FlightState enum value
    start_date: datetime.datetime
    landing_date: Optional[datetime.datetime]
    actual_voivodeship: Optional[str]

    @classmethod
    def from_aircraft(cls, aircraft) -> "AircraftSnapshot":
        return cls(
            id=aircraft.id,
            starting_point=aircraft.starting_point,
            destination=aircraft.destination,
            current_lat=aircraft.current_lat,
            current_lon=aircraft.current_lon,
            speed=aircraft.speed,
            height=aircraft.height,
            state=aircraft.state,
            start_date=aircraft.start_date,
            landing_date=aircraft.landing_date,
            actual_voivodeship=aircraft.actual_voivodeship,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "starting_point": self.starting_point,
            "destination": self.destination,
            "current_lat": self.current_lat,
            "current_lon": self.current_lon,
            "speed": self.speed,
            "height": self.height,
            "state": self.state.name if hasattr(self.state, "name") else str(self.state),
            "start_date": self.start_date.isoformat(),
            "landing_date": self.landing_date.isoformat() if self.landing_date else None,
            "actual_voivodeship": self.actual_voivodeship,
        }


# ---------------------------------------------------------------------------
# Tick summary returned by update()
# ---------------------------------------------------------------------------

@dataclass
class TickSummary:
    """Summary of what happened in a single update tick for one voivodeship."""
    voivodeship: str
    tick: int
    sim_time: float
    active_count: int
    handed_off: List[str] = field(default_factory=list)
    handoff_targets: Dict[str, str] = field(default_factory=dict)
    arrived: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "voivodeship": self.voivodeship,
            "tick": self.tick,
            "sim_time": self.sim_time,
            "active_count": self.active_count,
            "handed_off": self.handed_off,
            "handoff_targets": self.handoff_targets,
            "arrived": self.arrived,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Voivodeship Actor
# ---------------------------------------------------------------------------

@ray.remote
class VoivodeshipActor:
    """
    Ray Actor representing a single Polish voivodeship ATC control tower.

    Lifecycle
    ---------
    1.  Instantiated by the manager with topology + generator references.
    2.  ``register_neighbors()`` is called once after all actors exist so that
        each actor holds direct Ray handles to its geographical neighbours.
    3.  On every simulation tick the manager calls ``update()`` on all actors
        concurrently (``ray.get([a.update.remote(...) for a in actors])``).
    4.  Within ``update()``, aircraft that cross a border trigger an awaited
        ``neighbor.accept_aircraft.remote(...)`` call so the handoff is committed
        before the tick completes.
    5.  Each actor publishes a compact local snapshot to NeighborInfoService,
        so adjacent actors can inspect the latest known neighbour activity.

    Time synchronisation
    --------------------
    Every inter-actor message carries (sim_time, tick) taken from the shared
    SimulationClock, ensuring causal ordering across the entire network.
    """

    def __init__(
        self,
        name: str,
        topology,          # AdjacencyMatrix instance
        generator,         # AircraftGenerator instance
        simulation_speed: float = 60.0,
        neighbor_info_service=None,
        database_log_service=None,
        log_capacity: int = 500,
    ) -> None:
        """
        :param name:              Voivodeship identifier (matches YAML / adjacency keys).
        :param topology:          ``AdjacencyMatrix`` — provides airports_data, haversine,
                                  and the static adjacency map.
        :param generator:         ``AircraftGenerator`` — provides point-in-polygon
                                  voivodeship detection.
        :param simulation_speed:  Inherited from FlightSimulator / SimulationClock.
        :param log_capacity:      Maximum number of log entries kept in memory.
        """
        self.name: str = name
        self._topology = topology
        self._generator = generator
        self.simulation_speed: float = simulation_speed
        self._neighbor_info_service = neighbor_info_service
        self._database_log_service = database_log_service

        # Neighbour voivodeship names derived from the static adjacency map
        self.adjacent_names: List[str] = topology.adjacent_voivodeships.get(name, [])

        # aircraft_id -> Aircraft live object (owns the mutable state)
        self._aircraft: Dict[str, object] = {}

        # Handles to neighbouring VoivodeshipActor Ray objects.
        # Populated via register_neighbors() after all actors are created.
        self._neighbors: Dict[str, "VoivodeshipActor"] = {}

        # Latest published snapshots fetched from neighbouring towers.
        self._neighbor_activity: Dict[str, dict] = {}

        # Internal event log
        self._log: List[str] = []
        self._log_capacity: int = log_capacity

        self._logger = logging.getLogger(f"ATC.{name}")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def register_neighbors(
        self,
        neighbor_handles: Dict[str, "VoivodeshipActor"],
    ) -> None:
        """
        Store Ray actor handles to all geographically adjacent voivodeships.
        Must be called once by the manager after all actors have been created.

        :param neighbor_handles: {voivodeship_name: Ray actor handle}
        """
        self._neighbors = neighbor_handles
        self._append_log(
            f"[INIT] Registered {len(neighbor_handles)} neighbors: "
            f"{sorted(neighbor_handles.keys())}",
            tick=0,
            sim_time=0.0,
            event_type="NEIGHBORS_REGISTERED",
            payload={"neighbors": sorted(neighbor_handles.keys())},
        )
        await self._publish_neighbor_snapshot(tick=0, sim_time=0.0)

    # ------------------------------------------------------------------
    # Aircraft management — inbound
    # ------------------------------------------------------------------

    async def accept_aircraft(
        self,
        aircraft,
        sim_time: float,
        tick: int,
    ) -> bool:
        """
        Receive an aircraft handed off by a neighbouring tower.

        Awaited by the sending actor so that ownership transfer is atomic within
        the current tick.

        :param aircraft:  Aircraft instance (serialised by Ray / pickle).
        :param sim_time:  Simulation time at which the transfer occurred.
        :param tick:      Clock tick at which the transfer occurred.
        :returns:         True on success.
        """
        previous_voivodeship = aircraft.actual_voivodeship
        aircraft.actual_voivodeship = self.name
        self._aircraft[aircraft.id] = aircraft

        msg = (
            f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
            f"ACCEPT  {aircraft.id:>10} : "
            f"{previous_voivodeship} → {self.name} "
            f"(dest={aircraft.destination})"
        )
        self._append_log(
            msg,
            tick=tick,
            sim_time=sim_time,
            event_type="AIRCRAFT_ACCEPTED",
            source_voivodeship=previous_voivodeship,
            target_voivodeship=self.name,
            flight_id=aircraft.id,
            payload={
                "destination": aircraft.destination,
                "starting_point": aircraft.starting_point,
            },
        )
        self._logger.info(msg)
        await self._publish_neighbor_snapshot(tick=tick, sim_time=sim_time)
        return True

    async def add_aircraft(
        self,
        aircraft,
        sim_time: float = 0.0,
        tick: int = 0,
    ) -> None:
        """
        Inject a newly spawned aircraft into this tower's area of responsibility.
        Called by the manager when a flight originates in this voivodeship.

        :param aircraft:  Fresh Aircraft instance from AircraftGenerator.
        :param sim_time:  Current simulation time.
        :param tick:      Current clock tick.
        """
        aircraft.actual_voivodeship = self.name
        self._aircraft[aircraft.id] = aircraft
        msg = (
            f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
            f"SPAWN   {aircraft.id:>10} : "
            f"{aircraft.starting_point} → {aircraft.destination}"
        )
        self._append_log(
            msg,
            tick=tick,
            sim_time=sim_time,
            event_type="AIRCRAFT_SPAWNED",
            flight_id=aircraft.id,
            payload={
                "starting_point": aircraft.starting_point,
                "destination": aircraft.destination,
                "speed": aircraft.speed,
                "height": aircraft.height,
            },
        )
        self._logger.info(msg)
        await self._publish_neighbor_snapshot(tick=tick, sim_time=sim_time)

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    async def update(
        self,
        sim_time: float,
        tick: int,
        delta_hours: float,
    ) -> TickSummary:
        """
        Execute one simulation tick for this voivodeship.

        Steps
        -----
        1. For every IN_FLIGHT aircraft tracked here, move it along its
           great-circle route by ``speed × delta_hours`` km.
        2. Detect whether the new position is still inside this voivodeship
           using GeoJSON point-in-polygon (AircraftGenerator.actual_voivodeship).
        3. If the aircraft crossed into a neighbouring voivodeship, await
           ``neighbor.accept_aircraft.remote(...)`` so the receiving tower
           confirms ownership before this tick returns.
        4. If the aircraft has reached its destination, mark it ARRIVED.

        :param sim_time:    Current simulation time in seconds (from SimulationClock).
        :param tick:        Current clock tick (from SimulationClock).
        :param delta_hours: Simulated hours elapsed since the previous tick.
        :returns:           TickSummary with counts and lists of events.
        """
        from flight_states import FlightState  # local import avoids circular issues

        summary = TickSummary(
            voivodeship=self.name,
            tick=tick,
            sim_time=sim_time,
            active_count=0,
        )

        await self._refresh_neighbor_activity(tick=tick, sim_time=sim_time)

        airports_data = self._topology.airports_data

        for aircraft_id, aircraft in list(self._aircraft.items()):
            if aircraft.state != FlightState.IN_FLIGHT:
                continue

            dest_data = airports_data.get(aircraft.destination)
            if dest_data is None:
                warning = f"Unknown destination {aircraft.destination} for {aircraft_id}"
                summary.warnings.append(warning)
                self._append_log(
                    warning,
                    tick=tick,
                    sim_time=sim_time,
                    event_type="UNKNOWN_DESTINATION",
                    flight_id=aircraft_id,
                    payload={"destination": aircraft.destination},
                )
                continue

            dest_lat: float = dest_data["latitude"]
            dest_lon: float = dest_data["longitude"]

            dist_to_go: float = self._topology._haversine_distance(
                aircraft.current_lat,
                aircraft.current_lon,
                dest_lat,
                dest_lon,
            )
            step_distance: float = aircraft.speed * delta_hours
            self._append_log(
                f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                f"TRACK   {aircraft_id:>10} : {dist_to_go:.2f}km to {aircraft.destination}",
                tick=tick,
                sim_time=sim_time,
                event_type="AIRCRAFT_TRACKED",
                flight_id=aircraft_id,
                payload={
                    "destination": aircraft.destination,
                    "distance_to_destination_km": round(dist_to_go, 3),
                    "step_distance_km": round(step_distance, 3),
                    "current_lat": aircraft.current_lat,
                    "current_lon": aircraft.current_lon,
                },
            )

            if dist_to_go <= step_distance:
                # --------------------------------------------------------
                # Aircraft reached its destination this tick
                # --------------------------------------------------------
                aircraft.current_lat = dest_lat
                aircraft.current_lon = dest_lon
                aircraft.state = FlightState.ARRIVED
                aircraft.landing_date = datetime.datetime.now()
                raw_arrival_voiv = self._generator.actual_voivodeship(aircraft)
                aircraft.actual_voivodeship = (
                    _GEOJSON_TO_TOPOLOGY_KEY.get(raw_arrival_voiv, raw_arrival_voiv)
                    if raw_arrival_voiv else self.name
                )

                summary.arrived.append(aircraft_id)
                self._append_log(
                    f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                    f"ARRIVED {aircraft_id:>10} at {aircraft.destination}",
                    tick=tick,
                    sim_time=sim_time,
                    event_type="AIRCRAFT_ARRIVED",
                    flight_id=aircraft_id,
                    payload={
                        "destination": aircraft.destination,
                        "arrival_voivodeship": aircraft.actual_voivodeship,
                    },
                )
                del self._aircraft[aircraft_id]

            else:
                # --------------------------------------------------------
                # Move the aircraft one step toward its destination
                # --------------------------------------------------------
                ratio: float = step_distance / dist_to_go
                aircraft.current_lat += (dest_lat - aircraft.current_lat) * ratio
                aircraft.current_lon += (dest_lon - aircraft.current_lon) * ratio

                # Translate the raw GeoJSON name to a topology key so that
                # cross-actor comparisons and neighbor lookups are consistent.
                raw_voivodeship: Optional[str] = self._generator.actual_voivodeship(aircraft)
                new_voivodeship: Optional[str] = (
                    _GEOJSON_TO_TOPOLOGY_KEY.get(raw_voivodeship, raw_voivodeship)
                    if raw_voivodeship else None
                )

                if new_voivodeship and new_voivodeship != self.name:
                    # --------------------------------------------------------
                    # Aircraft crossed into a different voivodeship
                    # --------------------------------------------------------
                    await self._handoff_aircraft(
                        aircraft_id=aircraft_id,
                        aircraft=aircraft,
                        target_voivodeship=new_voivodeship,
                        sim_time=sim_time,
                        tick=tick,
                        summary=summary,
                    )
                else:
                    # Aircraft is still in our area — update field and keep it
                    aircraft.actual_voivodeship = new_voivodeship or self.name

        summary.active_count = len(self._aircraft)
        await self._publish_neighbor_snapshot(
            tick=tick,
            sim_time=sim_time,
            summary=summary,
        )
        return summary

    # ------------------------------------------------------------------
    # Handoff protocol
    # ------------------------------------------------------------------

    async def _handoff_aircraft(
        self,
        aircraft_id: str,
        aircraft,
        target_voivodeship: str,
        sim_time: float,
        tick: int,
        summary: TickSummary,
    ) -> None:
        """
        Transfer an aircraft to the correct neighbouring tower.

        The call to ``accept_aircraft`` is awaited so the transfer is confirmed
        before this tick returns.
        This guarantees that no tick boundary can leave an aircraft untracked.

        If the target is not a direct neighbour (e.g. a jump over a small
        voivodeship at high speed), a warning is logged and the aircraft is
        kept here until the manager reconciles on the next tick.
        """
        if target_voivodeship in self._neighbors:
            target_actor = self._neighbors[target_voivodeship]
            request_msg = (
                f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                f"HANDOFF REQUEST {aircraft_id:>10} : {self.name} → {target_voivodeship}"
            )
            self._append_log(
                request_msg,
                tick=tick,
                sim_time=sim_time,
                event_type="HANDOFF_REQUESTED",
                target_voivodeship=target_voivodeship,
                flight_id=aircraft_id,
                payload={
                    "destination": aircraft.destination,
                    "current_lat": aircraft.current_lat,
                    "current_lon": aircraft.current_lon,
                },
            )
            # Await until the neighbour confirms ownership.
            accepted: bool = await target_actor.accept_aircraft.remote(
                aircraft, sim_time, tick
            )
            if accepted:
                del self._aircraft[aircraft_id]
                summary.handed_off.append(aircraft_id)
                summary.handoff_targets[aircraft_id] = target_voivodeship
                event_type = "HANDOFF_COMPLETED"
                msg = (
                    f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                    f"HANDOFF {aircraft_id:>10} : "
                    f"{self.name} → {target_voivodeship}"
                )
            else:
                event_type = "HANDOFF_REJECTED"
                msg = (
                    f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                    f"HANDOFF REJECTED for {aircraft_id} to {target_voivodeship}"
                )
                summary.warnings.append(msg)
        else:
            # Non-adjacent voivodeship — aircraft moved faster than one tick
            # can resolve via the adjacency graph.  Keep ownership here and
            # warn; the manager may trigger an emergency re-route next tick.
            msg = (
                f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
                f"WARNING {aircraft_id:>10} jumped to non-adjacent "
                f"'{target_voivodeship}' from '{self.name}'. "
                f"Known neighbours: {sorted(self._neighbors.keys())}"
            )
            event_type = "HANDOFF_NON_ADJACENT"
            summary.warnings.append(msg)
            self._logger.warning(msg)

        self._append_log(
            msg,
            tick=tick,
            sim_time=sim_time,
            event_type=event_type,
            target_voivodeship=target_voivodeship,
            flight_id=aircraft_id,
            payload={"accepted": target_voivodeship in self._neighbors and aircraft_id in summary.handoff_targets},
        )
        self._logger.info(msg)

    # ------------------------------------------------------------------
    # Query interface (called remotely by manager / API layer)
    # ------------------------------------------------------------------

    def get_aircraft_snapshots(self) -> List[dict]:
        """
        Return serialisable snapshots of all aircraft currently tracked
        by this tower.  Safe to call between ticks.
        """
        return [AircraftSnapshot.from_aircraft(a).to_dict() for a in self._aircraft.values()]

    def get_aircraft_count(self) -> int:
        """Return the number of aircraft currently tracked here."""
        return len(self._aircraft)

    def get_status(self) -> dict:
        """Return a concise status dictionary for monitoring."""
        return {
            "voivodeship": self.name,
            "aircraft_count": len(self._aircraft),
            "neighbors": sorted(self._neighbors.keys()),
            "adjacent_defined": sorted(self.adjacent_names),
            "log_entries": len(self._log),
            "known_neighbor_activity": {
                name: {
                    "tick": snapshot.get("tick"),
                    "aircraft_count": snapshot.get("aircraft_count", 0),
                    "warnings": snapshot.get("warnings", []),
                    "recent_events": snapshot.get("recent_events", []),
                }
                for name, snapshot in self._neighbor_activity.items()
            },
        }

    async def get_neighbor_activity(self) -> Dict[str, dict]:
        """Return the latest published snapshots from adjacent towers."""
        await self._refresh_neighbor_activity()
        return deepcopy(self._neighbor_activity)

    def get_log(self, last_n: int = 50) -> List[str]:
        """
        Return the last *last_n* log entries for this tower.

        :param last_n: Number of most recent entries to return.
        """
        return self._log[-last_n:]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(
        self,
        message: str,
        tick: int,
        sim_time: float,
        event_type: str = "AGENT_EVENT",
        source_voivodeship: Optional[str] = None,
        target_voivodeship: Optional[str] = None,
        flight_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """Append a message to the rolling event log."""
        self._log.append(message)
        if len(self._log) > self._log_capacity:
            # Drop oldest entries to cap memory usage
            self._log = self._log[-self._log_capacity:]
        if self._database_log_service is not None:
            self._database_log_service.record.remote(
                event_type=event_type,
                message=message,
                tick=tick,
                sim_time=sim_time,
                source_voivodeship=source_voivodeship or self.name,
                target_voivodeship=target_voivodeship,
                flight_id=flight_id,
                payload=payload or {},
            )

    async def _refresh_neighbor_activity(
        self,
        tick: int = 0,
        sim_time: float = 0.0,
    ) -> None:
        """Fetch the latest published snapshots for all adjacent neighbours."""
        if self._neighbor_info_service is None or not self.adjacent_names:
            self._neighbor_activity = {}
            return
        self._neighbor_activity = await self._neighbor_info_service.get_snapshots.remote(
            self.adjacent_names
        )
        self._append_log(
            f"[TICK {tick:>6} | T={sim_time:>10.1f}s] REFRESH neighbor activity: "
            f"{sorted(self._neighbor_activity.keys())}",
            tick=tick,
            sim_time=sim_time,
            event_type="NEIGHBOR_ACTIVITY_REFRESHED",
            payload={
                "requested_neighbors": sorted(self.adjacent_names),
                "available_snapshots": sorted(self._neighbor_activity.keys()),
            },
        )

    async def _publish_neighbor_snapshot(
        self,
        tick: int,
        sim_time: float,
        summary: Optional[TickSummary] = None,
    ) -> None:
        """Publish this tower's latest local state for neighbouring actors."""
        if self._neighbor_info_service is None:
            return
        await self._neighbor_info_service.publish.remote(
            self.name,
            {
                "tick": tick,
                "sim_time": sim_time,
                "aircraft_count": len(self._aircraft),
                "active_flight_ids": sorted(self._aircraft.keys()),
                "active_aircraft": self.get_aircraft_snapshots(),
                "neighbors": sorted(self._neighbors.keys()),
                "adjacent_defined": sorted(self.adjacent_names),
                "log_entries": len(self._log),
                "warnings": list(summary.warnings) if summary else [],
                "recent_events": self._log[-5:],
                "handed_off": list(summary.handed_off) if summary else [],
                "handoff_targets": dict(summary.handoff_targets) if summary else {},
                "arrived": list(summary.arrived) if summary else [],
            },
        )
        self._append_log(
            f"[TICK {tick:>6} | T={sim_time:>10.1f}s] "
            f"SNAPSHOT published aircraft={len(self._aircraft)}",
            tick=tick,
            sim_time=sim_time,
            event_type="NEIGHBOR_SNAPSHOT_PUBLISHED",
            payload={
                "aircraft_count": len(self._aircraft),
                "active_flight_ids": sorted(self._aircraft.keys()),
                "neighbors": sorted(self._neighbors.keys()),
            },
        )
