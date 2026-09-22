"""Tests für den Omio-Provider (Mapping, Ortsauflösung, 403-Rotation). Kein Netz."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bahn_de import omio

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "omio_results.json").read_text()
)


@pytest.fixture(autouse=True)
def clear_cache():
    omio._position_cache.clear()
    yield
    omio._position_cache.clear()


@pytest.fixture
def fake_api(monkeypatch):
    """Mockt _request und protokolliert, was der Provider tatsächlich schickt."""
    calls = {"suggest": [], "create": [], "results": 0}

    def _request(session, method, path, **kwargs):
        if "suggester-api" in path:
            term = kwargs["params"]["term"]
            calls["suggest"].append(term)
            return [{"positionId": f"pid-{term.lower()}", "type": "location",
                     "displayName": term}]
        if path.endswith("/searches"):
            calls["create"].append(kwargs["json"])
            return {"searchId": "SID123"}
        calls["results"] += 1
        return _FIXTURE

    monkeypatch.setattr(omio, "_request", _request)
    monkeypatch.setattr(omio, "_POLL_WAIT", 0)
    return calls


# --- Ortsauflösung ------------------------------------------------------------

def test_resolve_position_caches(fake_api):
    session = object()
    first = omio.resolve_position(session, "Vienna")
    second = omio.resolve_position(session, "vienna  ")
    assert first == second
    assert calls_once(fake_api["suggest"], "Vienna")


def calls_once(seq, term):
    return seq.count(term) == 1 and len(seq) == 1


def test_resolve_position_unknown(monkeypatch):
    monkeypatch.setattr(omio, "_request", lambda *a, **k: [])
    with pytest.raises(omio.OmioError, match="kennt keinen Ort"):
        omio.resolve_position(object(), "Nirgendwo")


# --- Suchanlage ---------------------------------------------------------------

def test_create_search_wraps_body_and_sets_identifier(fake_api):
    omio.search_journeys_omio("Vienna", "Zagreb", when_iso="2026-08-05")
    body = fake_api["create"][0]
    # Das Wrapping ist der Kern des Fixes: flach antwortet die API 400 ohne Text.
    assert set(body) == {"searchOptions"}
    options = body["searchOptions"]
    assert options["departurePosition"]["id"] == "pid-vienna"
    assert options["arrivalPosition"]["id"] == "pid-zagreb"
    assert options["departureDate"] == "2026-08-05"
    # identifier ist Pflichtfeld; fehlt es, gibt Omio 400 ohne Fehlertext.
    assert options["userInfo"]["identifier"]
    assert options["passengers"][0]["age"] == 26


def test_invalid_date_rejected():
    with pytest.raises(omio.OmioError, match="Ungueltiges Datum"):
        omio.search_journeys_omio("Vienna", "Zagreb", when_iso="05.08.2026")


# --- Mapping ------------------------------------------------------------------

def test_maps_prices_from_cents(fake_api):
    result = omio.search_journeys_omio("Vienna", "Zagreb", when_iso="2026-08-05", limit=99)
    assert result.connections
    for connection in result.connections:
        raw = _FIXTURE["outbounds"][_key_for(connection)]["price"]
        assert connection.preis == raw / 100
        assert connection.waehrung == "EUR"


def _key_for(connection):
    for key, outbound in _FIXTURE["outbounds"].items():
        if outbound["outboundId"] == connection.trip_id:
            return key
    raise AssertionError(f"kein Outbound zu {connection.trip_id}")


def test_maps_train_legs_with_train_numbers(fake_api):
    result = omio.search_journeys_omio("Vienna", "Zagreb", when_iso="2026-08-05", limit=99)
    trains = [c for c in result.connections if "TRAIN" in c.produkte]
    assert trains, "Fixture enthaelt eine Bahnverbindung"
    train = trains[0]
    assert train.abschnitte
    assert all(section.abfahrt_ort and section.ankunft_ort for section in train.abschnitte)
    # transportId ist die Zugnummer ("RJX 133"); genau die soll im Abschnitt stehen.
    assert any(section.produkt for section in train.abschnitte)
    assert train.abfahrt and "+" not in train.abfahrt  # naive lokale Zeit


def test_drops_connections_without_price(fake_api, monkeypatch):
    stripped = json.loads(json.dumps(_FIXTURE))
    for outbound in stripped["outbounds"].values():
        outbound["price"] = None
    monkeypatch.setattr(omio, "_poll_results", lambda *a, **k: stripped)
    result = omio.search_journeys_omio("Vienna", "Zagreb", when_iso="2026-08-05")
    assert result.connections == []
    assert any("keine buchbare Verbindung" in n for n in result.notices)


def test_sorted_by_departure_and_limited(fake_api):
    result = omio.search_journeys_omio("Vienna", "Zagreb", when_iso="2026-08-05", limit=2)
    assert len(result.connections) == 2
    departures = [c.abfahrt for c in result.connections]
    assert departures == sorted(departures)


# --- 403-Rotation -------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = "blocked" if status != 200 else "ok"

    def json(self):
        return self._payload


def test_request_rotates_impersonate_on_403(monkeypatch):
    seen = []

    class _Session:
        impersonate = omio._IMPERSONATE_FALLBACKS[0]
        _omio_impersonate = omio._IMPERSONATE_FALLBACKS[0]

        def request(self, method, url, **kwargs):
            seen.append(self._omio_impersonate)
            # Erst das dritte Target kommt durch.
            return _Resp(200, {"ok": True}) if len(seen) == 3 else _Resp(403)

    assert omio._request(_Session(), "GET", "/x") == {"ok": True}
    assert seen == list(omio._IMPERSONATE_FALLBACKS)


def test_request_does_not_rotate_on_non_403(monkeypatch):
    seen = []

    class _Session:
        _omio_impersonate = omio._IMPERSONATE_FALLBACKS[0]

        def request(self, method, url, **kwargs):
            seen.append(1)
            return _Resp(500)

    with pytest.raises(omio.OmioError, match="HTTP 500"):
        omio._request(_Session(), "GET", "/x")
    assert len(seen) == 1
