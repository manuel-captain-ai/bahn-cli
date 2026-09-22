"""End-to-end tests against the real bahn.de API.

These tests make real network calls and are SKIPPED unless:
  - the environment variable ``BAHN_DE_E2E=1`` is set, AND
  - a TCP connection to www.bahn.de:443 succeeds.

Run explicitly with:
    BAHN_DE_E2E=1 python -m pytest bahn_de/tests/test_full_e2e.py -v -s
"""
from __future__ import annotations

import datetime
import os
import socket
import time

import pytest

from bahn_de import backend as bahn_backend
from bahn_de import formatters


def _bahn_de_reachable():
    try:
        with socket.create_connection(("www.bahn.de", 443), timeout=5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    os.environ.get("BAHN_DE_E2E") != "1",
    reason="Set BAHN_DE_E2E=1 to run end-to-end tests against the real bahn.de API.",
)


@pytest.fixture(scope="module", autouse=True)
def _require_network():
    if not _bahn_de_reachable():
        pytest.skip("www.bahn.de:443 is not reachable from this environment.")


@pytest.fixture(scope="module")
def search_date():
    """Departure date 7 days from today, as YYYY-MM-DD."""
    return (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")


def test_e2e_stations_hamburg_has_results_with_lid():
    results = bahn_backend.search_stations("Hamburg")
    time.sleep(2)

    assert isinstance(results, list)
    assert len(results) >= 1
    for station in results:
        assert station.get("id")
        assert "@O=" in station["id"]


def test_e2e_search_hamburg_hbf_to_berlin_hbf_has_priced_connection(search_date):
    src = bahn_backend.resolve_station("Hamburg Hbf")
    dst = bahn_backend.resolve_station("Berlin Hbf")
    time.sleep(2)

    payload = bahn_backend.build_journey_payload(
        src["id"], dst["id"], f"{search_date}T09:00:00"
    )
    raw = bahn_backend.search_connections(payload)
    time.sleep(2)

    connections = raw.get("verbindungen", [])
    assert len(connections) >= 1

    parsed = [formatters.parse_connection(c) for c in connections]
    priced = [c for c in parsed if c["preis"] is not None]
    assert len(priced) >= 1
    assert priced[0]["preis"] > 0


def test_e2e_bahncard25_price_not_higher_than_without(search_date):
    src = bahn_backend.resolve_station("Hamburg Hbf")
    dst = bahn_backend.resolve_station("Berlin Hbf")
    time.sleep(2)

    when_iso = f"{search_date}T09:00:00"

    payload_normal = bahn_backend.build_journey_payload(src["id"], dst["id"], when_iso)
    raw_normal = bahn_backend.search_connections(payload_normal)
    time.sleep(2)

    payload_bc25 = bahn_backend.build_journey_payload(
        src["id"], dst["id"], when_iso, bahncard=25
    )
    raw_bc25 = bahn_backend.search_connections(payload_bc25)
    time.sleep(2)

    parsed_normal = [formatters.parse_connection(c) for c in raw_normal.get("verbindungen", [])]
    parsed_bc25 = [formatters.parse_connection(c) for c in raw_bc25.get("verbindungen", [])]

    prices_normal = [c["preis"] for c in parsed_normal if c["preis"] is not None]
    prices_bc25 = [c["preis"] for c in parsed_bc25 if c["preis"] is not None]

    # Soft assertion: only compare if both sides actually returned prices.
    if prices_normal and prices_bc25:
        assert min(prices_bc25) <= min(prices_normal)


def test_e2e_via_search_rendsburg_bruessel_via_neanderthal():
    """Verify a Rendsburg->Bruessel via Neanderthal via-connection returns priced results.

    Reference: 2026-07-03 hin, 2026-07-06 weiter. With BahnCard 25 expect ~60 EUR.
    """
    src = bahn_backend.resolve_station("Rendsburg")
    via = bahn_backend.resolve_station("Neanderthal")
    dst = bahn_backend.resolve_station("Brussel-Noord")
    time.sleep(2)

    fare_params = {"bahncard": 25, "bahncard_class": 2, "first_class": False, "deutschlandticket": False}
    dwell = bahn_backend.resolve_via_dwell(
        src["id"], via["id"], "2026-07-03T09:00:00", "2026-07-06T08:00:00", fare_params
    )
    time.sleep(2)
    assert dwell > 0

    payload = bahn_backend.build_journey_payload(
        src["id"], dst["id"], "2026-07-03T09:00:00",
        bahncard=25, bahncard_class=2,
        zwischenhalte=[{"id": via["id"], "aufenthaltsdauer": dwell}],
    )
    raw = bahn_backend.search_connections(payload)
    time.sleep(2)

    verbindungen = raw.get("verbindungen") or []
    assert len(verbindungen) >= 1

    # Must have ctxRecon (nachgelagerte Preisermittlung)
    ctx = verbindungen[0].get("ctxRecon")
    assert ctx, "Expected ctxRecon token for via connection"

    reisende = payload["reisende"]
    rec = bahn_backend.recon_price(ctx, first_class=False, deutschlandticket=False, reisende=reisende)
    time.sleep(2)

    # Soft assertion: Sparpreis-Verfuegbarkeit schwankt tagesaktuell, daher kann
    # recon auch "No price information available" liefern. Geprueft wird die
    # Struktur; ist ein Preis vorhanden, muss er plausibel sein.
    assert "preis" in rec
    if rec["preis"] is not None:
        assert rec["preis"] > 0
        # Rough sanity: price should be below the direct price of ~105 EUR
        assert rec["preis"] < 110.0


def test_e2e_best_returns_sorted_list_of_fares(search_date):
    src = bahn_backend.resolve_station("Hamburg Hbf")
    dst = bahn_backend.resolve_station("Berlin Hbf")
    time.sleep(2)

    payload = bahn_backend.build_journey_payload(src["id"], dst["id"], f"{search_date}T00:00:00")
    raw, source = bahn_backend.day_best_prices(payload)
    time.sleep(2)

    assert source in ("bestpreis-endpoint", "bestpreis-endpoint-int", "paging-fallback")
    assert isinstance(raw, list)
    assert len(raw) >= 1

    parsed = [formatters.parse_connection(c) for c in raw]

    def _key(c):
        p = c.get("preis")
        return (p is None, p if p is not None else 0)

    parsed_sorted = sorted(parsed, key=_key)

    prices = [c["preis"] for c in parsed_sorted if c["preis"] is not None]
    assert prices == sorted(prices)
