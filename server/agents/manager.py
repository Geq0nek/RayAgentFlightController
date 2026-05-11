"""
manager.py — ATCManager: centralny Ray aktor nadzorujący sieć wież ATC.

Architektura
------------
ATCManager jest asynchronicznym Ray aktorem (async actor), który:

  1. Tworzy i inicjalizuje wszystkie 16 VoivodeshipActor-ów oraz SimulationClock.
  2. Rejestruje sąsiadów między wieżami (register_neighbors).
  3. Uruchamia pętlę symulacji w tle (asyncio task) — każdy tick:
       a. pobiera (sim_time, tick, delta_hours) z SimulationClock.advance()
       b. wywołuje VoivodeshipActor.update() na wszystkich wieżach równolegle
       c. przetwarza TickSummary-e: zbiera statystyki, loguje ostrzeżenia
       d. losowo generuje nowe loty via FlightSimulator.generate_random_connected_flight()
       e. zwalnia ID zakończonych lotów via FlightSimulator.generator.release_id()
  4. Udostępnia metody query dla warstwy API / Flask.

Generowanie lotów
-----------------
Manager **nie duplikuje** logiki tworzenia samolotów — deleguje ją do
``FlightSimulator`` z flight_engine.py (ta sama klasa co w trybie standalone).
``FlightSimulator`` pełni tu rolę fabryki: tworzy obiekt Aircraft z poprawnym
stanem początkowym, a manager natychmiast przejmuje ten obiekt (usuwa go z
``simulator.all_flights``) i kieruje do odpowiedniego VoivodeshipActor-a.
Aktualizacja pozycji należy wyłącznie do aktorów — simulator.update_positions()
pozostaje nieużywana.

Użycie
------
  ray.init()
  manager = ATCManager.remote(max_flights=16, simulation_speed=60)
  ray.get(manager.start.remote())          # uruchamia pętlę w tle
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
    """Zbiorczy raport z jednego tick-a całej sieci."""
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
    Centralny menedżer całej sieci wież ATC (16 województw).

    Działa jako asynchroniczny Ray aktor, dzięki czemu pętla symulacji
    (asyncio.Task) może pracować w tle, a metody query są nadal dostępne
    w trakcie działania symulacji.
    """

    def __init__(
        self,
        max_flights: int = 16,
        simulation_speed: float = 60.0,
        tick_interval: float = 1.0,
    ) -> None:
        """
        :param max_flights:        Maksymalna liczba jednoczesnych lotów w sieci.
        :param simulation_speed:   Mnożnik czasu (60 → 1s real = 1 min sim).
        :param tick_interval:      Rzeczywisty czas (w sekundach) między tick-ami.
        """
        # Lazy imports — moduły te muszą być dostępne w PYTHONPATH workerów Ray
        from topology import AdjacencyMatrix
        from aircraft_generator import AircraftGenerator
        from actor import SimulationClock, VoivodeshipActor

        self.max_flights: int = max_flights
        self.simulation_speed: float = simulation_speed
        self.tick_interval: float = tick_interval

        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None

        # ----------------------------------------------------------------
        # FlightSimulator jako fabryka lotów.
        # Zarządza generatorem ID (AircraftGenerator) oraz topologią.
        # Aktualizacja pozycji należy do aktorów — simulator.update_positions()
        # pozostaje nieużywana; simulator pełni tu wyłącznie rolę fabryki.
        # max_flights ustawione na 9999 — kontrolę limitu prowadzi manager.
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
        # Zegar symulacji (shared singleton)
        # ----------------------------------------------------------------
        from actor import SimulationClock, VoivodeshipActor
        self._clock = SimulationClock.remote(simulation_speed)

        # ----------------------------------------------------------------
        # Tworzenie aktorów województw
        # ----------------------------------------------------------------
        self._actors: Dict[str, "VoivodeshipActor"] = {
            name: VoivodeshipActor.remote(
                name,
                self._simulator.matrix,
                self._simulator.generator,
                simulation_speed,
            )
            for name in self._voiv_names
        }

        # ----------------------------------------------------------------
        # Rejestracja sąsiadów — futures przechowywane, await w start()
        # aby uniknąć ray.get() w konstruktorze async aktora.
        # ----------------------------------------------------------------
        self._neighbor_reg_futures = [
            actor.register_neighbors.remote(
                {n: self._actors[n] for n in self._simulator.matrix.adjacent_voivodeships[name] if n in self._actors}
            )
            for name, actor in self._actors.items()
        ]

        # ----------------------------------------------------------------
        # Śledzenie aktywnych lotów (id -> voivodeship) dla cleanup
        # ----------------------------------------------------------------
        self._active_flight_ids: Dict[str, str] = {}  # flight_id → voivodeship name

        # ----------------------------------------------------------------
        # Historia raportów
        # ----------------------------------------------------------------
        self._tick_reports: List[dict] = []
        self._report_capacity: int = 200

        logger.info(
            "[ATCManager] Zainicjalizowano: %d województw, max_flights=%d, speed=%sx",
            len(self._actors),
            max_flights,
            simulation_speed,
        )

    # ------------------------------------------------------------------
    # Sterowanie symulacją
    # ------------------------------------------------------------------

    async def start(self) -> str:
        """
        Uruchamia pętlę symulacji jako asyncio.Task w tle.
        Jeśli symulacja już działa, nie robi nic.
        Przy pierwszym wywołaniu czeka na zakończenie rejestracji sąsiadów.
        """
        if self._running:
            return "already_running"
        # Dokończ rejestrację sąsiadów (odpala się tu, nie w __init__, żeby
        # uniknąć blokującego ray.get w konstruktorze async aktora).
        if self._neighbor_reg_futures:
            await asyncio.gather(*self._neighbor_reg_futures)
            self._neighbor_reg_futures = []
        self._running = True
        self._loop_task = asyncio.ensure_future(self._simulation_loop())
        logger.info("[ATCManager] Symulacja uruchomiona.")
        return "started"

    async def stop(self) -> str:
        """
        Zatrzymuje pętlę symulacji.
        Metoda czeka, aż bieżący tick zakończy się, zanim wróci.
        """
        self._running = False
        if self._loop_task and not self._loop_task.done():
            try:
                await asyncio.wait_for(self._loop_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._loop_task.cancel()
        logger.info("[ATCManager] Symulacja zatrzymana.")
        return "stopped"

    def is_running(self) -> bool:
        """Zwraca True, jeśli pętla symulacji jest aktywna."""
        return self._running

    # ------------------------------------------------------------------
    # Pętla symulacji (prywatna, uruchamiana jako task)
    # ------------------------------------------------------------------

    async def _simulation_loop(self) -> None:
        """Główna pętla — każda iteracja to jeden tick symulacji."""
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("[ATCManager] Błąd w pętli: %s", exc, exc_info=True)
            await asyncio.sleep(self.tick_interval)

    async def _tick(self) -> None:
        """
        Jeden krok symulacji:
          1. Pobierz czas z zegara (await — nie blokuje event loopa).
          2. Zaktualizuj wszystkie wieże równolegle (asyncio.gather).
          3. Przetwórz wyniki (statystyki, zwalnianie ID).
          4. Losowo wygeneruj nowy lot.
        """
        # 1. Czas — jeden call do zegara, wynik dzielony ze wszystkimi aktorami.
        #    W async aktorze Ray ObjectRef jest awaitable, więc nie blokujemy
        #    event loopa.
        sim_time, tick, delta_hours = await self._clock.advance.remote()

        # 2. Aktualizacja wszystkich wież równolegle.
        #    asyncio.gather() pozwala event loopowi obsługiwać inne zadania
        #    podczas oczekiwania na wyniki zdalnych aktorów.
        update_refs = [
            actor.update.remote(sim_time, tick, delta_hours)
            for actor in self._actors.values()
        ]
        summaries = await asyncio.gather(*update_refs)

        # 3. Przetwarzanie wyników
        total_handed = 0
        total_arrived = 0
        total_warnings = 0
        per_voiv: List[dict] = []

        for summary in summaries:
            total_handed += len(summary.handed_off)
            total_arrived += len(summary.arrived)
            total_warnings += len(summary.warnings)

            # Zwalnianie ID zakończonych lotów
            for fid in summary.arrived:
                self._simulator.generator.release_id(fid)
                self._active_flight_ids.pop(fid, None)

            # Aktualizacja trackera (handoff zmienia właściciela)
            for fid in summary.handed_off:
                self._active_flight_ids[fid] = summary.voivodeship

            per_voiv.append(summary.to_dict())

            if summary.warnings:
                for w in summary.warnings:
                    logger.warning("[%s] %s", summary.voivodeship, w)

        # 4. Spawn nowego lotu (jeśli jest miejsce) — async, nie blokuje event loopa
        current_total = len(self._active_flight_ids)
        if current_total < self.max_flights and random.random() > 0.55:
            await self._spawn_random_flight_async(sim_time, tick)

        # 5. Zapisanie raportu
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

    # ------------------------------------------------------------------
    # Generowanie lotów — delegowane do FlightSimulator
    # ------------------------------------------------------------------

    def _route_aircraft_to_actor(self, aircraft) -> Optional[tuple]:
        """
        Wyznacza właściwy VoivodeshipActor dla już gotowego obiektu Aircraft
        (ze statusem IN_FLIGHT i uzupełnionym actual_voivodeship).

        FlightSimulator.add_flight() ustawia actual_voivodeship na nazwę
        z GeoJSON (np. 'Mazowieckie'). Mapowanie na klucz topologii
        (np. 'mazowieckie') pochodzi z _GEOJSON_TO_TOPOLOGY_KEY w actor.py.

        :returns: ``(aircraft, target_actor, voiv_key)`` lub ``None``.
        """
        from actor import _GEOJSON_TO_TOPOLOGY_KEY

        raw_voiv = aircraft.actual_voivodeship
        voiv_key = _GEOJSON_TO_TOPOLOGY_KEY.get(raw_voiv, raw_voiv) if raw_voiv else None

        if voiv_key and voiv_key in self._actors:
            return aircraft, self._actors[voiv_key], voiv_key

        # Fallback: voivodeship lotniska startowego z danych topologii
        start_data = self._simulator.matrix.airports_data.get(aircraft.starting_point, {})
        voiv_key = start_data.get("voivodeship")
        if voiv_key and voiv_key in self._actors:
            return aircraft, self._actors[voiv_key], voiv_key

        logger.warning(
            "[ATCManager] Nie znaleziono aktora dla %s (voiv=%s).",
            aircraft.id, raw_voiv,
        )
        self._simulator.generator.release_id(aircraft.id)
        return None

    async def _spawn_flight_async(self, start: str, dest: str, sim_time: float, tick: int) -> Optional[str]:
        """
        Tworzy nowy lot via FlightSimulator.add_flight() i kieruje go do
        właściwego VoivodeshipActor-a.

        FlightSimulator.add_flight() odpowiada za całą inicjalizację samolotu:
        unikalny ID, stan IN_FLIGHT, współrzędne GPS, wykrycie voivodeship.
        Manager przejmuje obiekt (usuwa z simulator.all_flights) i oddaje
        aktorowi — od tej chwili pozycję aktualizują wyłącznie aktorzy.

        :returns: ID nowego lotu lub None przy błędzie.
        """
        aircraft = self._simulator.add_flight(start, dest)
        if aircraft is None:
            return None

        # Natychmiast wyjmij z wewnętrznej listy symulatora —
        # właścicielem stanu jest teraz VoivodeshipActor.
        self._simulator.all_flights.remove(aircraft)

        result = self._route_aircraft_to_actor(aircraft)
        if result is None:
            return None
        aircraft, target_actor, voiv_key = result

        await target_actor.add_aircraft.remote(aircraft, sim_time, tick)
        self._active_flight_ids[aircraft.id] = voiv_key
        logger.info(
            "[ATCManager] Nowy lot %s: %s → %s (wieża: %s)",
            aircraft.id, start, dest, voiv_key,
        )
        return aircraft.id

    async def _spawn_random_flight_async(self, sim_time: float, tick: int) -> Optional[str]:
        """
        Deleguje do FlightSimulator.generate_random_connected_flight(),
        a następnie kieruje wynikowy lot do właściwego aktora.

        :returns: ID nowego lotu lub None.
        """
        # Użyj wbudowanej metody symulatora do wyboru losowej pary lotnisk
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
        logger.info(
            "[ATCManager] Nowy lot %s: %s → %s (wieża: %s)",
            aircraft.id, aircraft.starting_point, aircraft.destination, voiv_key,
        )
        return aircraft.id

    # ------------------------------------------------------------------
    # Publiczne API — query (async, bo aktor jest async i ray.get w sync
    # metodach blokowałby event loop)
    # ------------------------------------------------------------------

    async def get_all_flights(self) -> List[dict]:
        """
        Zwraca snapshoty wszystkich aktywnych lotów ze wszystkich wież.
        Bezpieczne do wywołania między tick-ami.

        :returns: Lista słowników (format AircraftSnapshot.to_dict()).
        """
        refs = [a.get_aircraft_snapshots.remote() for a in self._actors.values()]
        results = await asyncio.gather(*refs)
        flights: List[dict] = []
        for voiv_flights in results:
            flights.extend(voiv_flights)
        return flights

    async def get_flights_by_voivodeship(self, voivodeship: str) -> List[dict]:
        """
        Zwraca snapshoty lotów z konkretnego województwa.

        :param voivodeship: Klucz województwa (np. ``'mazowieckie'``).
        :returns: Lista słowników lub pusta lista, gdy województwo nie istnieje.
        """
        actor = self._actors.get(voivodeship)
        if actor is None:
            return []
        return await actor.get_aircraft_snapshots.remote()

    async def get_network_status(self) -> dict:
        """
        Zbiorczy status całej sieci: liczba lotów per województwo,
        sąsiedzi, liczba wpisów w logach.

        :returns: Słownik z kluczem ``"voivodeships"`` i listą statusów.
        """
        status_refs = [a.get_status.remote() for a in self._actors.values()]
        statuses = await asyncio.gather(*status_refs)
        sim_time_now, tick_now = await self._clock.get_time.remote()
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
        Pobiera ostatnie *last_n* wpisów z logu wskazanej wieży.

        :param voivodeship: Klucz województwa.
        :param last_n:      Ile ostatnich wpisów zwrócić.
        :returns: Lista stringów z logiem lub ``[]`` gdy województwo nie istnieje.
        """
        actor = self._actors.get(voivodeship)
        if actor is None:
            return []
        return await actor.get_log.remote(last_n)

    def get_last_tick_reports(self, last_n: int = 10) -> List[dict]:
        """
        Zwraca ostatnie *last_n* raportów tick-owych całej sieci.
        Czysto synchroniczna — bez remote calls, bezpieczna w async aktorze.

        :param last_n: Liczba ostatnich raportów.
        :returns: Lista słowników TickReport.to_dict().
        """
        return self._tick_reports[-last_n:]

    async def spawn_flight(self, start: str, dest: str) -> Optional[str]:
        """
        Publiczne API do ręcznego tworzenia lotu.
        Deleguje do FlightSimulator.add_flight() — całe przygotowanie
        obiektu Aircraft (ID, stan, współrzędne, voivodeship) odbywa się tam.

        :param start: Kod IATA lotniska startowego.
        :param dest:  Kod IATA lotniska docelowego.
        :returns:     ID stworzonego lotu lub None przy błędzie.
        """
        sim_time, tick = await self._clock.get_time.remote()
        return await self._spawn_flight_async(start, dest, sim_time, tick)

    def get_available_airports(self) -> List[str]:
        """Zwraca listę wszystkich dostępnych kodów IATA lotnisk."""
        return self._simulator.available_airports

    def get_voivodeship_names(self) -> List[str]:
        """Zwraca listę nazw-kluczy wszystkich województw."""
        return self._voiv_names


# ---------------------------------------------------------------------------
# Skrypt uruchomieniowy (python manager.py)
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

    print("Symulacja działa. Ctrl+C aby zatrzymać.\n")
    try:
        while True:
            time.sleep(5)
            status = ray.get(manager.get_network_status.remote())
            reports = ray.get(manager.get_last_tick_reports.remote(3))
            print(
                f"\n[T={status['sim_time']:.0f}s | tick={status['tick']}] "
                f"Aktywnych lotów: {status['total_aircraft']}"
            )
            for r in reports:
                print(
                    f"  tick={r['tick']:>5} | aktywne={r['total_active']:>3} | "
                    f"handoff={r['total_handed_off']:>2} | "
                    f"lądowania={r['total_arrived']:>2} | "
                    f"ostrzeżenia={r['total_warnings']}"
                )
    except KeyboardInterrupt:
        print("\nZatrzymywanie...")
        ray.get(manager.stop.remote())
        ray.shutdown()
        print("Zamknięto.")
