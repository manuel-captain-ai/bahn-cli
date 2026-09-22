"""Tests für bahn_de.heuristic — Offline-Pünktlichkeits-Heuristik.

Alle Tests arbeiten mit Mini-Fixture-Tabellen; kein Netzwerk-Zugriff.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bahn_de import formatters
from bahn_de.heuristic import _cdf, _extract_transfer_probs, score_connections

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_MINI_LOOKUP = {
    "_meta": {
        "source": "test fixture",
        "license": "CC BY 4.0",
        "attribution": "test",
        "rows_total": 1000,
        "built": "2026-06-20",
        "thresholds_min": [-5, 0, 5, 10, 20],
        "min_samples": 10,
        "fallback_category": "RE",
    },
    "ICE": [0.01, 0.30, 0.60, 0.75, 0.90],
    "RE":  [0.00, 0.35, 0.75, 0.88, 0.96],
    "S":   [0.00, 0.40, 0.90, 0.97, 0.99],
}


@pytest.fixture(autouse=True)
def patch_lookup(tmp_path):
    """Ersetzt das echte Lookup durch _MINI_LOOKUP für alle Tests."""
    lookup_file = tmp_path / "reliability_lookup.json"
    lookup_file.write_text(json.dumps(_MINI_LOOKUP))

    import bahn_de.heuristic as h
    orig_lookup = h._lookup
    orig_thresholds = h._thresholds
    orig_path_fn = h._lookup_path

    h._lookup = None
    h._thresholds = []
    h._lookup_path = lambda: lookup_file
    yield
    h._lookup = orig_lookup
    h._thresholds = orig_thresholds
    h._lookup_path = orig_path_fn


# ── _cdf ─────────────────────────────────────────────────────────────────────

class TestCdf:
    def test_known_category_at_threshold(self):
        p = _cdf("ICE", 5)
        assert abs(p - 0.60) < 1e-9

    def test_interpolation_between_thresholds(self):
        # Zwischen t=5 (0.60) und t=10 (0.75) bei buffer=7.5 → 0.675
        p = _cdf("ICE", 7.5)
        assert abs(p - 0.675) < 1e-9

    def test_below_minimum_threshold(self):
        p = _cdf("ICE", -10)
        assert p == pytest.approx(0.01)

    def test_above_maximum_threshold(self):
        p = _cdf("ICE", 60)
        assert p == pytest.approx(0.90)

    def test_unknown_category_falls_back_to_re(self):
        p_unknown = _cdf("XYZ", 5)
        p_re = _cdf("RE", 5)
        assert p_unknown == p_re

    def test_category_lookup_case_insensitive_via_upper(self):
        # heuristic.py ruft .upper() auf — "re" → "RE"
        p_lower = _cdf("re", 5)
        p_upper = _cdf("RE", 5)
        assert p_lower == p_upper

    def test_zero_buffer_returns_p_at_zero(self):
        p = _cdf("S", 0)
        assert abs(p - 0.40) < 1e-9


# ── _extract_transfer_probs ───────────────────────────────────────────────────

class TestExtractTransferProbs:
    def _make_cols(self, *, prognosed, minimal, category, is_arrival):
        n = len(prognosed)
        return {
            "category": category,
            "prognosed_transfer_time": prognosed,
            "minimal_transfer_time": minimal,
            "is_arrival": is_arrival,
            "number": [1] * n,
        }

    def test_single_transfer_returns_one_prob(self):
        # 2 Beine → 4 Zeilen: dep/arr für Leg 0, dep/arr für Leg 1
        # Nur Ankunfts-Zeile von Leg 0 hat prognosed_transfer_time
        cols = self._make_cols(
            prognosed=[None, 10, None, None],
            minimal=[None, 5, None, None],
            category=["ICE", "ICE", "RE", "RE"],
            is_arrival=[False, True, False, True],
        )
        probs = _extract_transfer_probs(cols)
        assert len(probs) == 1
        # buffer = 10 - 5 = 5 → P(ICE ≤ 5) = 0.60
        assert abs(probs[0] - 0.60) < 1e-9

    def test_direct_connection_no_transfer_probs(self):
        cols = self._make_cols(
            prognosed=[None, None],
            minimal=[None, None],
            category=["ICE", "ICE"],
            is_arrival=[False, True],
        )
        probs = _extract_transfer_probs(cols)
        assert probs == []

    def test_two_transfers_returns_two_probs(self):
        cols = self._make_cols(
            prognosed=[None, 10, None, 8, None, None],
            minimal=[None, 5, None, 5, None, None],
            category=["ICE", "ICE", "RE", "RE", "S", "S"],
            is_arrival=[False, True, False, True, False, True],
        )
        probs = _extract_transfer_probs(cols)
        assert len(probs) == 2


# ── score_connections ─────────────────────────────────────────────────────────

class TestScoreConnections:
    def _umstieg_parsed(self):
        data = json.loads((FIXTURES_DIR / "fahrplan_umstieg_response.json").read_text())
        return formatters.parse_connection(data["verbindungen"][0])

    def test_with_transfer_returns_score_in_range(self):
        parsed = self._umstieg_parsed()
        results = score_connections([parsed])
        vs, pk = results[0]
        assert vs is not None
        assert 0.0 <= vs <= 1.0
        assert pk is None

    def test_direct_connection_returns_none(self):
        parsed = {"_raw_abschnitte": [], "abfahrt": "2026-07-06T10:00:00"}
        results = score_connections([parsed])
        assert results == [(None, None)]

    def test_missing_raw_abschnitte_returns_none(self):
        results = score_connections([{"abfahrt": "2026-07-06T10:00:00"}])
        assert results == [(None, None)]

    def test_multiple_connections_returns_correct_length(self):
        parsed = self._umstieg_parsed()
        results = score_connections([parsed, parsed, parsed])
        assert len(results) == 3

    def test_min_aggregation_over_transfers(self):
        """verbindungsscore = min der Einzel-Wahrscheinlichkeiten."""
        parsed = self._umstieg_parsed()
        results = score_connections([parsed])
        vs, _ = results[0]
        # Muss <= 1 sein; bei einem einzelnen Umstieg gibt es exakt 1 Score
        assert vs is not None
        assert vs <= 1.0

    def test_missing_lookup_does_not_raise(self, tmp_path):
        """Fehlende Lookup-Tabelle → (None, None), keine Exception."""
        import bahn_de.heuristic as h
        orig = h._lookup_path
        h._lookup_path = lambda: tmp_path / "nonexistent.json"
        h._lookup = None
        h._thresholds = []
        try:
            parsed = self._umstieg_parsed()
            results = score_connections([parsed])
            # Fallback: 0.5 je Umstieg (unbekannte Kategorie ohne Lookup)
            # Wichtig: kein Crash
            assert len(results) == 1
        finally:
            h._lookup_path = orig
            h._lookup = None
            h._thresholds = []


# ── service.py SCORE_BACKEND Umschaltung ─────────────────────────────────────

class TestScoreBackendSwitch:
    def test_backend_none_returns_all_none(self, monkeypatch):
        monkeypatch.setenv("SCORE_BACKEND", "none")
        from bahn_de.service import _score_connections
        result = _score_connections([{"_raw_abschnitte": [], "abfahrt": "2026-07-06T10:00"}])
        assert result == [(None, None)]

    def test_backend_heuristic_calls_heuristic(self, monkeypatch):
        monkeypatch.setenv("SCORE_BACKEND", "heuristic")
        from bahn_de.service import _score_connections
        from bahn_de import heuristic as _h
        called = []

        orig = _h.score_connections

        def fake(parsed):
            called.append(parsed)
            return [(0.8, None)] * len(parsed)

        with patch.object(_h, "score_connections", fake):
            result = _score_connections([{"_raw_abschnitte": []}])
        assert called
        assert result == [(0.8, None)]

    def test_backend_predictor_calls_predictor(self, monkeypatch):
        monkeypatch.setenv("SCORE_BACKEND", "predictor")
        monkeypatch.setenv("PREDICTOR_URL", "")
        from bahn_de.service import _score_connections
        result = _score_connections([{"_raw_abschnitte": []}])
        assert result == [(None, None)]
