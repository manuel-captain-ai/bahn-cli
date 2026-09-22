"""Tests für Gruppenreisen (plan_group / suggest_meeting_point).

Alle bahn.de-Aufrufe sind gemockt: ``service.search_journeys`` wird durch eine
Tabelle kanonischer Ergebnisse ersetzt, damit Aggregation und Ranking ohne Netz
geprüft werden können.
"""
from __future__ import annotations

import pytest

from bahn_de import service
from bahn_de.models import Connection, FareParams
from bahn_de.service import SearchResult


def _conn(dep, arr, dur, umstiege=0, preis=None, score=None):
    return Connection(
        abfahrt=f"2026-08-01T{dep}:00", ankunft=f"2026-08-01T{arr}:00",
        dauer_minuten=dur, umstiege=umstiege, preis=preis, waehrung="EUR",
        verbindungsscore=score,
    )


FARE = FareParams(bahncard="25", bahncard_class=2)


def test_pick_arrival_prefers_latest_in_time():
    target = "2026-08-01T14:00:00"
    conns = [
        _conn("09:00", "13:00", 240),
        _conn("10:00", "13:50", 230),   # latest still <= 14:00 -> winner
        _conn("11:00", "14:30", 210),   # too late
    ]
    best = service._pick_arrival(conns, target)
    assert best.ankunft == "2026-08-01T13:50:00"


def test_pick_arrival_falls_back_to_earliest_when_all_late():
    target = "2026-08-01T08:00:00"
    conns = [_conn("09:00", "12:30", 210), _conn("08:30", "11:45", 195)]
    best = service._pick_arrival(conns, target)
    assert best.ankunft == "2026-08-01T11:45:00"


def _fake_search_table(table):
    """Return a search_journeys stub driven by a {(origin,target): [conns]} table."""
    def _stub(from_station, to_station, *, when_iso, fare, arrival=False,
             max_transfers=None, limit=5, **kw):
        conns = table.get((from_station, to_station), [])
        return SearchResult(connections=list(conns), notices=[])
    return _stub


def test_plan_group_aggregates(monkeypatch):
    table = {
        ("Berlin", "Frankfurt"): [_conn("09:12", "13:40", 268, 0, 79.9, 0.9)],
        ("Düsseldorf", "Frankfurt"): [_conn("11:20", "13:50", 150, 1, 49.9, 0.7)],
    }
    monkeypatch.setattr(service, "search_journeys", _fake_search_table(table))
    plan = service.plan_group(
        ["Berlin", "Düsseldorf"], "Frankfurt",
        date="2026-08-01", arrive_by="14:00", fare=FARE,
    )
    assert plan.earliest_departure == "2026-08-01T09:12:00"
    assert plan.latest_arrival == "2026-08-01T13:50:00"
    assert plan.total_price == pytest.approx(129.8)
    assert plan.worst_score == pytest.approx(0.7)
    assert all(leg.best is not None for leg in plan.legs)


def test_plan_group_records_missing_origin(monkeypatch):
    table = {("Berlin", "Frankfurt"): [_conn("09:12", "13:40", 268)]}  # Düsseldorf missing
    monkeypatch.setattr(service, "search_journeys", _fake_search_table(table))
    plan = service.plan_group(
        ["Berlin", "Düsseldorf"], "Frankfurt",
        date="2026-08-01", arrive_by="14:00", fare=FARE,
    )
    missing = [l for l in plan.legs if l.best is None]
    assert [l.origin for l in missing] == ["Düsseldorf"]
    assert any("Keine Verbindung" in n for n in plan.notices)


def test_suggest_meeting_point_ranks_by_worst_case(monkeypatch):
    # Two hubs. At Hannover the worst leg is 120min; at Kassel it's 200min.
    table = {
        ("Berlin", "Hannover Hbf"): [_conn("10:00", "11:40", 100)],
        ("Düsseldorf", "Hannover Hbf"): [_conn("10:00", "12:00", 120)],
        ("Berlin", "Kassel-Wilhelmshöhe"): [_conn("10:00", "13:20", 200)],
        ("Düsseldorf", "Kassel-Wilhelmshöhe"): [_conn("10:00", "12:30", 150)],
    }
    monkeypatch.setattr(service, "search_journeys", _fake_search_table(table))
    res = service.suggest_meeting_point(
        ["Berlin", "Düsseldorf"], date="2026-08-01", arrive_by="14:00",
        fare=FARE, hubs=["Hannover Hbf", "Kassel-Wilhelmshöhe"], top=2,
    )
    assert [c.hub for c in res.candidates] == ["Hannover Hbf", "Kassel-Wilhelmshöhe"]
    assert res.candidates[0].max_minutes == 120


def test_suggest_meeting_point_reachable_first(monkeypatch):
    # Hub A reachable by all (worst 300min); hub B unreachable by one member.
    table = {
        ("Berlin", "HubA"): [_conn("08:00", "13:00", 300)],
        ("Düsseldorf", "HubA"): [_conn("09:00", "12:00", 180)],
        ("Berlin", "HubB"): [_conn("08:00", "10:00", 120)],
        # Düsseldorf -> HubB missing => unreachable
    }
    monkeypatch.setattr(service, "search_journeys", _fake_search_table(table))
    res = service.suggest_meeting_point(
        ["Berlin", "Düsseldorf"], date="2026-08-01", arrive_by="14:00",
        fare=FARE, hubs=["HubA", "HubB"], top=2,
    )
    assert res.candidates[0].hub == "HubA"        # reachable beats faster-but-incomplete
    assert res.candidates[0].reachable is True
    assert res.candidates[1].reachable is False
