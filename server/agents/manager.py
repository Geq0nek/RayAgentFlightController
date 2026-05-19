"""
manager.py — ATCManager: central Ray actor supervising the ATC tower network.

Architecture
------------
ATCManager is an asynchronous Ray actor (async actor) that:

  1. Creates and initializes all 16 VoivodeshipActor instances and SimulationClock.
  2. Registers neighbors between towers (register_neighbors).
  3. Runs the simulation loop in the background (asyncio task) — each tick:
       a. fetches (sim_time, tick, delta_hours) from SimulationClock.advance()
       b. calls VoivodeshipActor.update() on all towers in parallel
       c. processes TickSummary objects: collects statistics, logs warnings
       d. randomly generates new flights via FlightSimulator.generate_random_connected_flight()
       e. releases IDs of finished flights via FlightSimulator.generator.release_id()
  4. Provides query methods for the API / Flask layer.

Flight Generation
------------------
Manager **does not duplicate** the aircraft creation logic — it delegates it to
``FlightSimulator`` from flight_engine.py (same class as in standalone mode).
``FlightSimulator`` acts as a factory: creates an Aircraft object with correct
initial state, and manager immediately takes over the object (removes it from
``simulator.all_flights``) and directs it to the appropriate VoivodeshipActor.
Position updates belong exclusively to actors — simulator.update_positions()
remains unused.

Usage
------
  ray.init()
  manager = ATCManager.remote(max_flights=16, simulation_speed=60)
  ray.get(manager.start.remote())          # starts the loop in the background
  ...
  flights = ray.get(manager.get_all_flights.remote())
  ray.get(manager.stop.remote())
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import ray

logger = logging.getLogger("ATC.Manager")


# ---------------------------------------------------------------------------
# Globalny raport tick-a (zwracany przez get_last_tick_report)
# ---------------------------------------------------------------------------

@dataclass
class TickReport:
    """Aggregate report from a single tick of the entire network."""
    tick: int
    sim_time: float
    total_active: int
    total_handed_off: int
    total_arrived: int
    total_warnings: int
    per_voivodeship: List[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "sim_time": self.sim_time,
            "total_active": self.total_active,
            "total_handed_off": self.total_handed_off,
            "total_arrived": self.total_arrived,
            "total_warnings": self.total_warnings,
            "per_voivodeship": self.per_voivodeship,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# ATCManager
# ---------------------------------------------------------------------------

@ray.remote
class ATCManager:
    """
    Central manager of the entire ATC tower network (16 voivodeships).

    Acts as an asynchronous Ray actor, so the simulation loop
    (asyncio.Task) can work in the background while query methods remain available
    during simulation execution.
    """

    def __init__(
        self,
        max_flights: int = 16,
        simulation_speed: float = 60.0,
        tick_interval: float = 1.0,
    ) -> None:
        """
        :param max_flights:        Maximum number of concurrent flights in the network.
        :param simulation_speed:   Time multiplier (60 → 1s real = 1 min sim).
        :param tick_interval:      Actual time (in seconds) between ticks.
        """
        # Lazy imports — these modules must be available in Ray workers' PYTHONPATH
        from actor import SimulationClock, VoivodeshipActor
        from database_log_service import DatabaseLogService
        from neighbor_info_service import NeighborInfoService

        self.max_flights: int = max_flights
        self.simulation_speed: float = simulation_speed
        self.tick_interval: float = tick_interval

        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None

        # ----------------------------------------------------------------
        # FlightSimulator as the flights factory.
        # Manages the ID generator (AircraftGenerator) and topology.
        # Position updates belong to actors — simulator.update_positions()
        # remains unused; simulator acts only as a factory.
        # max_flights set to 9999 — the manager handles the limit.
        # ----------------------------------------------------------------
        from flight_engine import FlightSimulator
        from topology import AdjacencyMatrix

        self._simulator = FlightSimulator(
            adjacency_matrix=AdjacencyMatrix(),
            max_flights=9999,
            simulation_speed=simulation_speed,
        )
        self._voiv_names: List[str] = list(self._simulator.matrix.adjacent_voivodeships.keys())

        # ----------------------------------------------------------------
        # Simulation clock (shared singleton)
        # ----------------------------------------------------------------
        self._clock = SimulationClock.remote(simulation_speed)
        self._neighbor_info_service = NeighborInfoService.remote()
        self._database_log_service = DatabaseLogService.remote()

        # ----------------------------------------------------------------
        # Creating voivodeship actors
        # ----------------------------------------------------------------
        self._actors: Dict[str, "VoivodeshipActor"] = {
            name: VoivodeshipActor.remote(
                name,
                self._simulator.matrix,
                self._simulator.generator,
                simulation_speed=simulation_speed,
                neighbor_info_service=self._neighbor_info_service,
                database_log_service=self._database_log_service,
            )
            for name in self._voiv_names
        }

        # ----------------------------------------------------------------
        # Registering neighbors — futures stored, awaited in start()
        # to avoid ray.get() in the async actor constructor.
        # ----------------------------------------------------------------
        self._neighbor_reg_futures = [
            actor.register_neighbors.remote(
                {n: self._actors[n] for n in self._simulator.matrix.adjacent_voivodeships[name] if n in self._actors}
            )
            for name, actor in self._actors.items()
        ]

        # ----------------------------------------------------------------
        # Tracking active flights (id -> voivodeship) for cleanup
        # ----------------------------------------------------------------
        self._active_flight_ids: Dict[str, str] = {}  # flight_id → voivodeship name

        # ----------------------------------------------------------------
        # Report history
        # ----------------------------------------------------------------
        self._tick_reports: List[dict] = []
        self._report_capacity: int = 200

        logger.info(
            "[ATCManager] Initialized: %d voivodeships, max_flights=%d, speed=%sx",
            len(self._actors),
            max_flights,
            simulation_speed,
        )

    # ------------------------------------------------------------------
    # Simulation control
    # ------------------------------------------------------------------

    async def start(self) -> str:
        """
        Starts the simulation loop as an asyncio.Task in the background.
        If simulation is already running, does nothing.
        On first call, waits for neighbor registration to complete.
        """
        if self._running:
            return "already_running"
        # Complete neighbor registration (runs here, not in __init__, to
        # avoid blocking ray.get in the async actor constructor).
        if self._neighbor_reg_futures:
            await asyncio.gather(*self._neighbor_reg_futures)
            self._neighbor_reg_futures = []
        self._running = True
        self._loop_task = asyncio.ensure_future(self._simulation_loop())
        self._record_manager_log(
            "MANAGER_STARTED",
            "ATCManager simulation loop started.",
            tick=0,
            sim_time=0.0,
            payload={"max_flights": self.max_flights, "tick_interval": self.tick_interval},
        )
        logger.info("[ATCManager] Simulation started.")
        return "started"

    async def stop(self) -> str:
        """
        Stops the simulation loop.
        Waits for the current tick to finish before returning.
        """
        self._running = False
        if self._loop_task and not self._loop_task.done():
            try:
                await asyncio.wait_for(self._loop_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._loop_task.cancel()
        sim_time, tick = await self._clock.get_time.remote()
        self._record_manager_log(
            "MANAGER_STOPPED",
            "ATCManager simulation loop stopped.",
            tick=tick,
            sim_time=sim_time,
        )
        logger.info("[ATCManager] Simulation stopped.")
        return "stopped"

    def is_running(self) -> bool:
        """Returns True if the simulation loop is active."""
        return self._running

    # ------------------------------------------------------------------
    # Simulation loop (private, run as a task)
    # ------------------------------------------------------------------

    async def _simulation_loop(self) -> None:
        """Main loop — each iteration is one simulation tick."""
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("[ATCManager] Error in loop: %s", exc, exc_info=True)
            await asyncio.sleep(self.tick_interval)

    async def _tick(self) -> None:
        """
        One simulation step:
          1. Fetch time from the clock (await — doesn't block event loop).
          2. Update all towers in parallel (asyncio.gather).
          3. Process results (statistics, ID release).
          4. Randomly generate a new flight.
        """
        # 1. Time — single clock call, result shared with all actors.
        #    In an async Ray actor, ObjectRef is awaitable, so we don't block
        #    the event loop.
        sim_time, tick, delta_hours = await self._clock.advance.remote()
        self._record_manager_log(
            "TICK_STARTED",
            f"Tick {tick} started for {len(self._actors)} voivodeship actors.",
            tick=tick,
            sim_time=sim_time,
            payload={"delta_hours": delta_hours, "actor_count": len(self._actors)},
        )

        # 2. Update all towers in parallel.
        #    asyncio.gather() allows the event loop to handle other tasks
        #    while waiting for remote actor results.
        update_refs = [
            actor.update.remote(sim_time, tick, delta_hours)
            for actor in self._actors.values()
        ]
        summaries = await asyncio.gather(*update_refs)

        # 3. Process results
        total_handed = 0
        total_arrived = 0
        total_warnings = 0
        per_voiv: List[dict] = []

        for summary in summaries:
            total_handed += len(summary.handed_off)
            total_arrived += len(summary.arrived)
            total_warnings += len(summary.warnings)

            # Release IDs of finished flights
            for fid in summary.arrived:
                self._simulator.generator.release_id(fid)
                self._active_flight_ids.pop(fid, None)

            # Update tracker (handoff changes owner)
            for fid, target_voivodeship in summary.handoff_targets.items():
                self._active_flight_ids[fid] = target_voivodeship

            per_voiv.append(summary.to_dict())

            if summary.warnings:
                for w in summary.warnings:
                    logger.warning("[%s] %s", summary.voivodeship, w)
                    self._record_manager_log(
                        "ACTOR_WARNING",
                        w,
                        tick=tick,
                        sim_time=sim_time,
                        source_voivodeship=summary.voivodeship,
                    )

        # 4. Spawn new flight (if space available) — async, doesn't block event loop
        current_total = len(self._active_flight_ids)
        if current_total < self.max_flights and random.random() > 0.55:
            await self._spawn_random_flight_async(sim_time, tick)

        # 5. Save report
        report = TickReport(
            tick=tick,
            sim_time=sim_time,
            total_active=len(self._active_flight_ids),
            total_handed_off=total_handed,
            total_arrived=total_arrived,
            total_warnings=total_warnings,
            per_voivodeship=per_voiv,
        )
        self._tick_reports.append(report.to_dict())
        if len(self._tick_reports) > self._report_capacity:
            self._tick_reports = self._tick_reports[-self._report_capacity:]
        self._record_manager_log(
            "TICK_COMPLETED",
            f"Tick {tick} completed: active={report.total_active}, "
            f"handoff={total_handed}, arrived={total_arrived}, warnings={total_warnings}.",
            tick=tick,
            sim_time=sim_time,
            payload=report.to_dict(),
        )

    # ------------------------------------------------------------------
    # Flight generation — delegated to FlightSimulator
    # ------------------------------------------------------------------

    def _route_aircraft_to_actor(self, aircraft) -> Optional[tuple]:
        """
        Determines the proper VoivodeshipActor for an already-ready Aircraft object
        (with IN_FLIGHT status and populated actual_voivodeship).

        FlightSimulator.add_flight() sets actual_voivodeship to the GeoJSON name
        (e.g., 'Mazowieckie'). Mapping to topology key (e.g., 'mazowieckie')
        comes from _GEOJSON_TO_TOPOLOGY_KEY in actor.py.

        :returns: ``(aircraft, target_actor, voiv_key)`` or ``None``.
        """
        from actor import _GEOJSON_TO_TOPOLOGY_KEY

        raw_voiv = aircraft.actual_voivodeship
        voiv_key = _GEOJSON_TO_TOPOLOGY_KEY.get(raw_voiv, raw_voiv) if raw_voiv else None

        if voiv_key and voiv_key in self._actors:
            return aircraft, self._actors[voiv_key], voiv_key

        # Fallback: voivodeship of the starting airport from topology data
        start_data = self._simulator.matrix.airports_data.get(aircraft.starting_point, {})
        voiv_key = start_data.get("voivodeship")
        if voiv_key and voiv_key in self._actors:
            return aircraft, self._actors[voiv_key], voiv_key

        logger.warning(
            "[ATCManager] Actor not found for %s (voiv=%s).",
            aircraft.id, raw_voiv,
        )
        self._simulator.generator.release_id(aircraft.id)
        return None

    async def _spawn_flight_async(self, start: str, dest: str, sim_time: float, tick: int) -> Optional[str]:
        """
        Creates a new flight via FlightSimulator.add_flight() and directs it to
        the proper VoivodeshipActor.

        FlightSimulator.add_flight() is responsible for full aircraft initialization:
        unique ID, IN_FLIGHT state, GPS coordinates, voivodeship detection.
        Manager takes over the object (removes from simulator.all_flights) and gives
        it to the actor — from that point, positions are updated exclusively by actors.

        :returns: ID of new flight or None on error.
        """
        aircraft = self._simulator.add_flight(start, dest)
        if aircraft is None:
            return None

        # Immediately remove from simulator's internal list —
        # state ownership now belongs to VoivodeshipActor.
        self._simulator.all_flights.remove(aircraft)

        result = self._route_aircraft_to_actor(aircraft)
        if result is None:
            return None
        aircraft, target_actor, voiv_key = result

        await target_actor.add_aircraft.remote(aircraft, sim_time, tick)
        self._active_flight_ids[aircraft.id] = voiv_key
        self._record_manager_log(
            "MANUAL_FLIGHT_ROUTED",
            f"Manual flight {aircraft.id}: {start} -> {dest} routed to {voiv_key}.",
            tick=tick,
            sim_time=sim_time,
            target_voivodeship=voiv_key,
            flight_id=aircraft.id,
            payload={"start": start, "dest": dest},
        )
        logger.info(
            "[ATCManager] Nowy lot %s: %s → %s (wieża: %s)",
            aircraft.id, start, dest, voiv_key,
        )
        return aircraft.id

    async def _spawn_random_flight_async(self, sim_time: float, tick: int) -> Optional[str]:
        """
        Delegates to FlightSimulator.generate_random_connected_flight(),
        then directs the resulting flight to the proper actor.

        :returns: ID of new flight or None.
        """
        # Use simulator's built-in method to select random airport pair
        aircraft = self._simulator.generate_random_connected_flight()
        if aircraft is None:
            return None

        self._simulator.all_flights.remove(aircraft)

        result = self._route_aircraft_to_actor(aircraft)
        if result is None:
            return None
        aircraft, target_actor, voiv_key = result

        await target_actor.add_aircraft.remote(aircraft, sim_time, tick)
        self._active_flight_ids[aircraft.id] = voiv_key
        self._record_manager_log(
            "RANDOM_FLIGHT_ROUTED",
            f"Random flight {aircraft.id}: {aircraft.starting_point} -> "
            f"{aircraft.destination} routed to {voiv_key}.",
            tick=tick,
            sim_time=sim_time,
            target_voivodeship=voiv_key,
            flight_id=aircraft.id,
            payload={
                "start": aircraft.starting_point,
                "dest": aircraft.destination,
            },
        )
        logger.info(
            "[ATCManager] Nowy lot %s: %s → %s (wieża: %s)",
            aircraft.id, aircraft.starting_point, aircraft.destination, voiv_key,
        )
        return aircraft.id

    # ------------------------------------------------------------------
    # Public API — queries (async because actor is async and ray.get in sync
    # methods would block the event loop)
    # ------------------------------------------------------------------

    async def get_all_flights(self) -> List[dict]:
        """
        Returns snapshots of all active flights from the shared neighbour cache.
        This avoids polling every tower just to build API state.

        :returns: List of dicts (AircraftSnapshot.to_dict() format).
        """
        snapshots = await self._neighbor_info_service.get_all_snapshots.remote()
        flights: List[dict] = []
        for voivodeship in self._voiv_names:
            flights.extend(snapshots.get(voivodeship, {}).get("active_aircraft", []))
        return flights

    async def get_flights_by_voivodeship(self, voivodeship: str) -> List[dict]:
        """
        Returns flight snapshots from a specific voivodeship using the shared cache.

        :param voivodeship: Voivodeship key (e.g., ``'mazowieckie'``).
        :returns: List of dicts or empty list if voivodeship doesn't exist.
        """
        if voivodeship not in self._actors:
            return []
        snapshot = await self._neighbor_info_service.get_snapshot.remote(voivodeship)
        return snapshot.get("active_aircraft", []) if snapshot else []

    async def get_network_status(self) -> dict:
        """
        Aggregate network status from NeighborInfoService snapshots.
        The manager does not need to poll every tower for monitoring data.

        :returns: Dict with ``"voivodeships"`` key and list of statuses.
        """
        snapshots = await self._neighbor_info_service.get_all_snapshots.remote()
        sim_time_now, tick_now = await self._clock.get_time.remote()
        statuses = []
        for name in self._voiv_names:
            snapshot = snapshots.get(name, {})
            adjacent_names = sorted(self._simulator.matrix.adjacent_voivodeships.get(name, []))
            statuses.append(
                {
                    "voivodeship": name,
                    "aircraft_count": snapshot.get("aircraft_count", 0),
                    "neighbors": snapshot.get("neighbors", adjacent_names),
                    "adjacent_defined": snapshot.get("adjacent_defined", adjacent_names),
                    "log_entries": snapshot.get("log_entries", 0),
                    "snapshot_tick": snapshot.get("tick"),
                    "snapshot_sim_time": snapshot.get("sim_time"),
                    "recent_events": snapshot.get("recent_events", []),
                    "warnings": snapshot.get("warnings", []),
                }
            )
        return {
            "voivodeships": statuses,
            "total_aircraft": sum(s["aircraft_count"] for s in statuses),
            "total_voivodeships": len(self._actors),
            "is_running": self._running,
            "sim_time": sim_time_now,
            "tick": tick_now,
        }

    async def get_voivodeship_log(self, voivodeship: str, last_n: int = 50) -> List[str]:
        """
        Fetches the last *last_n* log entries from the specified tower.

        :param voivodeship: Voivodeship key.
        :param last_n:      How many recent entries to return.
        :returns: List of log strings or ``[]`` if voivodeship doesn't exist.
        """
        actor = self._actors.get(voivodeship)
        if actor is None:
            return []
        return await actor.get_log.remote(last_n)

    async def get_neighbor_activity(self, voivodeship: str) -> Dict[str, dict]:
        """Return latest snapshots for neighbours of a specific voivodeship."""
        if voivodeship not in self._actors:
            return {}
        adjacent_names = self._simulator.matrix.adjacent_voivodeships.get(voivodeship, [])
        return await self._neighbor_info_service.get_snapshots.remote(adjacent_names)

    async def get_persisted_logs(self, filters: Optional[dict] = None) -> List[dict]:
        """Return logs persisted in PostgreSQL, filtered for the history view."""
        filters = filters or {}
        return await self._database_log_service.query.remote(**filters)

    async def get_log_event_types(self) -> List[str]:
        """Return persisted event types available for filtering."""
        return await self._database_log_service.get_event_types.remote()

    async def get_log_filter_options(self) -> dict:
        """Return distinct persisted values available in log filters."""
        return await self._database_log_service.get_filter_options.remote()

    async def get_log_database_status(self) -> dict:
        """Return PostgreSQL log service status."""
        return await self._database_log_service.status.remote()

    def get_last_tick_reports(self, last_n: int = 10) -> List[dict]:
        """
        Returns the last *last_n* tick reports from the entire network.
        Purely synchronous — no remote calls, safe in async actor.

        :param last_n: Number of last reports.
        :returns: List of TickReport.to_dict() dicts.
        """
        return self._tick_reports[-last_n:]

    async def spawn_flight(self, start: str, dest: str) -> Optional[str]:
        """
        Public API for manually creating a flight.
        Delegates to FlightSimulator.add_flight() — all Aircraft object preparation
        (ID, state, coordinates, voivodeship) happens there.

        :param start: IATA code of starting airport.
        :param dest:  IATA code of destination airport.
        :returns:     ID of created flight or None on error.
        """
        sim_time, tick = await self._clock.get_time.remote()
        return await self._spawn_flight_async(start, dest, sim_time, tick)

    def get_available_airports(self) -> List[str]:
        """Returns list of all available IATA airport codes."""
        return self._simulator.available_airports

    def get_voivodeship_names(self) -> List[str]:
        """Returns list of all voivodeship key names."""
        return self._voiv_names

    def _record_manager_log(
        self,
        event_type: str,
        message: str,
        tick: Optional[int] = None,
        sim_time: Optional[float] = None,
        source_voivodeship: Optional[str] = None,
        target_voivodeship: Optional[str] = None,
        flight_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """Persist a structured manager-level event."""
        self._database_log_service.record.remote(
            event_type=event_type,
            message=message,
            tick=tick,
            sim_time=sim_time,
            source_voivodeship=source_voivodeship or "manager",
            target_voivodeship=target_voivodeship,
            flight_id=flight_id,
            payload=payload or {},
        )


# ---------------------------------------------------------------------------
# Startup script (python manager.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    ray.init(ignore_reinit_error=True)

    print("=== ATC Manager — start ===")
    manager = ATCManager.remote(max_flights=12, simulation_speed=120, tick_interval=1.0)
    ray.get(manager.start.remote())

    print("Simulation is running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(5)
            status = ray.get(manager.get_network_status.remote())
            reports = ray.get(manager.get_last_tick_reports.remote(3))
            print(
                f"\n[T={status['sim_time']:.0f}s | tick={status['tick']}] "
                f"Active flights: {status['total_aircraft']}"
            )
            for r in reports:
                print(
                    f"  tick={r['tick']:>5} | active={r['total_active']:>3} | "
                    f"handoff={r['total_handed_off']:>2} | "
                    f"landings={r['total_arrived']:>2} | "
                    f"warnings={r['total_warnings']}"
                )
    except KeyboardInterrupt:
        print("\nStopping...")
        ray.get(manager.stop.remote())
        ray.shutdown()
        print("Closed.")
