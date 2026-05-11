"""
routes.py — Flask REST API for the ATC system.

Endpoints
---------
GET  /api/status          → entire network status (tick, sim_time, number of flights)
GET  /api/flights         → all active flights (snapshots)
GET  /api/flights/<voiv>  → flights tracked by a specific tower
GET  /api/voivodeships    → list of voivodeships with number of flights
GET  /api/log/<voiv>      → last 50 tower log entries
POST /api/spawn           → manual flight spawn  { "start": "WAW", "dest": "KRK" }
POST /api/control/start   → start simulation
POST /api/control/stop    → stop simulation
"""

from __future__ import annotations

import ray
from flask import Blueprint, jsonify, request, current_app

api = Blueprint("api", __name__, url_prefix="/api")


def _manager():
    """Get handle to ATCManager from application context."""
    return current_app.config["ATC_MANAGER"]


# ---------------------------------------------------------------------------
# Network / status
# ---------------------------------------------------------------------------

@api.route("/status")
def get_status():
    """Aggregate network status: tick, sim_time, number of active flights."""
    status = ray.get(_manager().get_network_status.remote())
    return jsonify(status)


@api.route("/voivodeships")
def get_voivodeships():
    """List of voivodeships with name and current number of tracked flights."""
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
# Flights
# ---------------------------------------------------------------------------

@api.route("/flights")
def get_all_flights():
    """Snapshots of all active flights in the network."""
    flights = ray.get(_manager().get_all_flights.remote())
    return jsonify(flights)


@api.route("/flights/<voivodeship>")
def get_flights_by_voivodeship(voivodeship: str):
    """
    Flights tracked by a specific tower.
    Voivodeship parameter is an ASCII-lowercase key (e.g., 'mazowieckie').
    """
    flights = ray.get(_manager().get_flights_by_voivodeship.remote(voivodeship))
    return jsonify(flights)


# ---------------------------------------------------------------------------
# Agent logs
# ---------------------------------------------------------------------------

@api.route("/log/<voivodeship>")
def get_voivodeship_log(voivodeship: str):
    """Last 50 log entries from the ATC tower of given voivodeship."""
    n = request.args.get("n", 50, type=int)
    log = ray.get(_manager().get_voivodeship_log.remote(voivodeship, n))
    return jsonify({"voivodeship": voivodeship, "log": log})


@api.route("/reports")
def get_tick_reports():
    """Last tick-reports from the entire network (for the history page)."""
    n = request.args.get("n", 30, type=int)
    reports = ray.get(_manager().get_last_tick_reports.remote(n))
    return jsonify(reports)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------

@api.route("/spawn", methods=["POST"])
def spawn_flight():
    """
    Manual flight spawn.
    JSON body: { "start": "WAW", "dest": "KRK" }
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
    """Starts the simulation loop."""
    result = ray.get(_manager().start.remote())
    return jsonify({"result": result})


@api.route("/control/stop", methods=["POST"])
def control_stop():
    """Stops the simulation loop."""
    result = ray.get(_manager().stop.remote())
    return jsonify({"result": result})


@api.route("/airports")
def get_airports():
    """List of available IATA airport codes."""
    airports = ray.get(_manager().get_available_airports.remote())
    return jsonify(airports)
