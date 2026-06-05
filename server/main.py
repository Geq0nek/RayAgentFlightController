"""
main.py — punkt wejścia serwera ATC.

Uruchamia:
  1. Ray (lokalny klaster)
  2. ATCManager (Ray async actor) — pełna sieć 16 wież
  3. Flask REST API (port 5000)
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "agents"))

import ray
from flask import Flask
from flask_cors import CORS
from agents.manager import ATCManager
from api.routes import api


def create_app(manager_handle) -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.config["ATC_MANAGER"] = manager_handle
    app.register_blueprint(api)
    return app


if __name__ == "__main__":
    agents_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")

    os.environ["RAY_memory_monitor_refresh_ms"] = "0"

    ray.init(
        ignore_reinit_error=True,
        num_cpus=4,
        runtime_env={"env_vars": {"PYTHONPATH": agents_dir}},
    )
    print("[main] Ray uruchomiony.")

    # 2. ATCManager
    manager = ATCManager.remote(
        max_flights=20,
        simulation_speed=60,   # 1s real = 1min sim
        tick_interval=1.0,
    )
    ray.get(manager.start.remote())
    print("[main] ATCManager uruchomiony.")

    # 3. Flask
    app = create_app(manager)
    print("[main] Flask API uruchamiany na :5000 ...")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
