"""Shared voivodeship name mapping between GeoJSON and topology keys."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

GEOJSON_TO_TOPOLOGY_KEY: Dict[str, str] = {
    "Dolnośląskie": "dolnoslaskie",
    "Kujawsko-Pomorskie": "kujawsko_pomorskie",
    "Lubelskie": "lubelskie",
    "Lubuskie": "lubuskie",
    "Łódzkie": "lodzkie",
    "Małopolskie": "malopolskie",
    "Mazowieckie": "mazowieckie",
    "Opolskie": "opolskie",
    "Podkarpackie": "podkarpackie",
    "Podlaskie": "podlaskie",
    "Pomorskie": "pomorskie",
    "Śląskie": "slaskie",
    "Świętokrzyskie": "swietokrzyskie",
    "Warmińsko-Mazurskie": "warminsko_mazurskie",
    "Wielkopolskie": "wielkopolskie",
    "Zachodniopomorskie": "zachodniopomorskie",
}

TOPOLOGY_TO_GEOJSON: Dict[str, str] = {
    value: key for key, value in GEOJSON_TO_TOPOLOGY_KEY.items()
}

TOPOLOGY_KEYS = frozenset(GEOJSON_TO_TOPOLOGY_KEY.values())


def normalize_voivodeship_key(name: Optional[str]) -> Optional[str]:
    """Return the canonical topology key for a voivodeship name."""
    if not name or name == "manager":
        return name
    if name in GEOJSON_TO_TOPOLOGY_KEY:
        return GEOJSON_TO_TOPOLOGY_KEY[name]
    if name in TOPOLOGY_KEYS:
        return name
    return name


def expand_voivodeship_filter(values: Sequence[str]) -> List[str]:
    """Include legacy GeoJSON spellings when filtering persisted logs."""
    expanded: List[str] = []
    for value in values:
        if not value or value in expanded:
            continue
        expanded.append(value)
        normalized = normalize_voivodeship_key(value)
        if normalized and normalized not in expanded:
            expanded.append(normalized)
        geojson_name = TOPOLOGY_TO_GEOJSON.get(normalized or value)
        if geojson_name and geojson_name not in expanded:
            expanded.append(geojson_name)
    return expanded
