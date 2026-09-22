"""Tests für den Flixbus-Provider (Mapping + Ortsauflösung). Kein Netz: _get gemockt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bahn_de import flix, service

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flix_search.json").read_text()
)


@pytest.fixture(autouse=True)
def clear_cache():
    flix._city_cache.clear()
    yield
    flix._city_cache.clear()


@pytest.fixture
def fake_api(monkeypatch):
    """Mockt _get; merkt sich, mit welchem q die Autocomplete aufgerufen wurde."""
    calls = {"autocomplete": [], "search": []}

    def _get(path, params):
        if "autocomplete" in path:
            calls["autocomplete"].append(params["q"])
            return [{"id": f"id-{params['q'].lower()}", "name": params["q"]}]
        calls["search"].append(params)
        return _FIXTURE

    monkeypatch.setattr(flix, "_get", _get)
    return calls


# --- Ortsauflösung ------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Berlin Hbf", "Berlin"),
    ("Paris Gare de Lyon", "Paris"),
    ("Paris Gare du Nord", "Paris"),
    ("Warszawa Centralna", "Warszawa"),
    ("Praha hl.n.", "Praha"),
    ("Zagreb Glavni kol.", "Zagreb"),
    ("Frankfurt(Main)Hbf", "Frankfurt"),
    ("Budapest Keleti", "Budapest"),
    ("Barcelona Sants", "Barcelona"),
    ("Amsterdam Centraal", "Amsterdam"),
    # Reine Städtenamen bleiben unangetastet.
    ("Lisboa", "Lisboa"),
    ("Bern", "Bern"),
    ("Bad Bentheim", "Bad Bentheim"),
])
def test_bahnhofszusatz_wird_entfernt(query, expected):
    assert flix._strip_station_suffix(query) == expected


def test_normalisierung_passiert_vor_der_abfrage(fake_api):
    """Regression: Flix matcht unscharf.

    "Paris Gare de Lyon" ungefiltert abgeschickt liefert als Top-Treffer Lyon,
    also stillschweigend die falsche Stadt und einen falschen Preis.
    """
    flix.resolve_city("Paris Gare de Lyon")
    assert fake_api["autocomplete"] == ["Paris"]


def test_resolve_city_cached(fake_api):
    flix.resolve_city("Berlin Hbf")
    flix.resolve_city("Berlin Hbf")
    assert len(fake_api["autocomplete"]) == 1


def test_leerer_ort_wirft(fake_api):
    with pytest.raises(ValueError):
        flix.resolve_city("  ")


def test_unbekannter_ort_wirft_flixerror(monkeypatch):
    monkeypatch.setattr(flix, "_get", lambda path, params: [])
    with pytest.raises(flix.FlixError):
        flix.resolve_city("Kleinkleckersdorf")


# --- Mapping ------------------------------------------------------------------

def test_search_mappt_in_connections(fake_api):
    result = flix.search_journeys_flix(
        "Berlin Hbf", "München Hbf", when_iso="2026-08-20T08:00:00", limit=5)
    assert result.connections
    c = result.connections[0]
    assert c.abfahrt_ort and c.ankunft_ort
    assert c.dauer_minuten and c.dauer_minuten > 0
    assert c.waehrung == "EUR"
    assert c.abschnitte
    # Naive lokale Zeit, kein Offset - so wie das bahn.de-Schema es vorgibt.
    assert "+" not in (c.abfahrt or "")


def test_preis_enthaelt_servicegebuehr(fake_api):
    """platform_fee_in_price_required=True -> total_with_platform_fee ist der Zahlbetrag.

    Sonst vergleicht `cheapest` einen Flix-Preis ohne Gebühr gegen einen
    DB-Endpreis und bevorzugt Flix zu Unrecht.
    """
    raw = list(_FIXTURE["trips"][0]["results"].values())[0]
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00", limit=1)
    assert result.connections[0].preis == raw["price"]["total_with_platform_fee"]
    assert result.connections[0].preis != raw["price"]["total"]


def test_ohne_pflichtgebuehr_zaehlt_total(fake_api, monkeypatch):
    raw = list(_FIXTURE["trips"][0]["results"].values())[0]
    fixture = dict(_FIXTURE, platform_fee_in_price_required=False)
    monkeypatch.setattr(flix, "_get", lambda path, params: (
        [{"id": "x", "name": params.get("q", "")}] if "autocomplete" in path else fixture))
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00", limit=1)
    assert result.connections[0].preis == raw["price"]["total"]


def test_umstiege_aus_legs(fake_api):
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00", limit=5)
    for c in result.connections:
        assert c.umstiege == max(len(c.abschnitte) - 1, 0)


def test_limit_wird_beachtet(fake_api):
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00", limit=1)
    assert len(result.connections) == 1


def test_ungueltiges_datum_wirft(fake_api):
    with pytest.raises(flix.FlixError):
        flix.search_journeys_flix("Berlin", "München", when_iso="20.08.2026")


def test_keine_verbindung_gibt_notice(monkeypatch):
    monkeypatch.setattr(flix, "_get", lambda path, params: (
        [{"id": "x", "name": params.get("q", "")}] if "autocomplete" in path
        else {"trips": [], "platform_fee_in_price_required": False}))
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00")
    assert result.connections == []
    assert any("keine buchbare Verbindung" in n for n in result.notices)


def test_nicht_verfuegbare_fahrten_fliegen_raus(monkeypatch):
    trip = _FIXTURE["trips"][0]
    sold_out = {k: dict(v, status="sold_out") for k, v in trip["results"].items()}
    fixture = dict(_FIXTURE, trips=[dict(trip, results=sold_out)])
    monkeypatch.setattr(flix, "_get", lambda path, params: (
        [{"id": "x", "name": params.get("q", "")}] if "autocomplete" in path else fixture))
    result = flix.search_journeys_flix(
        "Berlin", "München", when_iso="2026-08-20T08:00:00")
    assert result.connections == []


# --- Dispatch -----------------------------------------------------------------

def test_service_dispatch_source_flix(fake_api, monkeypatch):
    called = {}
    original = flix.search_journeys_flix  # vor dem Patchen, sonst Rekursion

    def _spy(*args, **kwargs):
        called["hit"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(service._flix, "search_journeys_flix", _spy)
    result = service.search_journeys(
        "Berlin", "München",
        when_iso="2026-08-20T08:00:00",
        fare=service.resolve_fare({}),
        source="flix",
    )
    assert called.get("hit")
    assert result.connections


def test_flix_ist_immer_verfuegbar():
    """Kein API-Key noetig - anders als navitia."""
    assert flix.available() is True
