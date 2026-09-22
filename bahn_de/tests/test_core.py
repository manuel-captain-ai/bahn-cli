"""Unit tests for bahn_de.

All HTTP is mocked at the ``bahn_backend._request`` entry point (or, for the
``_request`` error-path tests, at ``bahn_backend._get_session``). No real
network calls happen here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bahn_de import backend as bahn_backend
from bahn_de import formatters
from bahn_de import models as bahn_models
from bahn_de import service as bahn_de_service
from bahn_de import cli as bahn_de_cli
from bahn_de.models import Connection, FareParams, Section, Station, Teilpreis, ViaHalt


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name):
    with open(FIXTURES_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def orte_response():
    return _load_fixture("orte_response.json")


@pytest.fixture
def fahrplan_response():
    return _load_fixture("fahrplan_response.json")


@pytest.fixture
def fahrplan_regional_response():
    return _load_fixture("fahrplan_regional_response.json")


@pytest.fixture
def bestpreis_response():
    return _load_fixture("bestpreis_response.json")


@pytest.fixture
def fahrplan_umstieg_response():
    return _load_fixture("fahrplan_umstieg_response.json")


# ============================================================================
# build_journey_payload
# ============================================================================

MINIMAL_PAYLOAD = {
    "abfahrtsHalt": "<from>",
    "ankunftsHalt": "<to>",
    "anfrageZeitpunkt": "<when>",
    "ankunftSuche": "ABFAHRT",
    "klasse": "KLASSE_2",
    "produktgattungen": [
        "ICE", "EC_IC", "IR", "REGIONAL", "SBAHN",
        "BUS", "SCHIFF", "UBAHN", "TRAM", "ANRUFPFLICHTIG",
    ],
    "reisende": [
        {
            "typ": "ERWACHSENER",
            "ermaessigungen": [{"art": "KEINE_ERMAESSIGUNG", "klasse": "KLASSENLOS"}],
            "alter": [],
            "anzahl": 1,
        }
    ],
    "schnelleVerbindungen": True,
    "sitzplatzOnly": False,
    "bikeCarriage": False,
    "reservierungsKontingenteVorhanden": False,
}


def test_build_journey_payload_default_matches_minimal_payload():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert payload == MINIMAL_PAYLOAD


def test_build_journey_payload_with_bahncard25():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", bahncard=25)
    assert payload["reisende"][0]["ermaessigungen"] == [
        {"art": "BAHNCARD25", "klasse": "KLASSE_2"}
    ]
    # rest of the payload is unchanged
    expected = dict(MINIMAL_PAYLOAD)
    expected["reisende"] = [
        {
            "typ": "ERWACHSENER",
            "ermaessigungen": [{"art": "BAHNCARD25", "klasse": "KLASSE_2"}],
            "alter": [],
            "anzahl": 1,
        }
    ]
    assert payload == expected


def test_build_journey_payload_with_bahncard50():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", bahncard=50)
    assert payload["reisende"][0]["ermaessigungen"] == [
        {"art": "BAHNCARD50", "klasse": "KLASSE_2"}
    ]


def test_build_journey_payload_with_bahncard25_class1():
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", bahncard=25, bahncard_class=1
    )
    assert payload["reisende"][0]["ermaessigungen"] == [
        {"art": "BAHNCARD25", "klasse": "KLASSE_1"}
    ]


def test_build_journey_payload_first_class():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", first_class=True)
    assert payload["klasse"] == "KLASSE_1"
    # everything else stays at minimal-payload defaults
    expected = dict(MINIMAL_PAYLOAD)
    expected["klasse"] = "KLASSE_1"
    assert payload == expected


def test_build_journey_payload_deutschlandticket():
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", deutschlandticket=True
    )
    assert payload["deutschlandTicketVorhanden"] is True
    # absent by default
    default_payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert "deutschlandTicketVorhanden" not in default_payload


def test_build_journey_payload_dticket_only():
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", dticket_only=True
    )
    assert payload["nurDeutschlandTicketVerbindungen"] is True
    default_payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert "nurDeutschlandTicketVerbindungen" not in default_payload


def test_build_journey_payload_deutschlandticket_and_dticket_only_together():
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", deutschlandticket=True, dticket_only=True
    )
    assert payload["deutschlandTicketVorhanden"] is True
    assert payload["nurDeutschlandTicketVerbindungen"] is True


def test_build_journey_payload_arrival():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", arrival=True)
    assert payload["ankunftSuche"] == "ANKUNFT"
    default_payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert default_payload["ankunftSuche"] == "ABFAHRT"


def test_build_journey_payload_max_transfers_zero():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", max_transfers=0)
    assert payload["maxUmstiege"] == 0
    default_payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert "maxUmstiege" not in default_payload


def test_build_journey_payload_min_transfer_time():
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", min_transfer_time=20
    )
    assert payload["minUmstiegszeit"] == 20
    # absent by default, like the other optional fields
    default_payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert "minUmstiegszeit" not in default_payload


def test_build_journey_payload_min_transfer_time_zero_is_set():
    # 0 is a meaningful value (not "unset"), must still be included
    payload = bahn_backend.build_journey_payload(
        "<from>", "<to>", "<when>", min_transfer_time=0
    )
    assert payload["minUmstiegszeit"] == 0


def test_build_journey_payload_bike():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", bike=True)
    assert payload["bikeCarriage"] is True


def test_build_journey_payload_passengers():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", passengers=2)
    assert payload["reisende"][0]["anzahl"] == 2


# ============================================================================
# parse_connection
# ============================================================================

def test_parse_connection_fahrplan_has_price(fahrplan_response):
    raw = fahrplan_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)

    assert set(parsed.keys()) == {
        "trip_id", "abfahrt", "abfahrt_ort", "ankunft", "ankunft_ort",
        "dauer_minuten", "umstiege", "umstiegszeiten_minuten", "produkte",
        "abschnitte", "preis", "waehrung", "via_halte", "has_teilpreis", "teilpreise",
        "ctx_recon", "alterseingabe_erforderlich",
        "_raw_abschnitte",  # private: raw sections for predictor enrichment
    }
    assert parsed["trip_id"] == raw["tripId"]
    assert parsed["abfahrt"] == "2026-06-19T09:34:00"
    assert parsed["abfahrt_ort"] == "Hamburg Hbf"
    assert parsed["ankunft"] == "2026-06-19T11:23:00"
    assert parsed["ankunft_ort"] == "Berlin Hbf"
    assert parsed["dauer_minuten"] == raw["verbindungsDauerInSeconds"] // 60
    assert parsed["umstiege"] == raw["umstiegsAnzahl"]
    assert parsed["produkte"] == ["RJ 175"]
    assert parsed["preis"] == 77.99
    assert parsed["preis"] > 0
    assert parsed["ctx_recon"] == raw.get("ctxRecon")
    assert parsed["alterseingabe_erforderlich"] == bool(raw.get("isAlterseingabeErforderlich"))
    assert parsed["waehrung"] == "EUR"

    # direct connection -> no layovers, one leg, no via halte, no teilpreise
    assert parsed["umstiegszeiten_minuten"] == []
    assert len(parsed["abschnitte"]) == 1
    assert parsed["via_halte"] == []
    assert parsed["teilpreise"] == []
    leg = parsed["abschnitte"][0]
    assert set(leg.keys()) == {
        "abfahrt", "abfahrt_ort", "ankunft", "ankunft_ort",
        "dauer_minuten", "produkt", "typ",
    }
    assert leg["abfahrt"] == "2026-06-19T09:34:00"
    assert leg["abfahrt_ort"] == "Hamburg Hbf"
    assert leg["ankunft"] == "2026-06-19T11:23:00"
    assert leg["ankunft_ort"] == "Berlin Hbf"
    assert leg["produkt"] == "RJ 175"
    assert leg["typ"] == "PUBLICTRANSPORT"
    assert leg["dauer_minuten"] == raw["verbindungsAbschnitte"][0]["abschnittsDauer"] // 60


def test_parse_connection_regional_has_no_price(fahrplan_regional_response):
    for raw in fahrplan_regional_response["verbindungen"]:
        parsed = formatters.parse_connection(raw)
        assert parsed["preis"] is None
        assert parsed["waehrung"] is None


def test_parse_connection_regional_first_connection_schema(fahrplan_regional_response):
    raw = fahrplan_regional_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    assert parsed["trip_id"] == raw["tripId"]
    assert parsed["produkte"] == ["S5"]
    assert parsed["preis"] is None
    assert parsed["waehrung"] is None


def test_parse_connection_from_flattened_bestpreis(bestpreis_response):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    assert flat  # non-empty

    element = flat[0]
    parsed = formatters.parse_connection(element)

    # price comes from abPreis (angebotsPreis is absent on bestpreis elements)
    assert element.get("angebotsPreis") is None
    assert element.get("abPreis") is not None
    assert parsed["preis"] == element["abPreis"]["betrag"]
    assert parsed["preis"] > 0
    assert parsed["waehrung"] == element["abPreis"]["waehrung"]
    # trip id comes from the embedded "verbindung" object
    assert parsed["trip_id"] == element["tripId"]


def test_parse_connection_no_sections_yields_none_fields():
    raw = {"tripId": "abc", "verbindungsAbschnitte": []}
    parsed = formatters.parse_connection(raw)
    assert parsed["abfahrt"] is None
    assert parsed["abfahrt_ort"] is None
    assert parsed["ankunft"] is None
    assert parsed["ankunft_ort"] is None
    assert parsed["produkte"] == []
    assert parsed["preis"] is None
    assert parsed["waehrung"] is None


def test_parse_connection_filters_footpaths():
    raw = {
        "tripId": "footpath-test",
        "verbindungsDauerInSeconds": 600,
        "umstiegsAnzahl": 1,
        "verbindungsAbschnitte": [
            {
                "abfahrt": {"sollzeit": "2026-06-19T08:00:00"},
                "ankunft": {"sollzeit": "2026-06-19T08:10:00"},
                "abfahrtsOrt": "A",
                "ankunftsOrt": "B",
                "verkehrsmittel": {"typ": "WALK", "name": "Fussweg"},
            },
            {
                "abfahrt": {"sollzeit": "2026-06-19T08:15:00"},
                "ankunft": {"sollzeit": "2026-06-19T09:00:00"},
                "abfahrtsOrt": "B",
                "ankunftsOrt": "C",
                "verkehrsmittel": {"typ": "PUBLICTRANSPORT", "name": "RE 1"},
            },
        ],
        "angebotsPreis": {"betrag": 12.5, "waehrung": "EUR"},
    }
    parsed = formatters.parse_connection(raw)
    assert parsed["produkte"] == ["RE 1"]
    assert parsed["abfahrt_ort"] == "A"
    assert parsed["ankunft_ort"] == "C"
    assert parsed["abfahrt"] == "2026-06-19T08:00:00"
    assert parsed["ankunft"] == "2026-06-19T09:00:00"


# ============================================================================
# parse_connection: umstiegszeiten_minuten / abschnitte
# ============================================================================

def test_parse_connection_umstieg_layover_minutes(fahrplan_umstieg_response):
    raw = fahrplan_umstieg_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)

    assert raw["umstiegsAnzahl"] == 1
    assert parsed["umstiege"] == 1
    # arrival in Hamburg Hbf at 10:21, departure at 10:34 -> 13 minute layover
    assert parsed["umstiegszeiten_minuten"] == [13]


def test_parse_connection_umstieg_abschnitte(fahrplan_umstieg_response):
    raw = fahrplan_umstieg_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)

    abschnitte = parsed["abschnitte"]
    assert len(abschnitte) == 2

    first, second = abschnitte
    for leg in abschnitte:
        assert set(leg.keys()) == {
            "abfahrt", "abfahrt_ort", "ankunft", "ankunft_ort",
            "dauer_minuten", "produkt", "typ",
        }
        assert leg["typ"] == "PUBLICTRANSPORT"

    assert first["abfahrt"] == "2026-06-19T08:59:00"
    assert first["abfahrt_ort"] == "Rendsburg"
    assert first["ankunft"] == "2026-06-19T10:21:00"
    assert first["ankunft_ort"] == "Hamburg Hbf"
    # menschenlesbare Linie aus `mittelText`, nicht die nackte Laufnummer ("11163")
    assert first["produkt"] == "RE7"
    assert first["dauer_minuten"] == 4920 // 60

    assert second["abfahrt"] == "2026-06-19T10:34:00"
    assert second["abfahrt_ort"] == "Hamburg Hbf"
    assert second["ankunft"] == "2026-06-19T12:22:00"
    assert second["ankunft_ort"] == "Berlin Hbf"
    assert second["produkt"] == "ICE 509"
    assert second["dauer_minuten"] == 6480 // 60

    # journey endpoints come from the outer legs, not the layover
    assert parsed["abfahrt"] == "2026-06-19T08:59:00"
    assert parsed["abfahrt_ort"] == "Rendsburg"
    assert parsed["ankunft"] == "2026-06-19T12:22:00"
    assert parsed["ankunft_ort"] == "Berlin Hbf"
    assert parsed["produkte"] == ["RE7", "ICE 509"]


def test_parse_connection_layover_none_when_times_missing():
    raw = {
        "tripId": "missing-times",
        "verbindungsDauerInSeconds": 3600,
        "umstiegsAnzahl": 1,
        "verbindungsAbschnitte": [
            {
                "abfahrt": {"sollzeit": "2026-06-19T08:00:00"},
                "ankunft": {},  # missing arrival time
                "abfahrtsOrt": "A",
                "ankunftsOrt": "B",
                "verkehrsmittel": {"typ": "PUBLICTRANSPORT", "name": "RE 1"},
                "abschnittsDauer": 1800,
            },
            {
                "abfahrt": {"sollzeit": "2026-06-19T09:00:00"},
                "ankunft": {"sollzeit": "2026-06-19T10:00:00"},
                "abfahrtsOrt": "B",
                "ankunftsOrt": "C",
                "verkehrsmittel": {"typ": "PUBLICTRANSPORT", "name": "RE 2"},
                "abschnittsDauer": 3600,
            },
        ],
    }
    parsed = formatters.parse_connection(raw)
    assert parsed["umstiegszeiten_minuten"] == [None]


def test_parse_connection_no_sections_yields_empty_abschnitte_and_umstiegszeiten():
    raw = {"tripId": "abc", "verbindungsAbschnitte": []}
    parsed = formatters.parse_connection(raw)
    assert parsed["abschnitte"] == []
    assert parsed["umstiegszeiten_minuten"] == []


# ============================================================================
# filter_by_transfer_time
# ============================================================================

def _connection_with_layovers(layovers):
    return {"trip_id": "x", "umstiegszeiten_minuten": layovers}


def test_filter_by_transfer_time_direct_connection_always_passes():
    direct = _connection_with_layovers([])
    assert formatters.filter_by_transfer_time([direct], min_minutes=10) == [direct]
    assert formatters.filter_by_transfer_time([direct], max_minutes=5) == [direct]
    assert formatters.filter_by_transfer_time([direct], min_minutes=10, max_minutes=5) == [direct]


def test_filter_by_transfer_time_min_minutes_drops_short_layovers():
    short = _connection_with_layovers([5])
    ok = _connection_with_layovers([13])

    result = formatters.filter_by_transfer_time([short, ok], min_minutes=10)
    assert result == [ok]


def test_filter_by_transfer_time_max_minutes_drops_long_layovers():
    long_one = _connection_with_layovers([90])
    ok = _connection_with_layovers([13])

    result = formatters.filter_by_transfer_time([long_one, ok], max_minutes=60)
    assert result == [ok]


def test_filter_by_transfer_time_min_and_max_combined():
    too_short = _connection_with_layovers([2])
    too_long = _connection_with_layovers([120])
    just_right = _connection_with_layovers([20])

    result = formatters.filter_by_transfer_time(
        [too_short, too_long, just_right], min_minutes=10, max_minutes=60
    )
    assert result == [just_right]


def test_filter_by_transfer_time_any_bad_layover_drops_multi_leg_connection():
    # two layovers, one within range, one too short -> dropped entirely
    mixed = _connection_with_layovers([13, 5])
    result = formatters.filter_by_transfer_time([mixed], min_minutes=10)
    assert result == []


def test_filter_by_transfer_time_none_layover_is_ignored():
    has_none = _connection_with_layovers([None])
    result_min = formatters.filter_by_transfer_time([has_none], min_minutes=100)
    result_max = formatters.filter_by_transfer_time([has_none], max_minutes=1)
    assert result_min == [has_none]
    assert result_max == [has_none]


def test_filter_by_transfer_time_no_bounds_passes_everything():
    connections = [
        _connection_with_layovers([]),
        _connection_with_layovers([5]),
        _connection_with_layovers([200]),
    ]
    assert formatters.filter_by_transfer_time(connections) == connections


def test_filter_by_transfer_time_real_umstieg_connection_passes_within_bounds(
    fahrplan_umstieg_response,
):
    raw = fahrplan_umstieg_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    assert parsed["umstiegszeiten_minuten"] == [13]

    assert formatters.filter_by_transfer_time([parsed], min_minutes=10, max_minutes=20) == [parsed]
    assert formatters.filter_by_transfer_time([parsed], min_minutes=15) == []
    assert formatters.filter_by_transfer_time([parsed], max_minutes=10) == []


# ============================================================================
# _fmt_umstiege / format_connections layout
# ============================================================================

def test_fmt_umstiege_direct_connection():
    c = {"umstiege": 0, "umstiegszeiten_minuten": []}
    assert formatters._fmt_umstiege(c) == "0"


def test_fmt_umstiege_single_transfer():
    c = {"umstiege": 1, "umstiegszeiten_minuten": [13]}
    assert formatters._fmt_umstiege(c) == "1 (13m)"


def test_fmt_umstiege_multiple_transfers():
    c = {"umstiege": 2, "umstiegszeiten_minuten": [13, 24]}
    assert formatters._fmt_umstiege(c) == "2 (13/24m)"


def test_fmt_umstiege_none_layovers_falls_back_to_count():
    c = {"umstiege": 1, "umstiegszeiten_minuten": [None]}
    assert formatters._fmt_umstiege(c) == "1"


def test_fmt_umstiege_missing_umstiege_is_unknown():
    c = {"umstiegszeiten_minuten": []}
    assert formatters._fmt_umstiege(c) == "?"


def test_format_connections_header_includes_umst_column():
    parsed = [{
        "abfahrt": "2026-06-19T09:34:00",
        "ankunft": "2026-06-19T11:23:00",
        "dauer_minuten": 109,
        "umstiege": 1,
        "umstiegszeiten_minuten": [13],
        "produkte": ["RJ 175"],
        "preis": 77.99,
        "waehrung": "EUR",
    }]
    table = formatters.format_connections(parsed)
    lines = table.splitlines()

    header, separator = lines[0], lines[1]
    assert "Umst" in header
    # separator line widened to 75 chars to match the wider Umst column
    assert separator == "-" * 75

    # the transfer time annotation shows up in the row
    body = "\n".join(lines[2:])
    assert "1 (13m)" in body


def test_format_connections_umstieg_fixture_end_to_end(fahrplan_umstieg_response):
    raw = fahrplan_umstieg_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    table = formatters.format_connections([parsed])
    assert "1 (13m)" in table


# ============================================================================
# _flatten_bestpreis
# ============================================================================

def test_flatten_bestpreis_with_intervalle_key(bestpreis_response):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    assert isinstance(flat, list)
    assert len(flat) > 0

    element = flat[0]
    # merged dict has both the wrapper's price fields...
    assert "abPreis" in element
    assert "klasse" in element
    # ...and the embedded verbindung's fields
    assert "tripId" in element
    assert "verbindungsAbschnitte" in element


def test_flatten_bestpreis_with_tagesbestpreisintervalle_key():
    synthetic = {
        "tagesbestPreisIntervalle": [
            {
                "verbindungen": [
                    {
                        "abPreis": {"betrag": 19.99, "waehrung": "EUR"},
                        "klasse": "KLASSE_2",
                        "verbindung": {
                            "tripId": "synthetic-1",
                            "verbindungsDauerInSeconds": 1200,
                            "umstiegsAnzahl": 0,
                            "verbindungsAbschnitte": [],
                        },
                    }
                ]
            }
        ]
    }
    flat = bahn_backend._flatten_bestpreis(synthetic)
    assert len(flat) == 1
    element = flat[0]
    assert element["abPreis"] == {"betrag": 19.99, "waehrung": "EUR"}
    assert element["klasse"] == "KLASSE_2"
    assert element["tripId"] == "synthetic-1"
    assert element["verbindungsDauerInSeconds"] == 1200


def test_flatten_bestpreis_empty_data():
    assert bahn_backend._flatten_bestpreis({}) == []
    assert bahn_backend._flatten_bestpreis({"intervalle": []}) == []


# ============================================================================
# resolve_station
# ============================================================================

def test_resolve_station_passthrough_for_lid():
    lid = "A=1@O=Hamburg Hbf@X=10006909@Y=53552733@U=80@L=8002549@"
    station = bahn_backend.resolve_station(lid)
    assert station == {"id": lid, "name": lid, "extId": None, "alternatives": []}


def test_resolve_station_passthrough_does_not_call_request(monkeypatch):
    called = []

    def fake_request(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("Should not call _request for @O= passthrough")

    monkeypatch.setattr(bahn_backend, "_request", fake_request)
    lid = "A=1@O=Hamburg@X=1@Y=1@"
    station = bahn_backend.resolve_station(lid)
    assert station["id"] == lid
    assert called == []


MOB_LOCATIONS = [
    {"locationId": "A=1@O=Hamburg Hbf@L=8002549@", "name": "Hamburg Hbf", "evaNr": "8002549", "locationType": "ST"},
    {"locationId": "A=1@O=Hamburg-Altona@L=8002553@", "name": "Hamburg-Altona", "evaNr": "8002553", "locationType": "ST"},
]


def test_resolve_station_with_match(monkeypatch):
    monkeypatch.setattr(bahn_backend, "_mob_post", lambda *a, **kw: MOB_LOCATIONS)

    station = bahn_backend.resolve_station("Hamburg")

    assert station == {
        "id": "A=1@O=Hamburg Hbf@L=8002549@", "name": "Hamburg Hbf",
        "extId": "8002549", "alternatives": ["Hamburg-Altona"],
    }


def test_resolve_station_no_match_raises_bahnapierror(monkeypatch):
    monkeypatch.setattr(bahn_backend, "_mob_post", lambda *a, **kw: [])

    with pytest.raises(bahn_backend.BahnApiError):
        bahn_backend.resolve_station("Nonexistent Place XYZ")


def test_resolve_station_empty_value_raises_valueerror():
    with pytest.raises(ValueError):
        bahn_backend.resolve_station("")

    with pytest.raises(ValueError):
        bahn_backend.resolve_station("   ")

    with pytest.raises(ValueError):
        bahn_backend.resolve_station(None)


# ============================================================================
# search_stations
# ============================================================================

def test_search_stations_empty_query_raises_valueerror():
    with pytest.raises(ValueError):
        bahn_backend.search_stations("")

    with pytest.raises(ValueError):
        bahn_backend.search_stations("   ")


def test_search_stations_returns_list(monkeypatch):
    captured = {}

    def fake_mob_post(path, body, content_type, timeout=30):
        captured.update(path=path, body=body, ct=content_type)
        return MOB_LOCATIONS

    monkeypatch.setattr(bahn_backend, "_mob_post", fake_mob_post)

    result = bahn_backend.search_stations("Hamburg", limit=10)

    assert [r["id"] for r in result] == [m["locationId"] for m in MOB_LOCATIONS]
    assert result[0]["extId"] == "8002549"
    assert captured["path"] == "location/search"
    assert captured["body"]["searchTerm"] == "Hamburg"
    assert captured["body"]["maxResults"] == 10
    assert "location" in captured["ct"]


def test_search_stations_unexpected_response_raises(monkeypatch):
    monkeypatch.setattr(bahn_backend, "_mob_post", lambda *a, **kw: {"not": "a list"})

    with pytest.raises(bahn_backend.BahnApiError):
        bahn_backend.search_stations("Hamburg")


# ============================================================================
# search_connections / day_best_prices
# ============================================================================

def test_search_connections_translates_to_and_from_app_api(monkeypatch):
    """Web payload in -> app body out; app response in -> web shape the parser reads."""
    captured = {}
    mob = _load_fixture("mob_fahrplan_response.json")  # real Kiel->Dresden, 2026-09-22

    def fake_mob_post(path, body, content_type, timeout=30):
        captured.update(path=path, body=body)
        return mob

    monkeypatch.setattr(bahn_backend, "_mob_post", fake_mob_post)

    payload = bahn_backend.build_journey_payload(
        "from-lid", "to-lid", "2026-11-16T08:00:00", bahncard=25, passengers=2, max_transfers=1,
    )
    result = bahn_backend.search_connections(payload)

    assert captured["path"] == "angebote/fahrplan"
    wunsch = captured["body"]["reiseHin"]["wunsch"]
    assert wunsch["abgangsLocationId"] == "from-lid"
    assert wunsch["zielLocationId"] == "to-lid"
    assert wunsch["zeitWunsch"] == {"reiseDatum": "2026-11-16T08:00:00+01:00", "zeitPunktArt": "ABFAHRT"}
    assert wunsch["verkehrsmittel"] == ["ALL"]
    assert wunsch["maxUmstiege"] == 1
    reisende = captured["body"]["reisendenProfil"]["reisende"]
    assert reisende == [{"ermaessigungen": ["BAHNCARD25 KLASSE_2"], "reisendenTyp": "ERWACHSENER"}] * 2

    c = formatters.parse_connection(result["verbindungen"][0])
    assert c["abfahrt"] == "2026-11-16T08:16:00"
    assert c["ankunft"] == "2026-11-16T13:07:00"
    assert c["abfahrt_ort"] == "Kiel Hbf"
    assert c["ankunft_ort"] == "Dresden Hbf"
    assert c["produkte"] == ["ICE 577", "RJ 175"]
    assert c["umstiege"] == 1
    assert c["preis"] == 22.49
    assert c["waehrung"] == "EUR"
    assert c["trip_id"] and c["ctx_recon"] == c["trip_id"]


def test_mob_products_maps_subset_and_all():
    assert bahn_backend._mob_products(None) == ["ALL"]
    assert bahn_backend._mob_products(list(bahn_backend.ALL_PRODUCTS)) == ["ALL"]
    assert bahn_backend._mob_products(["ICE", "REGIONAL"]) == ["HOCHGESCHWINDIGKEITSZUEGE", "NAHVERKEHRSONSTIGEZUEGE"]


def test_day_best_prices_pages_five_times_and_dedupes(monkeypatch, fahrplan_regional_response):
    calls = []

    def fake_search(payload):
        calls.append(payload["anfrageZeitpunkt"])
        return fahrplan_regional_response

    monkeypatch.setattr(bahn_backend, "search_connections", fake_search)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a, **kw: None)

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", "2026-06-19T09:00:00")
    result, source = bahn_backend.day_best_prices(payload)

    assert source == "paging-fallback"
    assert [c[11:13] for c in calls] == ["05", "09", "13", "17", "21"]
    trip_ids = {v["tripId"] for v in fahrplan_regional_response["verbindungen"]}
    assert len(result) == len(trip_ids)


# ============================================================================
# _departure_iso
# ============================================================================

def test_departure_iso_returns_first_section_sollzeit():
    raw = {
        "verbindungsAbschnitte": [
            {"abfahrt": {"sollzeit": "2026-06-19T08:59:00"}},
            {"abfahrt": {"sollzeit": "2026-06-19T10:34:00"}},
        ]
    }
    assert bahn_backend._departure_iso(raw) == "2026-06-19T08:59:00"


def test_departure_iso_no_sections_returns_none():
    assert bahn_backend._departure_iso({"verbindungsAbschnitte": []}) is None
    assert bahn_backend._departure_iso({}) is None


def test_departure_iso_missing_abfahrt_returns_none():
    raw = {"verbindungsAbschnitte": [{}]}
    assert bahn_backend._departure_iso(raw) is None


# ============================================================================
# search_window
# ============================================================================

def _conn(trip_id, departure_iso):
    """A minimal raw connection with a single PUBLICTRANSPORT leg."""
    return {
        "tripId": trip_id,
        "umstiegsAnzahl": 0,
        "verbindungsAbschnitte": [
            {
                "abfahrt": {"sollzeit": departure_iso},
                "ankunft": {"sollzeit": departure_iso},
                "abfahrtsOrt": "A",
                "ankunftsOrt": "B",
                "verkehrsmittel": {"typ": "PUBLICTRANSPORT", "name": "X"},
                "abschnittsDauer": 600,
            }
        ],
    }


ANCHOR = "2026-06-19T09:00:00"

# "anchor page" - results when anfrageZeitpunkt == ANCHOR (09:00)
ANCHOR_PAGE = [
    _conn("anchor-1", "2026-06-19T09:02:00"),   # within [before, after]
    _conn("anchor-2", "2026-06-19T09:45:00"),   # within after, outside if after small
    _conn("shared", "2026-06-19T09:10:00"),     # also present on the earlier page
]

# "earlier page" - results when anfrageZeitpunkt == anchor - before minutes
EARLIER_PAGE = [
    _conn("earlier-1", "2026-06-19T08:40:00"),  # within [before, after]
    _conn("earlier-2", "2026-06-19T08:10:00"),  # outside the window (too early)
    _conn("shared", "2026-06-19T09:10:00"),     # duplicate tripId, also on anchor page
]


def _fake_search_connections(payload):
    """Return ANCHOR_PAGE or EARLIER_PAGE depending on anfrageZeitpunkt."""
    if payload["anfrageZeitpunkt"] == ANCHOR:
        return {"verbindungen": ANCHOR_PAGE}
    return {"verbindungen": EARLIER_PAGE}


def test_search_window_single_call_when_before_is_zero(monkeypatch):
    calls = []

    def fake_search_connections(payload):
        calls.append(payload["anfrageZeitpunkt"])
        return _fake_search_connections(payload)

    monkeypatch.setattr(bahn_backend, "search_connections", fake_search_connections)
    sleep_calls = []
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a: sleep_calls.append(a))

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", ANCHOR)
    result = bahn_backend.search_window(payload, ANCHOR, before=0, after=60)

    # exactly one call, at the anchor
    assert calls == [ANCHOR]
    assert sleep_calls == []

    trip_ids = [v["tripId"] for v in result]
    # anchor-1 (09:02) and shared (09:10) within [09:00, 10:00]; anchor-2 (09:45) too
    assert set(trip_ids) == {"anchor-1", "anchor-2", "shared"}


def test_search_window_two_calls_when_before_positive(monkeypatch):
    calls = []

    def fake_search_connections(payload):
        calls.append(payload["anfrageZeitpunkt"])
        return _fake_search_connections(payload)

    monkeypatch.setattr(bahn_backend, "search_connections", fake_search_connections)
    sleep_calls = []
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a: sleep_calls.append(a))

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", ANCHOR)
    # window: [08:30, 09:15]
    result = bahn_backend.search_window(payload, ANCHOR, before=30, after=15, polite_delay=2.5)

    # two calls: the earlier edge (08:30) first, then the anchor (09:00)
    assert calls == ["2026-06-19T08:30:00", ANCHOR]
    # one polite delay between the two calls
    assert sleep_calls == [(2.5,)]

    trip_ids = [v["tripId"] for v in result]
    # earlier-1 (08:40) and shared (09:10) and anchor-1 (09:02) are within [08:30, 09:15]
    # earlier-2 (08:10) is too early; anchor-2 (09:45) is too late
    assert set(trip_ids) == {"earlier-1", "anchor-1", "shared"}


def test_search_window_dedupes_by_trip_id(monkeypatch):
    monkeypatch.setattr(bahn_backend, "search_connections", _fake_search_connections)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a: None)

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", ANCHOR)
    result = bahn_backend.search_window(payload, ANCHOR, before=30, after=60)

    trip_ids = [v["tripId"] for v in result]
    # "shared" appears in both pages but only once in the result
    assert trip_ids.count("shared") == 1


def test_search_window_sorted_by_departure(monkeypatch):
    monkeypatch.setattr(bahn_backend, "search_connections", _fake_search_connections)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a: None)

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", ANCHOR)
    result = bahn_backend.search_window(payload, ANCHOR, before=30, after=60)

    departures = [bahn_backend._departure_iso(v) for v in result]
    assert departures == sorted(departures)


def test_search_window_passes_through_payload_fields(monkeypatch):
    captured_payloads = []

    def fake_search_connections(payload):
        captured_payloads.append(payload)
        return _fake_search_connections(payload)

    monkeypatch.setattr(bahn_backend, "search_connections", fake_search_connections)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda *a: None)

    payload = bahn_backend.build_journey_payload("from-lid", "to-lid", ANCHOR, bahncard=25)
    bahn_backend.search_window(payload, ANCHOR, before=10, after=10)

    for p in captured_payloads:
        assert p["abfahrtsHalt"] == "from-lid"
        assert p["ankunftsHalt"] == "to-lid"
        assert p["reisende"][0]["ermaessigungen"] == [{"art": "BAHNCARD25", "klasse": "KLASSE_2"}]
    # original payload's anfrageZeitpunkt is untouched
    assert payload["anfrageZeitpunkt"] == ANCHOR


# ============================================================================
# load_config / save_config / effective_params
# ============================================================================

@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_file = config_dir / "config.json"
    legacy_file = tmp_path / "legacy" / "config.json"
    monkeypatch.setattr(bahn_backend, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(bahn_backend, "CONFIG_FILE", config_file)
    # Point the legacy path at a non-existent tmp location so the one-time
    # migration in load_config never touches the real ~/.config during tests.
    monkeypatch.setattr(bahn_backend, "_LEGACY_CONFIG_FILE", legacy_file)
    return config_file


def test_load_config_missing_file_returns_empty_dict(isolated_config):
    assert bahn_backend.load_config() == {}


def test_save_and_load_config_roundtrip(isolated_config):
    bahn_backend.save_config({"bahncard": "25"})
    assert bahn_backend.load_config() == {"bahncard": "25"}
    # chmod 600
    mode = isolated_config.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_config_invalid_json_returns_empty_dict(isolated_config):
    isolated_config.parent.mkdir(parents=True, exist_ok=True)
    isolated_config.write_text("not json{")
    assert bahn_backend.load_config() == {}


def test_effective_params_defaults(isolated_config):
    result = bahn_backend.effective_params({})
    assert result == {
        "bahncard": None,
        "bahncard_class": 2,
        "first_class": False,
        "deutschlandticket": False,
    }


def test_effective_params_config_overrides_defaults(isolated_config):
    bahn_backend.save_config({
        "bahncard": "25",
        "bahncard_class": "1",
        "first_class": True,
        "deutschlandticket": True,
    })
    result = bahn_backend.effective_params({})
    assert result["bahncard"] == "25"
    assert result["bahncard_class"] == 1
    assert result["first_class"] is True
    assert result["deutschlandticket"] is True


def test_effective_params_cli_overrides_config(isolated_config):
    bahn_backend.save_config({
        "bahncard": "25",
        "bahncard_class": "1",
        "first_class": True,
        "deutschlandticket": True,
    })
    result = bahn_backend.effective_params({
        "bahncard": "50",
        "bahncard_class": 2,
        "first_class": False,
        "deutschlandticket": False,
    })
    assert result["bahncard"] == "50"
    assert result["bahncard_class"] == 2
    assert result["first_class"] is False
    assert result["deutschlandticket"] is False


def test_effective_params_cli_none_values_do_not_override(isolated_config):
    bahn_backend.save_config({"bahncard": "25"})
    result = bahn_backend.effective_params({
        "bahncard": None,
        "bahncard_class": None,
        "first_class": None,
        "deutschlandticket": None,
    })
    # config value preserved since CLI override is None ("not set")
    assert result["bahncard"] == "25"
    assert result["bahncard_class"] == 2
    assert result["first_class"] is False
    assert result["deutschlandticket"] is False


# ============================================================================
# _request error paths
# ============================================================================

class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


class _FakeSession:
    def __init__(self, responses):
        # responses: list of _FakeResponse to return in sequence
        self._responses = list(responses)
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def test_request_success_returns_json(monkeypatch):
    fake = _FakeSession([_FakeResponse(200, json_data={"ok": True})])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)

    result = bahn_backend._request("GET", "https://example.test/foo")
    assert result == {"ok": True}
    assert fake.calls == 1


@pytest.fixture
def no_rotation(monkeypatch):
    """Schaltet die impersonate-Rotation ab.

    Die folgenden Tests prüfen das Retry-Budget (IP-Reputation), nicht die
    Fingerprint-Rotation, die vorgelagert läuft und sonst die Call-Counts
    verfälscht. Rotation selbst: test_impersonate_rotation.py.
    """
    monkeypatch.setattr(bahn_backend, "_ROTATION", ())


def test_request_403_raises_bahnapierror_with_status_code(monkeypatch, no_rotation):
    fake = _FakeSession([_FakeResponse(403, text="blocked")])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)

    with pytest.raises(bahn_backend.BahnApiError) as excinfo:
        bahn_backend._request("GET", "https://example.test/foo")

    assert excinfo.value.status_code == 403


def test_request_403_retries_until_success(monkeypatch, no_rotation):
    fake = _FakeSession([
        _FakeResponse(403, text="blocked"),
        _FakeResponse(403, text="blocked"),
        _FakeResponse(403, text="blocked"),
        _FakeResponse(200, json_data={"ok": True}),
    ])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)
    monkeypatch.setattr(bahn_backend, "MAX_RETRIES", 3)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda s: None)

    result = bahn_backend._request("POST", "https://example.test/foo")

    assert result == {"ok": True}
    assert fake.calls == 4


def test_request_403_exhausts_budget_then_raises(monkeypatch, no_rotation):
    fake = _FakeSession([
        _FakeResponse(403, text="blocked"),
        _FakeResponse(403, text="blocked"),
    ])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)
    monkeypatch.setattr(bahn_backend, "MAX_RETRIES", 1)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda s: None)

    with pytest.raises(bahn_backend.BahnApiError) as excinfo:
        bahn_backend._request("POST", "https://example.test/foo")

    assert excinfo.value.status_code == 403
    assert fake.calls == 2


def test_request_429_retries_once_then_succeeds(monkeypatch):
    fake = _FakeSession([
        _FakeResponse(429, text="too many requests"),
        _FakeResponse(200, json_data={"ok": True}),
    ])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)

    sleep_calls = []
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda s: sleep_calls.append(s))

    result = bahn_backend._request("GET", "https://example.test/foo")

    assert result == {"ok": True}
    assert fake.calls == 2
    assert sleep_calls == [5]


def test_request_429_twice_raises_after_one_retry(monkeypatch):
    fake = _FakeSession([
        _FakeResponse(429, text="too many requests"),
        _FakeResponse(429, text="still too many"),
    ])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)
    monkeypatch.setattr(bahn_backend.time, "sleep", lambda s: None)

    with pytest.raises(bahn_backend.BahnApiError) as excinfo:
        bahn_backend._request("GET", "https://example.test/foo")

    assert excinfo.value.status_code == 429
    assert fake.calls == 2


def test_request_422_raises_bahnapierror_with_body_excerpt(monkeypatch):
    fake = _FakeSession([_FakeResponse(422, text="Validation failed: bad field")])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)

    with pytest.raises(bahn_backend.BahnApiError) as excinfo:
        bahn_backend._request("GET", "https://example.test/foo")

    assert excinfo.value.status_code == 422
    assert "Validation failed" in str(excinfo.value)


def test_request_other_status_raises_bahnapierror(monkeypatch):
    fake = _FakeSession([_FakeResponse(500, text="server error")])
    monkeypatch.setattr(bahn_backend, "_get_session", lambda: fake)

    with pytest.raises(bahn_backend.BahnApiError) as excinfo:
        bahn_backend._request("GET", "https://example.test/foo")

    assert excinfo.value.status_code == 500


def test_request_network_exception_raises_bahnapierror(monkeypatch):
    class _BoomSession:
        def request(self, *args, **kwargs):
            raise ConnectionError("network down")

    monkeypatch.setattr(bahn_backend, "_get_session", lambda: _BoomSession())

    with pytest.raises(bahn_backend.BahnApiError):
        bahn_backend._request("GET", "https://example.test/foo")


# ============================================================================
# CLI: stations
# ============================================================================

def test_cli_stations_human(monkeypatch, orte_response):
    monkeypatch.setattr(bahn_de_cli.backend, "search_stations", lambda *a, **kw: orte_response)

    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["stations", "Hamburg"])

    assert result.exit_code == 0
    assert "HAMBURG" in result.output
    assert "Name" in result.output  # table header


def test_cli_stations_json(monkeypatch, orte_response):
    monkeypatch.setattr(bahn_de_cli.backend, "search_stations", lambda *a, **kw: orte_response)

    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["--json", "stations", "Hamburg"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == orte_response


def test_cli_stations_error_human(monkeypatch):
    def fake_search_stations(*a, **kw):
        raise bahn_backend.BahnApiError("No station found for 'xyz'.")

    monkeypatch.setattr(bahn_de_cli.backend, "search_stations", fake_search_stations)

    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["stations", "xyz"])

    assert result.exit_code == 1
    assert "Fehler:" in result.output


def test_cli_stations_error_json(monkeypatch):
    def fake_search_stations(*a, **kw):
        raise bahn_backend.BahnApiError("No station found for 'xyz'.")

    monkeypatch.setattr(bahn_de_cli.backend, "search_stations", fake_search_stations)

    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["--json", "stations", "xyz"])

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["type"] == "BahnApiError"
    assert "error" in data


# ============================================================================
# CLI: search
# ============================================================================

@pytest.fixture
def stub_resolve_station(monkeypatch):
    def fake_resolve(value):
        return {"id": f"LID-{value}", "name": value, "extId": "12345", "alternatives": []}

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)
    return fake_resolve


def test_cli_search_human(monkeypatch, stub_resolve_station, fahrplan_response, isolated_config):
    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", lambda payload: fahrplan_response)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["search", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 0
    assert "77.99" in result.output


def test_cli_search_json(monkeypatch, stub_resolve_station, fahrplan_response, isolated_config):
    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", lambda payload: fahrplan_response)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "search", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["trip_id"] == fahrplan_response["verbindungen"][0]["tripId"]
    assert data[0]["preis"] == 77.99


def test_cli_search_with_bahncard_builds_correct_payload(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    captured = {}

    def fake_search_connections(payload):
        captured["payload"] = payload
        return fahrplan_response

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fake_search_connections)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Hamburg Hbf", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--bahncard", "25",
        ],
    )

    assert result.exit_code == 0
    assert captured["payload"]["reisende"][0]["ermaessigungen"] == [
        {"art": "BAHNCARD25", "klasse": "KLASSE_2"}
    ]


def test_cli_search_error_json(monkeypatch, isolated_config):
    def fake_resolve(value):
        raise bahn_backend.BahnApiError(f"No station found for '{value}'.")

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "search", "Nonexistent", "Berlin Hbf", "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["type"] == "BahnApiError"


def test_cli_search_error_human_exit_code(monkeypatch, isolated_config):
    def fake_resolve(value):
        raise bahn_backend.BahnApiError(f"No station found for '{value}'.")

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["search", "Nonexistent", "Berlin Hbf", "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 1
    assert "Fehler:" in result.output


# ============================================================================
# CLI: search --around / --before / --after (search_window)
# ============================================================================

def test_cli_search_around_calls_search_window(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    captured = {}

    def fake_search_window(payload, when_iso, *, before, after):
        captured["payload"] = payload
        captured["when_iso"] = when_iso
        captured["before"] = before
        captured["after"] = after
        return fahrplan_response["verbindungen"]

    def fail_search_connections(payload):
        raise AssertionError("search_connections should not be called when --around is set")

    monkeypatch.setattr(bahn_de_cli.backend, "search_window", fake_search_window)
    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fail_search_connections)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Hamburg Hbf", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--around", "60",
        ],
    )

    assert result.exit_code == 0
    assert captured["before"] == 60
    assert captured["after"] == 60
    assert captured["when_iso"] == "2026-06-19T09:00:00"
    assert "77.99" in result.output


def test_cli_search_around_json(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_cli.backend, "search_window",
        lambda payload, when_iso, *, before, after: fahrplan_response["verbindungen"],
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "--json", "search", "Hamburg Hbf", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--around", "30",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["trip_id"] == fahrplan_response["verbindungen"][0]["tripId"]


def test_cli_search_before_after_explicit_values(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    captured = {}

    def fake_search_window(payload, when_iso, *, before, after):
        captured["before"] = before
        captured["after"] = after
        return fahrplan_response["verbindungen"]

    monkeypatch.setattr(bahn_de_cli.backend, "search_window", fake_search_window)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Hamburg Hbf", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--before", "15", "--after", "45",
        ],
    )

    assert result.exit_code == 0
    assert captured["before"] == 15
    assert captured["after"] == 45


def test_cli_search_without_window_options_calls_search_connections(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    called = {"search_connections": False, "search_window": False}

    def fake_search_connections(payload):
        called["search_connections"] = True
        return fahrplan_response

    def fake_search_window(*a, **kw):
        called["search_window"] = True
        return []

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fake_search_connections)
    monkeypatch.setattr(bahn_de_cli.backend, "search_window", fake_search_window)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["search", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 0
    assert called["search_connections"] is True
    assert called["search_window"] is False


# ============================================================================
# CLI: search --min-transfer-time / --max-transfer-time
# ============================================================================

def test_cli_search_min_transfer_time_in_payload(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    captured = {}

    def fake_search_connections(payload):
        captured["payload"] = payload
        return fahrplan_response

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fake_search_connections)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Hamburg Hbf", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--min-transfer-time", "15",
        ],
    )

    assert result.exit_code == 0
    assert captured["payload"]["minUmstiegszeit"] == 15


def test_cli_search_max_transfer_time_filters_human(
    monkeypatch, stub_resolve_station, fahrplan_umstieg_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_cli.backend, "search_connections", lambda payload: fahrplan_umstieg_response
    )

    runner = CliRunner()

    # layovers range 10-13 min; max-transfer-time 9 should drop every connection
    result_strict = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Rendsburg", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--max-transfer-time", "9",
        ],
    )
    assert result_strict.exit_code == 0
    assert "Keine Verbindungen gefunden." in result_strict.output

    # max-transfer-time 20 should keep them
    result_lenient = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Rendsburg", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--max-transfer-time", "20",
        ],
    )
    assert result_lenient.exit_code == 0
    assert "1 (13m)" in result_lenient.output


def test_cli_search_max_transfer_time_filters_json(
    monkeypatch, stub_resolve_station, fahrplan_umstieg_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_cli.backend, "search_connections", lambda payload: fahrplan_umstieg_response
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "--json", "search", "Rendsburg", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
            "--max-transfer-time", "9",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == []


def test_cli_search_without_max_transfer_time_keeps_all(
    monkeypatch, stub_resolve_station, fahrplan_umstieg_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_cli.backend, "search_connections", lambda payload: fahrplan_umstieg_response
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "--json", "search", "Rendsburg", "Berlin Hbf",
            "--date", "2026-06-19", "--time", "09:00",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == len(fahrplan_umstieg_response["verbindungen"])


# ============================================================================
# CLI: best

# ============================================================================

def test_cli_best_human(monkeypatch, stub_resolve_station, bestpreis_response, isolated_config):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    monkeypatch.setattr(
        bahn_de_cli.backend, "day_best_prices", lambda payload, **kw: (flat, "bestpreis-endpoint")
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["best", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19"],
    )

    assert result.exit_code == 0
    assert "bestpreis-endpoint" in result.output


def test_cli_best_json(monkeypatch, stub_resolve_station, bestpreis_response, isolated_config):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    monkeypatch.setattr(
        bahn_de_cli.backend, "day_best_prices", lambda payload, **kw: (flat, "bestpreis-endpoint")
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "best", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["source"] == "bestpreis-endpoint"
    assert isinstance(data["connections"], list)
    assert len(data["connections"]) <= 5
    # sorted ascending by price (None last)
    prices = [c["preis"] for c in data["connections"] if c["preis"] is not None]
    assert prices == sorted(prices)


def test_cli_best_top_option(monkeypatch, stub_resolve_station, bestpreis_response, isolated_config):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    monkeypatch.setattr(
        bahn_de_cli.backend, "day_best_prices", lambda payload, **kw: (flat, "bestpreis-endpoint")
    )

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "best", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19", "--top", "2"],
    )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["connections"]) <= 2


def test_cli_best_paging_fallback_note_on_stderr(
    monkeypatch, stub_resolve_station, fahrplan_regional_response, isolated_config
):
    parsed = [
        formatters.parse_connection(v) for v in fahrplan_regional_response["verbindungen"]
    ]
    monkeypatch.setattr(
        bahn_de_cli.backend, "day_best_prices",
        lambda payload, **kw: (fahrplan_regional_response["verbindungen"], "paging-fallback"),
    )

    # Click >=8.2 entfernte `mix_stderr`; stderr ist jetzt immer getrennt (result.stderr)
    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["best", "Hamburg Hbf", "Berlin Hbf", "--date", "2026-06-19"],
    )

    assert result.exit_code == 0
    assert "seitenweise" in result.stderr.lower()


def test_cli_best_error_json(monkeypatch, isolated_config):
    def fake_resolve(value):
        raise bahn_backend.BahnApiError(f"No station found for '{value}'.")

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "best", "Nonexistent", "Berlin Hbf", "--date", "2026-06-19"],
    )

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert "error" in data


# ============================================================================
# CLI: config set/get/path
# ============================================================================

def test_cli_config_set_get_path_human(isolated_config):
    runner = CliRunner()

    result = runner.invoke(bahn_de_cli.cli, ["config", "set", "bahncard", "25"])
    assert result.exit_code == 0
    assert "bahncard" in result.output

    result = runner.invoke(bahn_de_cli.cli, ["config", "get", "bahncard"])
    assert result.exit_code == 0
    assert "25" in result.output

    result = runner.invoke(bahn_de_cli.cli, ["config", "get"])
    assert result.exit_code == 0
    assert "bahncard" in result.output

    result = runner.invoke(bahn_de_cli.cli, ["config", "path"])
    assert result.exit_code == 0
    assert str(bahn_backend.CONFIG_FILE) in result.output


def test_cli_config_set_get_json(isolated_config):
    runner = CliRunner()

    result = runner.invoke(bahn_de_cli.cli, ["--json", "config", "set", "first_class", "true"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == {"first_class": True}

    result = runner.invoke(bahn_de_cli.cli, ["--json", "config", "get", "first_class"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == {"first_class": True}

    result = runner.invoke(bahn_de_cli.cli, ["--json", "config", "path"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["path"] == str(bahn_backend.CONFIG_FILE)


def test_cli_config_set_none_deletes_key(isolated_config):
    runner = CliRunner()
    runner.invoke(bahn_de_cli.cli, ["config", "set", "bahncard", "25"])

    result = runner.invoke(bahn_de_cli.cli, ["--json", "config", "set", "bahncard", "none"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == {"deleted": "bahncard"}

    cfg = bahn_backend.load_config()
    assert "bahncard" not in cfg


def test_cli_config_set_unknown_key_errors(isolated_config):
    runner = CliRunner()

    result = runner.invoke(bahn_de_cli.cli, ["config", "set", "totally_unknown_key", "x"])
    assert result.exit_code == 1
    assert "Fehler:" in result.output


def test_cli_config_set_unknown_key_errors_json(isolated_config):
    runner = CliRunner()

    result = runner.invoke(bahn_de_cli.cli, ["--json", "config", "set", "totally_unknown_key", "x"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["type"] == "ValueError"


def test_cli_config_set_invalid_value_errors(isolated_config):
    runner = CliRunner()

    result = runner.invoke(bahn_de_cli.cli, ["config", "set", "bahncard", "75"])
    assert result.exit_code == 1
    assert "Fehler:" in result.output


# ============================================================================
# build_journey_payload: zwischenhalte
# ============================================================================

def test_build_journey_payload_zwischenhalte_absent_by_default():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    assert "zwischenhalte" not in payload


def test_build_journey_payload_zwischenhalte_none_absent():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", zwischenhalte=None)
    assert "zwischenhalte" not in payload


def test_build_journey_payload_zwischenhalte_empty_list_absent():
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", zwischenhalte=[])
    assert "zwischenhalte" not in payload


def test_build_journey_payload_zwischenhalte_set():
    via = [{"id": "LID-Neanderthal", "aufenthaltsdauer": 4320}]
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", zwischenhalte=via)
    assert payload["zwischenhalte"] == via
    # rest of the payload unchanged
    default = bahn_backend.build_journey_payload("<from>", "<to>", "<when>")
    for k in default:
        assert payload[k] == default[k]


def test_build_journey_payload_zwischenhalte_two_stops():
    via = [
        {"id": "LID-A", "aufenthaltsdauer": 1440},
        {"id": "LID-B", "aufenthaltsdauer": 2880},
    ]
    payload = bahn_backend.build_journey_payload("<from>", "<to>", "<when>", zwischenhalte=via)
    assert len(payload["zwischenhalte"]) == 2
    assert payload["zwischenhalte"][0]["id"] == "LID-A"
    assert payload["zwischenhalte"][1]["id"] == "LID-B"


# ============================================================================
# recon_price
# ============================================================================

@pytest.fixture
def recon_response():
    return _load_fixture("recon_response.json")


def test_recon_price_parses_preis_and_teilpreise(monkeypatch, recon_response):
    monkeypatch.setattr(bahn_backend, "_request", lambda *a, **kw: recon_response)

    reisende = [{"typ": "ERWACHSENER", "ermaessigungen": [], "alter": [], "anzahl": 1}]
    result = bahn_backend.recon_price(
        "ctx-token-abc123",
        first_class=False,
        deutschlandticket=False,
        reisende=reisende,
    )

    assert result["preis"] == 60.0
    assert result["waehrung"] == "EUR"
    assert result["has_teilpreis"] is True
    assert len(result["teilpreise"]) == 2
    assert result["teilpreise"][0]["betrag"] == 25.0
    assert result["teilpreise"][1]["betrag"] == 35.0


def test_recon_price_sends_correct_body(monkeypatch, recon_response):
    captured = {}

    def fake_request(method, url, *, params=None, json_body=None, timeout=30, _retried=False):
        captured["method"] = method
        captured["url"] = url
        captured["body"] = json_body
        return recon_response

    monkeypatch.setattr(bahn_backend, "_request", fake_request)

    reisende = [{"typ": "ERWACHSENER", "ermaessigungen": [], "alter": [], "anzahl": 1}]
    bahn_backend.recon_price(
        "my-ctx-token",
        first_class=True,
        deutschlandticket=True,
        reisende=reisende,
    )

    assert captured["method"] == "POST"
    assert "recon" in captured["url"]
    assert captured["body"]["ctxRecon"] == "my-ctx-token"
    assert captured["body"]["klasse"] == "KLASSE_1"
    assert captured["body"]["deutschlandTicketVorhanden"] is True
    assert captured["body"]["reisende"] == reisende


def test_recon_price_empty_verbindungen_returns_none(monkeypatch):
    monkeypatch.setattr(bahn_backend, "_request", lambda *a, **kw: {"verbindungen": []})
    reisende = [{"typ": "ERWACHSENER", "ermaessigungen": [], "alter": [], "anzahl": 1}]
    result = bahn_backend.recon_price("ctx", reisende=reisende)
    assert result["preis"] is None
    assert result["waehrung"] is None
    assert result["teilpreise"] == []
    assert result["has_teilpreis"] is False


def test_recon_price_missing_angebotspreis_returns_none(monkeypatch):
    data = {"verbindungen": [{"tripId": "x", "hasTeilpreis": False, "reiseAngebote": []}]}
    monkeypatch.setattr(bahn_backend, "_request", lambda *a, **kw: data)
    reisende = [{"typ": "ERWACHSENER", "ermaessigungen": [], "alter": [], "anzahl": 1}]
    result = bahn_backend.recon_price("ctx", reisende=reisende)
    assert result["preis"] is None
    assert result["teilpreise"] == []


# ============================================================================
# resolve_via_dwell
# ============================================================================

@pytest.fixture
def fahrplan_zwischenhalt_response():
    return _load_fixture("fahrplan_zwischenhalt_response.json")


def _presearch_response(arrival_iso):
    """Minimal from->via search response: one connection ending at the via stop."""
    return {
        "verbindungen": [
            {
                "tripId": "presearch-trip-1",
                "umstiegsAnzahl": 0,
                "verbindungsDauerInSeconds": 3600,
                "verbindungsAbschnitte": [
                    {
                        "abfahrt": {"sollzeit": "2026-07-03T09:02:00"},
                        "ankunft": {"sollzeit": arrival_iso},
                        "abfahrtsOrt": "Rendsburg",
                        "ankunftsOrt": "Neanderthal",
                        "abschnittsDauer": 3600,
                        "verkehrsmittel": {"typ": "PUBLICTRANSPORT", "name": "RE 7"},
                    }
                ],
            }
        ]
    }


def test_resolve_via_dwell_computes_minutes(monkeypatch):
    # Earliest arrival at via: 2026-07-03T14:51:00, weiterreise: 2026-07-06T08:00
    monkeypatch.setattr(
        bahn_backend, "search_connections",
        lambda *a, **kw: _presearch_response("2026-07-03T14:51:00"),
    )
    dwell = bahn_backend.resolve_via_dwell(
        "LID-from", "LID-via", "2026-07-03T09:00:00", "2026-07-06T08:00:00",
        fare_params={},
    )
    from datetime import datetime
    ref = datetime.fromisoformat("2026-07-03T14:51:00")
    wei = datetime.fromisoformat("2026-07-06T08:00:00")
    expected = max(0, int((wei - ref).total_seconds() / 60) - 1)
    assert dwell == expected


def test_resolve_via_dwell_weiterreise_before_arrival_advances_to_next_day(monkeypatch):
    monkeypatch.setattr(
        bahn_backend, "search_connections",
        lambda *a, **kw: _presearch_response("2026-07-03T14:51:00"),
    )
    # Weiterreise (10:00) liegt vor der Ankunft (14:51): das LLM hat den Folgetag
    # gemeint -> resolve_via_dwell rückt um Tage vor, bis die Weiterreise sinnvoll ist.
    dwell = bahn_backend.resolve_via_dwell(
        "LID-from", "LID-via", "2026-07-03T09:00:00", "2026-07-03T10:00:00",
        fare_params={},
    )
    from datetime import datetime, timedelta
    ref = datetime.fromisoformat("2026-07-03T14:51:00")
    wei = datetime.fromisoformat("2026-07-03T10:00:00")
    while wei <= ref:
        wei += timedelta(days=1)
    expected = max(1, int((wei - ref).total_seconds() / 60) - 1)
    assert dwell == expected


def test_resolve_via_dwell_no_connections_raises(monkeypatch):
    monkeypatch.setattr(bahn_backend, "search_connections", lambda *a, **kw: {"verbindungen": []})
    with pytest.raises(bahn_backend.BahnApiError):
        bahn_backend.resolve_via_dwell(
            "LID-from", "LID-via", "2026-07-03T09:00:00", "2026-07-06T08:00:00",
            fare_params={},
        )


def test_resolve_via_dwell_invalid_weiterreise_raises(monkeypatch, fahrplan_zwischenhalt_response):
    monkeypatch.setattr(bahn_backend, "search_connections", lambda *a, **kw: fahrplan_zwischenhalt_response)
    with pytest.raises(ValueError):
        bahn_backend.resolve_via_dwell(
            "LID-from", "LID-via", "2026-07-03T09:00:00", "not-a-date",
            fare_params={},
        )


# ============================================================================
# parse_connection: via_halte and teilpreise
# ============================================================================

def test_parse_connection_via_halt_detected(fahrplan_zwischenhalt_response):
    raw = fahrplan_zwischenhalt_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)

    assert len(parsed["via_halte"]) == 1
    halt = parsed["via_halte"][0]
    assert halt["ort"] == "Neanderthal"
    assert halt["ankunft"] == "2026-07-03T14:51:00"
    assert halt["abfahrt"] == "2026-07-06T15:10:00"
    # gap: 2026-07-06T15:10 - 2026-07-03T14:51 = 4339 min
    from datetime import datetime
    gap = int((datetime.fromisoformat("2026-07-06T15:10:00") -
               datetime.fromisoformat("2026-07-03T14:51:00")).total_seconds() // 60)
    assert halt["aufenthalt_minuten"] == gap


def test_parse_connection_no_via_halt_on_short_connection(fahrplan_response):
    raw = fahrplan_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    assert parsed["via_halte"] == []


def test_parse_connection_teilpreise_from_injected_key():
    raw = {
        "tripId": "x",
        "verbindungsAbschnitte": [],
        "_teilpreise": [
            {"betrag": 25.0, "waehrung": "EUR"},
            {"betrag": 35.0, "waehrung": "EUR"},
        ],
    }
    parsed = formatters.parse_connection(raw)
    assert parsed["teilpreise"] == [
        {"betrag": 25.0, "waehrung": "EUR"},
        {"betrag": 35.0, "waehrung": "EUR"},
    ]


def test_parse_connection_teilpreise_empty_by_default(fahrplan_response):
    raw = fahrplan_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    assert parsed["teilpreise"] == []


def test_parse_connection_has_teilpreis_flag_injected():
    raw = {
        "tripId": "x",
        "verbindungsAbschnitte": [],
        "angebotsPreis": {"betrag": 75.0, "waehrung": "EUR"},
        "_has_teilpreis": True,
        "_teilpreise": [{"betrag": 75.0, "waehrung": "EUR"}],
    }
    parsed = formatters.parse_connection(raw)
    assert parsed["has_teilpreis"] is True


def test_parse_connection_has_teilpreis_false_by_default(fahrplan_response):
    raw = fahrplan_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    assert parsed["has_teilpreis"] is False


def test_format_connections_shows_teilstreckenpreis_warning():
    conn = {
        "abfahrt": "2026-07-04T08:00:00",
        "ankunft": "2026-07-04T18:00:00",
        "dauer_minuten": 600,
        "umstiege": 1,
        "umstiegszeiten_minuten": [30],
        "produkte": ["ICE", "Eurostar"],
        "preis": 75.0,
        "waehrung": "EUR",
        "has_teilpreis": True,
        "via_halte": [],
        "teilpreise": [{"betrag": 75.0, "waehrung": "EUR"}],
    }
    output = formatters.format_connections([conn])
    assert "(Teilstreckenpreis)" in output
    assert "Achtung" in output
    assert "Inlandssegmente" in output


def test_format_connections_no_warning_without_teilpreis():
    conn = {
        "abfahrt": "2026-07-04T08:00:00",
        "ankunft": "2026-07-04T18:00:00",
        "dauer_minuten": 600,
        "umstiege": 0,
        "umstiegszeiten_minuten": [],
        "produkte": ["ICE"],
        "preis": 49.0,
        "waehrung": "EUR",
        "has_teilpreis": False,
        "via_halte": [],
        "teilpreise": [],
    }
    output = formatters.format_connections([conn])
    assert "Teilstreckenpreis" not in output
    assert "Achtung" not in output


# ============================================================================
# _parse_via
# ============================================================================

def test_parse_via_at_form_calls_resolve_via_dwell(monkeypatch, fahrplan_zwischenhalt_response):
    resolved_stations = {}

    def fake_resolve(name):
        resolved_stations[name] = True
        return {"id": f"LID-{name}", "name": name, "extId": None, "alternatives": []}

    dwell_calls = {}

    def fake_resolve_via_dwell(from_lid, via_lid, when_iso, weiterreise_iso, fare_params):
        dwell_calls["from_lid"] = from_lid
        dwell_calls["via_lid"] = via_lid
        dwell_calls["weiterreise_iso"] = weiterreise_iso
        return 4320

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)
    monkeypatch.setattr(bahn_de_cli.backend, "resolve_via_dwell", fake_resolve_via_dwell)

    fare = FareParams(first_class=False, bahncard=None, bahncard_class=2, deutschlandticket=False)
    zwischenhalt, notices = bahn_de_service._parse_via(
        "Neanderthal@2026-07-06T08:00",
        "LID-from",
        "2026-07-03T09:00:00",
        fare,
    )

    assert zwischenhalt["id"] == "LID-Neanderthal"
    assert zwischenhalt["aufenthaltsdauer"] == 4320
    assert dwell_calls["via_lid"] == "LID-Neanderthal"
    assert dwell_calls["weiterreise_iso"] == "2026-07-06T08:00:00"
    assert notices == []


def test_parse_via_at_form_seconds_already_present(monkeypatch):
    def fake_resolve(name):
        return {"id": f"LID-{name}", "name": name, "extId": None, "alternatives": []}

    def fake_dwell(from_lid, via_lid, when_iso, weiterreise_iso, fare_params):
        assert weiterreise_iso == "2026-07-06T08:00:30"
        return 100

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)
    monkeypatch.setattr(bahn_de_cli.backend, "resolve_via_dwell", fake_dwell)

    fare = FareParams(first_class=False, bahncard=None, bahncard_class=2, deutschlandticket=False)
    zwischenhalt, _notices = bahn_de_service._parse_via(
        "Neanderthal@2026-07-06T08:00:30", "LID-from", "2026-07-03T09:00:00", fare
    )
    assert zwischenhalt["aufenthaltsdauer"] == 100


@pytest.mark.parametrize("value,expected_minutes", [
    ("Neanderthal:72h", 72 * 60),
    ("Neanderthal:3d", 3 * 24 * 60),
    ("Neanderthal:4320m", 4320),
    ("Neanderthal:4320", 4320),
])
def test_parse_via_dur_form(monkeypatch, value, expected_minutes):
    def fake_resolve(name):
        return {"id": f"LID-{name}", "name": name, "extId": None, "alternatives": []}

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)

    fare = FareParams(first_class=False, bahncard=None, bahncard_class=2, deutschlandticket=False)
    zwischenhalt, _notices = bahn_de_service._parse_via(value, "LID-from", "2026-07-03T09:00:00", fare)
    assert zwischenhalt["aufenthaltsdauer"] == expected_minutes


def test_parse_via_invalid_format_raises(monkeypatch):
    def fake_resolve(name):
        return {"id": f"LID-{name}", "name": name, "extId": None, "alternatives": []}

    monkeypatch.setattr(bahn_de_cli.backend, "resolve_station", fake_resolve)

    fare = FareParams(first_class=False, bahncard=None, bahncard_class=2, deutschlandticket=False)
    with pytest.raises(ValueError):
        bahn_de_service._parse_via("JustAStationName", "LID-from", "2026-07-03T09:00:00", fare)


# ============================================================================
# CLI: search --via (unit)
# ============================================================================

def test_cli_search_via_builds_payload_with_zwischenhalte(
    monkeypatch, stub_resolve_station, fahrplan_zwischenhalt_response, recon_response, isolated_config
):
    captured_payloads = []

    def fake_search_connections(payload):
        captured_payloads.append(payload)
        return fahrplan_zwischenhalt_response

    def fake_recon_price(ctx_recon, *, first_class, deutschlandticket, reisende):
        return {"preis": 60.0, "waehrung": "EUR", "has_teilpreis": True,
                "teilpreise": [{"betrag": 25.0, "waehrung": "EUR"}]}

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fake_search_connections)
    monkeypatch.setattr(bahn_de_cli.backend, "recon_price", fake_recon_price)
    monkeypatch.setattr(bahn_de_cli.backend, "resolve_via_dwell", lambda *a, **kw: 4320)
    monkeypatch.setattr(bahn_de_service._time, "sleep", lambda s: None)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "--json", "search", "Rendsburg", "Brussel-Noord",
            "--date", "2026-07-03", "--time", "09:00",
            "--via", "Neanderthal@2026-07-06T08:00",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) > 0
    # Price injected from recon
    assert data[0]["preis"] == 60.0

    # Payload sent to search_connections must contain zwischenhalte
    assert len(captured_payloads) == 1
    assert "zwischenhalte" in captured_payloads[0]
    assert len(captured_payloads[0]["zwischenhalte"]) == 1
    assert captured_payloads[0]["zwischenhalte"][0]["aufenthaltsdauer"] == 4320


def test_cli_search_via_default_limit_reduced(
    monkeypatch, stub_resolve_station, fahrplan_zwischenhalt_response, isolated_config
):
    captured = {}

    def fake_search_connections(payload):
        captured["payload"] = payload
        return fahrplan_zwischenhalt_response

    def fake_recon_price(ctx_recon, *, first_class, deutschlandticket, reisende):
        return {"preis": 60.0, "waehrung": "EUR", "has_teilpreis": False, "teilpreise": []}

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", fake_search_connections)
    monkeypatch.setattr(bahn_de_cli.backend, "recon_price", fake_recon_price)
    monkeypatch.setattr(bahn_de_cli.backend, "resolve_via_dwell", lambda *a, **kw: 72 * 60)
    monkeypatch.setattr(bahn_de_service._time, "sleep", lambda s: None)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "--json", "search", "Rendsburg", "Brussel-Noord",
            "--date", "2026-07-03", "--time", "09:00",
            "--via", "Neanderthal:72h",
            # no --limit given -> must default to 5
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Fixture has 2 connections, both within default limit=5
    assert len(data) == 2


def test_cli_search_via_too_many_raises(
    monkeypatch, stub_resolve_station, fahrplan_zwischenhalt_response, isolated_config
):
    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        [
            "search", "Rendsburg", "Brussel-Noord",
            "--date", "2026-07-03", "--time", "09:00",
            "--via", "Neanderthal:72h",
            "--via", "Koeln:24h",
            "--via", "Aachen:12h",  # third via -> error
        ],
    )

    assert result.exit_code == 1
    assert "Maximal 2" in result.output or "Fehler" in result.output


def test_cli_search_without_via_unchanged(
    monkeypatch, stub_resolve_station, fahrplan_response, isolated_config
):
    called = {"recon": False}

    def fail_recon(*a, **kw):
        called["recon"] = True
        raise AssertionError("recon_price must not be called without --via")

    monkeypatch.setattr(bahn_de_cli.backend, "search_connections", lambda p: fahrplan_response)
    monkeypatch.setattr(bahn_de_cli.backend, "recon_price", fail_recon)

    runner = CliRunner()
    result = runner.invoke(
        bahn_de_cli.cli,
        ["--json", "search", "Hamburg Hbf", "Berlin Hbf",
         "--date", "2026-06-19", "--time", "09:00"],
    )

    assert result.exit_code == 0
    assert called["recon"] is False


# ============================================================================
# models: validation / roundtrip
# ============================================================================

def test_model_connection_roundtrip_from_parse_connection(fahrplan_response):
    raw = fahrplan_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)

    conn = Connection.model_validate(parsed)
    # model_dump must equal the public fields of parsed (private _raw_abschnitte is
    # stripped by extra="ignore"; new score fields default to None).
    expected = {k: v for k, v in parsed.items() if not k.startswith("_")}
    expected.setdefault("verbindungsscore", None)
    expected.setdefault("puenktlichkeit", None)
    assert conn.model_dump() == expected
    # nested legs become Section models
    assert isinstance(conn.abschnitte[0], Section)
    assert conn.trip_id == parsed["trip_id"]
    assert conn.preis == 77.99


def test_model_connection_roundtrip_with_via_and_teilpreise(fahrplan_zwischenhalt_response):
    raw = fahrplan_zwischenhalt_response["verbindungen"][0]
    parsed = formatters.parse_connection(raw)
    conn = Connection.model_validate(parsed)
    expected = {k: v for k, v in parsed.items() if not k.startswith("_")}
    expected.setdefault("verbindungsscore", None)
    expected.setdefault("puenktlichkeit", None)
    assert conn.model_dump() == expected
    if conn.via_halte:
        assert isinstance(conn.via_halte[0], ViaHalt)


def test_model_connection_ignores_extra_keys():
    conn = Connection.model_validate({"trip_id": "x", "unbekannt": 123})
    assert conn.trip_id == "x"
    assert not hasattr(conn, "unbekannt")


def test_model_station_roundtrip(orte_response):
    # build a station dict like resolve_station returns
    first = orte_response[0]
    station_dict = {
        "id": first["id"], "name": first["name"],
        "extId": first["extId"], "alternatives": [m["name"] for m in orte_response[1:]],
    }
    station = Station.model_validate(station_dict)
    assert station.model_dump() == station_dict


def test_model_teilpreis_validates():
    tp = Teilpreis.model_validate({"betrag": 25.0, "waehrung": "EUR"})
    assert tp.betrag == 25.0
    assert tp.waehrung == "EUR"


def test_model_connection_json_schema_is_serializable():
    schema = Connection.model_json_schema()
    # must be JSON-serializable and contain the deutsche field names
    dumped = json.dumps(schema)
    assert "trip_id" in dumped
    assert "abschnitte" in dumped
    assert "via_halte" in dumped


# ============================================================================
# service: search_journeys / day_best / resolve_station
# ============================================================================

@pytest.fixture
def service_stub_resolve(monkeypatch):
    def fake_resolve(value):
        return {"id": f"LID-{value}", "name": value, "extId": "12345", "alternatives": []}

    monkeypatch.setattr(bahn_de_service.backend, "resolve_station", fake_resolve)
    return fake_resolve


def test_service_search_journeys_returns_connection_models(
    monkeypatch, service_stub_resolve, fahrplan_response, isolated_config
):
    monkeypatch.setattr(bahn_de_service.backend, "search_connections", lambda payload: fahrplan_response)

    fare = bahn_de_service.resolve_fare({})
    result = bahn_de_service.search_journeys(
        "Hamburg Hbf", "Berlin Hbf",
        when_iso="2026-06-19T09:00:00",
        fare=fare,
    )

    assert isinstance(result.connections, list)
    assert all(isinstance(c, bahn_models.Connection) for c in result.connections)
    assert result.connections[0].trip_id == fahrplan_response["verbindungen"][0]["tripId"]
    assert result.connections[0].preis == 77.99


def test_service_search_journeys_limit(
    monkeypatch, service_stub_resolve, fahrplan_regional_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_service.backend, "search_connections", lambda payload: fahrplan_regional_response
    )

    fare = bahn_de_service.resolve_fare({})
    result = bahn_de_service.search_journeys(
        "Hamburg Hbf", "Berlin Hbf",
        when_iso="2026-06-19T09:00:00",
        fare=fare,
        limit=2,
    )
    assert len(result.connections) == 2


def test_service_search_journeys_via_enriches_price(
    monkeypatch, service_stub_resolve, fahrplan_zwischenhalt_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_service.backend, "search_connections", lambda payload: fahrplan_zwischenhalt_response
    )
    monkeypatch.setattr(
        bahn_de_service.backend, "recon_price",
        lambda ctx, **kw: {"preis": 60.0, "waehrung": "EUR", "has_teilpreis": True,
                           "teilpreise": [{"betrag": 25.0, "waehrung": "EUR"}]},
    )
    monkeypatch.setattr(bahn_de_service.backend, "resolve_via_dwell", lambda *a, **kw: 4320)
    monkeypatch.setattr(bahn_de_service._time, "sleep", lambda s: None)

    fare = bahn_de_service.resolve_fare({})
    result = bahn_de_service.search_journeys(
        "Rendsburg", "Brussel-Noord",
        when_iso="2026-07-03T09:00:00",
        fare=fare,
        via_values=("Neanderthal@2026-07-06T08:00",),
    )
    assert result.connections[0].preis == 60.0
    assert result.connections[0].has_teilpreis is True
    # via hint surfaced in notices, not printed
    assert any("Zwischenhalt" in n for n in result.notices)


def test_service_day_best_returns_sorted_models(
    monkeypatch, service_stub_resolve, bestpreis_response, isolated_config
):
    flat = bahn_backend._flatten_bestpreis(bestpreis_response)
    monkeypatch.setattr(
        bahn_de_service.backend, "day_best_prices",
        lambda payload, **kw: (flat, "bestpreis-endpoint"),
    )

    fare = bahn_de_service.resolve_fare({})
    result = bahn_de_service.day_best(
        "Hamburg Hbf", "Berlin Hbf", date="2026-06-19", fare=fare, top=3,
    )

    assert result.source == "bestpreis-endpoint"
    assert all(isinstance(c, bahn_models.Connection) for c in result.connections)
    assert len(result.connections) <= 3
    prices = [c.preis for c in result.connections if c.preis is not None]
    assert prices == sorted(prices)


def test_service_day_best_paging_notice(
    monkeypatch, service_stub_resolve, fahrplan_regional_response, isolated_config
):
    monkeypatch.setattr(
        bahn_de_service.backend, "day_best_prices",
        lambda payload, **kw: (fahrplan_regional_response["verbindungen"], "paging-fallback"),
    )

    fare = bahn_de_service.resolve_fare({})
    result = bahn_de_service.day_best(
        "Hamburg Hbf", "Berlin Hbf", date="2026-06-19", fare=fare,
    )
    assert result.source == "paging-fallback"
    assert any("seitenweise" in n.lower() for n in result.notices)


def test_service_resolve_station_surfaces_ambiguity_notice(monkeypatch, orte_response):
    monkeypatch.setattr(
        bahn_de_service.backend, "resolve_station",
        lambda value: {
            "id": orte_response[0]["id"], "name": orte_response[0]["name"],
            "extId": orte_response[0]["extId"],
            "alternatives": [m["name"] for m in orte_response[1:]],
        },
    )
    resolved = bahn_de_service.resolve_station("Hamburg")
    assert isinstance(resolved.station, bahn_models.Station)
    assert resolved.notices  # ambiguity hint present
    assert "aufgelöst" in resolved.notices[0]


# ============================================================================
# CLI: schema command
# ============================================================================

def test_cli_schema_default_connection():
    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["--json", "schema"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == bahn_models.Connection.model_json_schema()


def test_cli_schema_station():
    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["--json", "schema", "--model", "station"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data == bahn_models.Station.model_json_schema()


def test_cli_schema_invalid_model_rejected():
    runner = CliRunner()
    result = runner.invoke(bahn_de_cli.cli, ["schema", "--model", "nonexistent"])
    assert result.exit_code != 0
