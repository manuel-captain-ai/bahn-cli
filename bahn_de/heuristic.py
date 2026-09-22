"""Offline-Pünktlichkeits-Heuristik auf Basis vorab aggregierter Parquet-Daten.

Berechnet denselben (verbindungsscore, puenktlichkeit)-Rückgabetyp wie
predictor.score_connections, aber ohne Container und ohne Netzwerk-Aufruf.

Datenquelle: bahn_de/data/reliability_lookup.json
  P(Ankunftsverspätung ≤ t) je Zugkategorie (train_type) bei Schwellwerten
  [-5, 0, 2, 5, 10, 15, 20, 30, 60] Minuten.
  Lizenz: CC BY 4.0 (piebro/deutsche-bahn-data, HuggingFace)

Score-Logik pro Umstieg:
  buffer = prognosed_transfer_time − minimal_transfer_time  (Minuten)
  P(Anschluss) = P(Zubringer-Verspätung bei Ankunft ≤ buffer)
               = CDF(category_des_zubringers, buffer)  ← linear interpoliert
  verbindungsscore = min(P) über alle Umstiege; None bei Direktverbindung.
"""
from __future__ import annotations

import bisect
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .predictor import build_transfer_data

_log = logging.getLogger(__name__)

_LOOKUP_PATH = Path(__file__).parent / "data" / "reliability_lookup.json"
_MIN_XFER_DEFAULT = 5

_lookup: Optional[dict] = None
_thresholds: list[float] = []


def _load_lookup() -> dict:
    global _lookup, _thresholds
    if _lookup is not None:
        return _lookup
    try:
        raw = json.loads(_lookup_path().read_text())
        _thresholds = raw["_meta"]["thresholds_min"]
        _lookup = raw
        _log.debug("reliability_lookup geladen: %d Kategorien", len(_lookup) - 1)
    except Exception as exc:
        _log.warning("reliability_lookup konnte nicht geladen werden: %s", exc)
        _lookup = {}
        _thresholds = []
    return _lookup


def _lookup_path() -> Path:
    return _LOOKUP_PATH


def _cdf(category: str, buffer_min: float) -> float:
    """P(Ankunftsverspätung ≤ buffer_min) für eine Zugkategorie."""
    table = _load_lookup()
    fallback = table.get("_meta", {}).get("fallback_category", "RE")

    cdf_vals = table.get(category) or table.get(fallback)
    if not cdf_vals or not _thresholds:
        return 0.5  # unbekannt → mittlere Annahme

    # Lineare Interpolation zwischen den Stützstellen
    ts = _thresholds
    if buffer_min <= ts[0]:
        return float(cdf_vals[0])
    if buffer_min >= ts[-1]:
        return float(cdf_vals[-1])

    idx = bisect.bisect_right(ts, buffer_min) - 1
    t0, t1 = ts[idx], ts[idx + 1]
    p0, p1 = float(cdf_vals[idx]), float(cdf_vals[idx + 1])
    frac = (buffer_min - t0) / (t1 - t0)
    return p0 + frac * (p1 - p0)


def score_connections(
    parsed_dicts: list[dict],
) -> list[tuple[Optional[float], Optional[float]]]:
    """Berechnet (verbindungsscore, puenktlichkeit) für jede Verbindung.

    Identische Rückgabe-Signatur wie predictor.score_connections.
    Nutzt build_transfer_data aus predictor, um Umstiegs-Merkmale zu extrahieren.
    Gibt (None, None) bei Direktverbindungen, leeren Abschnitten und Fehlern.
    """
    _load_lookup()
    results: list[tuple[Optional[float], Optional[float]]] = []

    for parsed in parsed_dicts:
        raw_abschnitte = parsed.get("_raw_abschnitte") or []
        if not raw_abschnitte:
            results.append((None, None))
            continue
        try:
            dep_iso = parsed.get("abfahrt")
            ref_now = (
                datetime.fromisoformat(dep_iso).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                if dep_iso
                else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            )
            cols = build_transfer_data(raw_abschnitte, ref_now)
            if not cols.get("number"):
                results.append((None, None))
                continue

            transfer_probs = _extract_transfer_probs(cols)
            if not transfer_probs:
                # Direktverbindung
                results.append((None, None))
            else:
                vs = min(transfer_probs)
                results.append((round(vs, 4), None))
        except Exception as exc:
            _log.debug("heuristic scoring fehlgeschlagen: %s", exc)
            results.append((None, None))

    return results


def _extract_transfer_probs(cols: dict) -> list[float]:
    """Berechnet P(Anschluss) für jeden Umstieg aus den TransferData-Spalten."""
    categories = cols.get("category", [])
    prog_xfer = cols.get("prognosed_transfer_time", [])
    min_xfer = cols.get("minimal_transfer_time", [])
    is_arrival = cols.get("is_arrival", [])

    probs = []
    n = len(categories)
    for i in range(n):
        # Nur Ankunfts-Zeilen mit definierter prognosed_transfer_time
        if not is_arrival[i]:
            continue
        pt = prog_xfer[i]
        mt = min_xfer[i]
        if pt is None or mt is None:
            continue

        buffer = float(pt) - float(mt)
        cat = str(categories[i]).upper()
        p = _cdf(cat, buffer)
        probs.append(p)

    return probs
