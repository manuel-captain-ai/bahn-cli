"""Tests for bahn_de.predictor and the _raw_abschnitte pipeline.

All predictor HTTP calls are mocked so no real network access is needed.
Baseline fixture: fahrplan_umstieg_response.json (Rendsburg→Hamburg→Berlin,
1 Umstieg RE7 → ICE 509) — confirmed working in Phase-B Spike.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bahn_de import formatters
from bahn_de.predictor import (
    _aggregate,
    build_transfer_data,
    score_connections,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture
def umstieg_raw():
    data = _load("fahrplan_umstieg_response.json")
    return data["verbindungen"][0]


@pytest.fixture
def umstieg_parsed(umstieg_raw):
    return formatters.parse_connection(umstieg_raw)


# ── parse_connection preserves _raw_abschnitte ────────────────────────────────

class TestRawAbschnitte:
    def test_field_present(self, umstieg_parsed):
        assert "_raw_abschnitte" in umstieg_parsed

    def test_field_is_list(self, umstieg_parsed):
        assert isinstance(umstieg_parsed["_raw_abschnitte"], list)

    def test_has_public_transport_entries(self, umstieg_parsed):
        pub = [
            s for s in umstieg_parsed["_raw_abschnitte"]
            if (s.get("verkehrsmittel") or {}).get("typ") == "PUBLICTRANSPORT"
        ]
        assert len(pub) >= 1

    def test_stripped_by_connection_model(self, umstieg_parsed):
        from bahn_de.models import Connection
        conn = Connection.model_validate(umstieg_parsed)
        assert not hasattr(conn, "_raw_abschnitte")


# ── build_transfer_data ────────────────────────────────────────────────────────

class TestBuildTransferData:
    def test_produces_required_columns(self, umstieg_parsed):
        raw_abschnitte = umstieg_parsed["_raw_abschnitte"]
        ref = datetime(2026, 7, 6, 0, 0)
        cols = build_transfer_data(raw_abschnitte, ref)
        required = {
            "number", "lat", "lon", "stop_sequence", "distance_traveled",
            "bearing", "delay_prognosed", "minute_of_day", "weekday",
            "is_regional", "is_arrival", "operator", "category", "line",
        }
        assert required.issubset(cols.keys())

    def test_rows_count_is_even(self, umstieg_parsed):
        raw_abschnitte = umstieg_parsed["_raw_abschnitte"]
        ref = datetime(2026, 7, 6, 0, 0)
        cols = build_transfer_data(raw_abschnitte, ref)
        n = len(cols["number"])
        assert n > 0
        assert n % 2 == 0  # departure + arrival per leg

    def test_has_arrival_alternates(self, umstieg_parsed):
        raw_abschnitte = umstieg_parsed["_raw_abschnitte"]
        ref = datetime(2026, 7, 6, 0, 0)
        cols = build_transfer_data(raw_abschnitte, ref)
        # Departure rows should be False, arrival rows True
        assert False in cols["is_arrival"]
        assert True in cols["is_arrival"]

    def test_empty_abschnitte_returns_empty(self):
        cols = build_transfer_data([], datetime(2026, 7, 6, 0, 0))
        assert cols["number"] == []


# ── _aggregate ────────────────────────────────────────────────────────────────

class TestAggregate:
    def test_single_score(self):
        vs, pk = _aggregate({"transfer_scores": [0.87]})
        assert abs(vs - 0.87) < 1e-9
        assert pk is None

    def test_multiple_scores_returns_min(self):
        vs, pk = _aggregate({"transfer_scores": [0.9, 0.6, 0.75]})
        assert abs(vs - 0.6) < 1e-9

    def test_empty_scores_returns_none(self):
        vs, pk = _aggregate({"transfer_scores": []})
        assert vs is None
        assert pk is None

    def test_all_none_scores(self):
        vs, pk = _aggregate({"transfer_scores": [None, None]})
        assert vs is None

    def test_missing_key(self):
        vs, pk = _aggregate({})
        assert vs is None


# ── score_connections ─────────────────────────────────────────────────────────

class TestScoreConnections:
    def test_skips_when_url_empty(self, umstieg_parsed, monkeypatch):
        monkeypatch.delenv("PREDICTOR_URL", raising=False)
        results = score_connections([umstieg_parsed], predictor_url="")
        assert results == [(None, None)]

    def test_returns_none_on_connection_error(self, umstieg_parsed, monkeypatch):
        monkeypatch.setenv("PREDICTOR_URL", "http://localhost:19999")
        # Port 19999 should be unreachable; timeout yields None score
        results = score_connections([umstieg_parsed], predictor_url="http://localhost:19999")
        assert results == [(None, None)]

    def test_returns_score_from_mocked_predictor(self, umstieg_parsed, monkeypatch):
        fake_response = json.dumps({"transfer_scores": [0.82]}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("bahn_de.predictor.urllib.request.urlopen", return_value=mock_resp):
            results = score_connections(
                [umstieg_parsed],
                predictor_url="http://mock-predictor:8000",
            )
        vs, pk = results[0]
        assert abs(vs - 0.82) < 1e-9
        assert pk is None

    def test_score_ends_up_in_connection_model(self, umstieg_parsed, monkeypatch):
        from bahn_de.models import Connection
        fake_response = json.dumps({"transfer_scores": [0.75]}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("bahn_de.predictor.urllib.request.urlopen", return_value=mock_resp):
            scores = score_connections(
                [umstieg_parsed],
                predictor_url="http://mock-predictor:8000",
            )
        vs, pk = scores[0]
        umstieg_parsed["verbindungsscore"] = vs
        conn = Connection.model_validate(umstieg_parsed)
        assert conn.verbindungsscore is not None
        assert abs(conn.verbindungsscore - 0.75) < 1e-9
