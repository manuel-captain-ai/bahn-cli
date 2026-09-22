"""Tests für den navitia.io-Backend (Mapping + Dispatch). Kein Netz: _get gemockt."""
from __future__ import annotations

import pytest

from bahn_de import navitia, service


_JOURNEY = {
    "departure_date_time": "20260801T090000",
    "arrival_date_time": "20260801T113000",
    "duration": 9000,          # 150 min
    "nb_transfers": 1,
    "sections": [
        {
            "type": "public_transport",
            "departure_date_time": "20260801T090000",
            "arrival_date_time": "20260801T101500",
            "from": {"name": "Paris Gare de Lyon"},
            "to": {"name": "Lyon Part-Dieu"},
            "duration": 4500,
            "display_informations": {"commercial_mode": "TGV", "label": "TGV 6607"},
        },
        {"type": "transfer", "duration": 600},
        {
            "type": "public_transport",
            "departure_date_time": "20260801T102500",
            "arrival_date_time": "20260801T113000",
            "from": {"name": "Lyon Part-Dieu"},
            "to": {"name": "Grenoble"},
            "duration": 3900,
            "display_informations": {"commercial_mode": "TER"},
        },
    ],
}


def test_datetime_roundtrip():
    assert navitia._to_navitia_dt("2026-08-01T09:00:00") == "20260801T090000"
    assert navitia._from_navitia_dt("20260801T113000") == "2026-08-01T11:30:00"


def test_map_journey():
    c = navitia._map_journey(_JOURNEY)
    assert c.abfahrt == "2026-08-01T09:00:00"
    assert c.ankunft == "2026-08-01T11:30:00"
    assert c.dauer_minuten == 150
    assert c.umstiege == 1
    assert c.produkte == ["TGV", "TER"]
    assert c.abfahrt_ort == "Paris Gare de Lyon"
    assert c.ankunft_ort == "Grenoble"
    assert c.preis is None            # navitia never prices
    assert len(c.abschnitte) == 2     # transfer section dropped


def test_available_false_without_key(monkeypatch):
    monkeypatch.delenv("NAVITIA_API_KEY", raising=False)
    assert navitia.available() is False


def test_search_journeys_navitia(monkeypatch):
    monkeypatch.setenv("NAVITIA_API_KEY", "dummy")

    def _fake_get(path, params):
        if path == "/places":
            return {"places": [{"id": f"stop_area:{params['q']}"}]}
        return {"journeys": [_JOURNEY]}

    monkeypatch.setattr(navitia, "_get", _fake_get)
    res = navitia.search_journeys_navitia("Paris", "Grenoble", when_iso="2026-08-01T09:00:00")
    assert len(res.connections) == 1
    assert res.connections[0].preis is None
    assert any("navitia" in n for n in res.notices)


def test_service_dispatches_to_navitia(monkeypatch):
    from bahn_de.service import SearchResult
    called = {}

    def _fake_nav(from_station, to_station, *, when_iso, arrival=False, limit=5):
        called["hit"] = (from_station, to_station)
        return SearchResult(connections=[], notices=["nav"])

    import bahn_de.navitia as nav
    monkeypatch.setattr(nav, "search_journeys_navitia", _fake_nav)
    from bahn_de.models import FareParams
    res = service.search_journeys(
        "Paris", "Lyon", when_iso="2026-08-01T09:00:00",
        fare=FareParams(), source="navitia")
    assert called["hit"] == ("Paris", "Lyon")
    assert res.notices == ["nav"]
