"""
routes.py — Flask REST API for the ATC system.

Endpoints
---------
GET  /api/status          → entire network status (tick, sim_time, number of flights)
GET  /api/flights         → all active flights (snapshots)
GET  /api/flights/<voiv>  → flights tracked by a specific tower
GET  /api/voivodeships    → list of voivodeships with number of flights
GET  /api/neighbors/<voiv> → latest cached snapshots of neighbouring towers
GET  /api/log/<voiv>      → last 50 tower log entries
GET  /api/logs            → PostgreSQL-backed structured agent logs
GET  /api/logs/types      → event types available in persisted logs
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


@api.route("/neighbors/<voivodeship>")
def get_neighbor_activity(voivodeship: str):
    """Cached snapshots published by neighbouring towers."""
    activity = ray.get(_manager().get_neighbor_activity.remote(voivodeship))
    return jsonify({"voivodeship": voivodeship, "neighbors": activity})


# ---------------------------------------------------------------------------
# Agent logs
# ---------------------------------------------------------------------------

@api.route("/log/<voivodeship>")
def get_voivodeship_log(voivodeship: str):
    """Last 50 log entries from the ATC tower of given voivodeship."""
    n = request.args.get("n", 50, type=int)
    log = ray.get(_manager().get_voivodeship_log.remote(voivodeship, n))
    return jsonify({"voivodeship": voivodeship, "log": log})


@api.route("/logs")
def get_persisted_logs():
    """Structured logs persisted in PostgreSQL, with optional filters."""
    sources = _multi_filter_values("source", "sources")
    targets = _multi_filter_values("target", "targets")
    event_types = _multi_filter_values("event_type", "event_types")
    flight_ids = _multi_filter_values("flight_id", "flight_ids")
    filters = {
        "source_voivodeship": None if sources else request.args.get("source") or None,
        "source_voivodeships": sources or None,
        "target_voivodeship": None if targets else request.args.get("target") or None,
        "target_voivodeships": targets or None,
        "event_type": None if event_types else request.args.get("event_type") or None,
        "event_types": event_types or None,
        "flight_id": None if flight_ids else request.args.get("flight_id") or None,
        "flight_ids": flight_ids or None,
        "text": request.args.get("q") or None,
        "tick_from": request.args.get("tick_from", type=int),
        "tick_to": request.args.get("tick_to", type=int),
        "limit": request.args.get("limit", 100, type=int),
    }
    logs = ray.get(_manager().get_persisted_logs.remote(filters))
    return jsonify({"logs": logs})


def _multi_filter_values(*names: str) -> list[str]:
    """Read repeated query params and comma-separated values for one filter."""
    values = []
    for name in names:
        values.extend(request.args.getlist(name))
    result = [
        value.strip()
        for csv_value in values
        for value in csv_value.split(",")
        if value.strip()
    ]
    return list(dict.fromkeys(result))


@api.route("/logs/types")
def get_log_event_types():
    """List event types currently stored in PostgreSQL logs."""
    event_types = ray.get(_manager().get_log_event_types.remote())
    return jsonify(event_types)


@api.route("/logs/options")
def get_log_filter_options():
    """Distinct values available for all PostgreSQL log filters."""
    options = ray.get(_manager().get_log_filter_options.remote())
    return jsonify(options)


@api.route("/logs/status")
def get_log_database_status():
    """Health information for the PostgreSQL log writer."""
    status = ray.get(_manager().get_log_database_status.remote())
    return jsonify(status)


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
