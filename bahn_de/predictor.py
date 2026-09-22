"""Reliability enrichment via bahnvorhersage.de Predictor.

Builds the TransferData feature columns from raw HAFAS verbindungsAbschnitte
(coordinates from HAFAS id string, routeIdx, adminID) and POSTs them to the
self-hosted bahnvorhersage-predictor container (FastAPI, POST /rate-journeys/).

Set PREDICTOR_URL to enable enrichment (e.g. http://predictor:8000).
Unset or empty → enrichment is skipped entirely (returns all-None scores).
All network errors and timeouts silently return None scores — never raises.

Feature pipeline ported from apps/bahn-de/spike/predict_one.py.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import urllib.request
from datetime import datetime
from typing import Optional

_log = logging.getLogger(__name__)

_COORD = re.compile(r"@X=(-?\d+)@Y=(-?\d+)")
_REGIONAL = {"REGIONAL", "SBAHN"}
_TIMEOUT = 5  # seconds per predictor call
_MIN_XFER_DEFAULT = 5  # assumed minimum transfer time in minutes


def _coords(halt: dict) -> tuple[float, float]:
    """lat, lon from HAFAS id string (@X=lon*1e6@Y=lat*1e6)."""
    m = _COORD.search(halt.get("id", ""))
    if not m:
        return (0.0, 0.0)
    lon, lat = int(m.group(1)) / 1e6, int(m.group(2)) / 1e6
    return (lat, lon)


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dphi, dlmb = la2 - la1, lo2 - lo1
    h = math.sin(dphi / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> int:
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return int((math.degrees(math.atan2(y, x)) + 360) % 360)


def _train_number(vm: dict) -> int:
    digits = re.sub(r"\D", "", vm.get("nummer") or vm.get("name") or "")
    return int(digits) if digits else 0


def _category(vm: dict) -> str:
    txt = (vm.get("kurzText") or vm.get("name") or "").strip()
    m = re.match(r"([A-Za-zÄÖÜ]+)", txt)
    return m.group(1).upper() if m else (vm.get("produktGattung") or "")


def _line(vm: dict) -> str:
    if vm.get("linienNummer"):
        return str(vm["linienNummer"])
    mt = (vm.get("mittelText") or vm.get("name") or "").strip()
    m = re.search(r"(\d+\w*)$", mt)
    return m.group(1) if m else mt


def _sollzeit(node: dict, key: str) -> Optional[datetime]:
    z = (node.get(key) or {}).get("sollzeit")
    return datetime.fromisoformat(z) if z else None


def build_transfer_data(raw_abschnitte: list, ref_now: datetime) -> dict:
    """Build TransferData column dict from raw verbindungsAbschnitte.

    One row per PUBLICTRANSPORT leg endpoint (departure + arrival).
    Direct connections (1 leg, 0 transfers) produce 2 rows but no transfer scores.
    """
    legs = [
        a for a in raw_abschnitte
        if (a.get("verkehrsmittel") or {}).get("typ") == "PUBLICTRANSPORT"
    ]
    cols: dict[str, list] = {k: [] for k in (
        "number", "lat", "lon", "stop_sequence", "distance_traveled",
        "dwell_time_schedule", "dwell_time_prognosed", "bearing", "delay_prognosed",
        "minute_of_day", "minutes_to_prognosed_time", "weekday", "is_regional",
        "is_arrival", "operator", "category", "line",
        "prognosed_transfer_time", "minimal_transfer_time",
    )}
    cum_dist = 0.0
    prev_coord: Optional[tuple[float, float]] = None

    def _add(halt: dict, vm: dict, is_arrival: bool,
             t: Optional[datetime], prog_xfer, min_xfer, brg: int) -> None:
        nonlocal cum_dist, prev_coord
        lat, lon = _coords(halt)
        if prev_coord is not None:
            cum_dist += _haversine_m(prev_coord, (lat, lon))
        prev_coord = (lat, lon)
        cols["number"].append(_train_number(vm))
        cols["lat"].append(lat)
        cols["lon"].append(lon)
        cols["stop_sequence"].append(int(halt.get("routeIdx", 0)))
        cols["distance_traveled"].append(int(cum_dist))
        cols["dwell_time_schedule"].append(None)
        cols["dwell_time_prognosed"].append(None)
        cols["delay_prognosed"].append(0)
        cols["minute_of_day"].append(t.hour * 60 + t.minute if t else 0)
        cols["minutes_to_prognosed_time"].append(
            int((t - ref_now).total_seconds() // 60) if t else 0
        )
        cols["weekday"].append(t.isoweekday() if t else 1)
        cols["is_regional"].append((vm.get("produktGattung") or "") in _REGIONAL)
        cols["is_arrival"].append(is_arrival)
        cols["operator"].append(str(halt.get("adminID") or ""))
        cols["category"].append(_category(vm))
        cols["line"].append(_line(vm))
        cols["prognosed_transfer_time"].append(prog_xfer)
        cols["minimal_transfer_time"].append(min_xfer)
        cols["bearing"].append(brg)

    for i, leg in enumerate(legs):
        vm = leg.get("verkehrsmittel") or {}
        halte = leg.get("halte") or []
        h_start = halte[0] if halte else leg.get("startHalt") or {}
        h_end = halte[-1] if halte else leg.get("zielHalt") or {}
        brg = _bearing_deg(_coords(h_start), _coords(h_end))
        dep_t = _sollzeit(leg, "abfahrt") or _sollzeit(leg.get("startHalt") or {}, "abfahrt")
        arr_t = _sollzeit(leg, "ankunft") or _sollzeit(leg.get("zielHalt") or {}, "ankunft")

        prog_xfer_arr = min_xfer_arr = None
        if i < len(legs) - 1:
            nxt = legs[i + 1]
            nxt_dep = (_sollzeit(nxt, "abfahrt")
                       or _sollzeit(nxt.get("startHalt") or {}, "abfahrt"))
            if arr_t and nxt_dep:
                gap = int((nxt_dep - arr_t).total_seconds() // 60)
                prog_xfer_arr = gap
                min_xfer_arr = _MIN_XFER_DEFAULT

        prog_xfer_dep = min_xfer_dep = None
        if i > 0:
            prv = legs[i - 1]
            prv_arr = (_sollzeit(prv, "ankunft")
                       or _sollzeit(prv.get("zielHalt") or {}, "ankunft"))
            if prv_arr and dep_t:
                prog_xfer_dep = int((dep_t - prv_arr).total_seconds() // 60)
                min_xfer_dep = _MIN_XFER_DEFAULT

        _add(h_start, vm, False, dep_t, prog_xfer_dep, min_xfer_dep, brg)
        _add(h_end, vm, True, arr_t, prog_xfer_arr, min_xfer_arr, brg)

    return cols


def _post(cols: dict, url: str) -> dict:
    data = json.dumps(cols).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read())


def _aggregate(response: dict) -> tuple[Optional[float], Optional[float]]:
    """Derive (verbindungsscore, puenktlichkeit) from a predictor response.

    verbindungsscore = min transfer probability (worst leg), or None for direct.
    puenktlichkeit = reserved (predictor does not expose a standalone score yet).
    """
    transfer_scores = response.get("transfer_scores") or []
    real = [s for s in transfer_scores if s is not None]
    return (min(real), None) if real else (None, None)


def score_connections(
    parsed_dicts: list[dict],
    *,
    predictor_url: Optional[str] = None,
) -> list[tuple[Optional[float], Optional[float]]]:
    """Score a list of parsed connection dicts via the predictor.

    Returns one (verbindungsscore, puenktlichkeit) tuple per connection.
    All-None when PREDICTOR_URL is unset or any error occurs per connection.
    """
    url = (predictor_url or os.environ.get("PREDICTOR_URL", "")).rstrip("/")
    if not url:
        return [(None, None)] * len(parsed_dicts)

    rate_url = url + "/rate-journeys/"
    results: list[tuple[Optional[float], Optional[float]]] = []

    for parsed in parsed_dicts:
        raw_abschnitte = parsed.get("_raw_abschnitte") or []
        if not raw_abschnitte:
            results.append((None, None))
            continue
        try:
            dep_iso = parsed.get("abfahrt")
            ref_now = (
                datetime.fromisoformat(dep_iso).replace(hour=0, minute=0, second=0, microsecond=0)
                if dep_iso else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            )
            cols = build_transfer_data(raw_abschnitte, ref_now)
            if not cols.get("number"):
                results.append((None, None))
                continue
            resp = _post(cols, rate_url)
            results.append(_aggregate(resp))
        except Exception as exc:
            _log.debug("predictor call failed for connection: %s", exc)
            results.append((None, None))

    return results
