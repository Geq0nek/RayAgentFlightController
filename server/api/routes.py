"""
routes.py — Flask REST API dla systemu ATC.

Endpointy
---------
GET  /api/status          → status całej sieci (tick, sim_time, liczba lotów)
GET  /api/flights         → wszystkie aktywne loty (snapshoty)
GET  /api/flights/<voiv>  → loty śledzone przez konkretną wieżę
GET  /api/voivodeships    → lista województw z liczbą lotów
GET  /api/log/<voiv>      → ostatnie 50 wpisów z logu wieży
POST /api/spawn           → ręczny spawn lotu  { "start": "WAW", "dest": "KRK" }
POST /api/control/start   → uruchom symulację
POST /api/control/stop    → zatrzymaj symulację
"""

from __future__ import annotations

import ray
from flask import Blueprint, jsonify, request, current_app

api = Blueprint("api", __name__, url_prefix="/api")


def _manager():
    """Pobierz uchwyt do ATCManager z kontekstu aplikacji."""
    return current_app.config["ATC_MANAGER"]


# ---------------------------------------------------------------------------
# Sieć / status
# ---------------------------------------------------------------------------

@api.route("/status")
def get_status():
    """Zbiorczy status sieci: tick, sim_time, liczba aktywnych lotów."""
    status = ray.get(_manager().get_network_status.remote())
    return jsonify(status)


@api.route("/voivodeships")
def get_voivodeships():
    """Lista województw z nazwą i liczbą aktualnie śledzonych lotów."""
    status = ray.get(_manager().get_network_status.remote())
    result = [
        {
            "name": v["voivodeship"],
            "aircraft_count": v["aircraft_count"],
            "neighbors": v["neighbors"],
        }
        for v in status["voivodeships"]
    ]
    return jsonify(result)


# ---------------------------------------------------------------------------
# Loty
# ---------------------------------------------------------------------------

@api.route("/flights")
def get_all_flights():
    """Snapshoty wszystkich aktywnych lotów w sieci."""
    flights = ray.get(_manager().get_all_flights.remote())
    return jsonify(flights)


@api.route("/flights/<voivodeship>")
def get_flights_by_voivodeship(voivodeship: str):
    """
    Loty śledzone przez konkretną wieżę.
    Parametr voivodeship to klucz ASCII-lowercase (np. 'mazowieckie').
    """
    flights = ray.get(_manager().get_flights_by_voivodeship.remote(voivodeship))
    return jsonify(flights)


# ---------------------------------------------------------------------------
# Logi agentów
# ---------------------------------------------------------------------------

@api.route("/log/<voivodeship>")
def get_voivodeship_log(voivodeship: str):
    """Ostatnie 50 wpisów z logu wieży ATC danego województwa."""
    n = request.args.get("n", 50, type=int)
    log = ray.get(_manager().get_voivodeship_log.remote(voivodeship, n))
    return jsonify({"voivodeship": voivodeship, "log": log})


@api.route("/reports")
def get_tick_reports():
    """Ostatnie tick-raporty całej sieci (do strony historii)."""
    n = request.args.get("n", 30, type=int)
    reports = ray.get(_manager().get_last_tick_reports.remote(n))
    return jsonify(reports)


# ---------------------------------------------------------------------------
# Sterowanie
# ---------------------------------------------------------------------------

@api.route("/spawn", methods=["POST"])
def spawn_flight():
    """
    Ręczny spawn lotu.
    Body JSON: { "start": "WAW", "dest": "KRK" }
    """
    data = request.get_json(force=True, silent=True) or {}
    start = data.get("start", "").strip().upper()
    dest = data.get("dest", "").strip().upper()

    airports = ray.get(_manager().get_available_airports.remote())
    if start not in airports:
        return jsonify({"error": f"Unknown airport: {start}"}), 400
    if dest not in airports:
        return jsonify({"error": f"Unknown airport: {dest}"}), 400
    if start == dest:
        return jsonify({"error": "start and dest must differ"}), 400

    flight_id = ray.get(_manager().spawn_flight.remote(start, dest))
    if flight_id is None:
        return jsonify({"error": "Could not spawn flight (limit reached?)"}), 503
    return jsonify({"id": flight_id, "start": start, "dest": dest}), 201


@api.route("/control/start", methods=["POST"])
def control_start():
    """Uruchamia pętlę symulacji."""
    result = ray.get(_manager().start.remote())
    return jsonify({"result": result})


@api.route("/control/stop", methods=["POST"])
def control_stop():
    """Zatrzymuje pętlę symulacji."""
    result = ray.get(_manager().stop.remote())
    return jsonify({"result": result})


@api.route("/airports")
def get_airports():
    """Lista dostępnych kodów IATA lotnisk."""
    airports = ray.get(_manager().get_available_airports.remote())
    return jsonify(airports)
