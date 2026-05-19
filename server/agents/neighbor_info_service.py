"""
neighbor_info_service.py — shared neighbor-awareness service for ATC actors.

Each voivodeship actor publishes its latest local snapshot here. Neighbouring
actors can then fetch the most recent published state of adjacent towers
without making N direct cross-actor calls every tick.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Optional

import ray


@ray.remote
class NeighborInfoService:
    """Stores the latest published snapshot for each voivodeship actor."""

    def __init__(self) -> None:
        self._snapshots: Dict[str, dict] = {}

    def publish(self, voivodeship: str, snapshot: dict) -> None:
        """Persist the latest serialisable snapshot for one voivodeship."""
        payload = deepcopy(snapshot)
        payload["voivodeship"] = voivodeship
        self._snapshots[voivodeship] = payload

    def get_snapshot(self, voivodeship: str) -> Optional[dict]:
        """Return the latest snapshot for one voivodeship, if any."""
        snapshot = self._snapshots.get(voivodeship)
        return deepcopy(snapshot) if snapshot is not None else None

    def get_snapshots(self, voivodeships: List[str]) -> Dict[str, dict]:
        """Return the latest snapshots for the requested voivodeships."""
        return {
            name: deepcopy(self._snapshots[name])
            for name in voivodeships
            if name in self._snapshots
        }

    def get_all_snapshots(self) -> Dict[str, dict]:
        """Return latest snapshots for all voivodeships known to the service."""
        return deepcopy(self._snapshots)
