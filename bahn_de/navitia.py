"""navitia.io backend — pan-European schedule routing for journeys bahn.de can't retail.

This is the ONLY place that talks to api.navitia.io. It fills the gap the bahn.de
scraper leaves: foreign-domestic legs (Paris→Lyon), routes that never touch the DB
retail network. navitia returns **schedules, not prices** — every mapped Connection
has ``preis=None`` and is labelled via ``notices``.

Free API key required (``NAVITIA_API_KEY``). Without it, :func:`available` is False and
callers skip navitia entirely (mirrors the optional predictor backend). Uses stdlib
``urllib`` — navitia is not behind bot detection, so no curl_cffi impersonation needed.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request

from .backend import BahnApiError
from .models import Connection, Section, SearchResult

BASE = os.environ.get("NAVITIA_URL", "https://api.navitia.io/v1")


class NavitiaError(BahnApiError):
    """navitia failure. Subclasses BahnApiError so existing CLI/web handlers catch it."""


def _key() -> str | None:
    return os.environ.get("NAVITIA_API_KEY") or None


def available() -> bool:
    """True if a key is configured, i.e. navitia can be called at all."""
    return _key() is not None


# ── HTTP ────────────────────────────────────────────────────────────────────────

def _get(path: str, params: dict) -> dict:
    key = _key()
    if not key:
        raise NavitiaError("NAVITIA_API_KEY not set")
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url)
    # navitia: HTTP Basic, username = key, empty password.
    auth = base64.b64encode(f"{key}:".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise NavitiaError(f"navitia HTTP {exc.code}: {body[:200]}")
    except urllib.error.URLError as exc:
        raise NavitiaError(f"navitia unreachable: {exc.reason}")


# ── datetime helpers ─────────────────────────────────────────────────────────---

def _to_navitia_dt(when_iso: str) -> str:
    """'2026-08-01T09:00:00' -> '20260801T090000' (navitia basic-ISO)."""
    s = when_iso.replace("-", "").replace(":", "")
    if "T" in s and len(s.split("T")[1]) == 4:  # HH MM only
        s += "00"
    return s


def _from_navitia_dt(dt: str | None) -> str | None:
    """'20260801T093000' -> '2026-08-01T09:30:00'."""
    if not dt or "T" not in dt:
        return dt
    date, _, tm = dt.partition("T")
    if len(date) != 8 or len(tm) < 6:
        return dt
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}T{tm[:2]}:{tm[2:4]}:{tm[4:6]}"


# ── resolution + search ─────────────────────────────────────────────────────---

def resolve_place(query: str) -> str:
    """Resolve a free-text place to a navitia id (stop_area/administrative/coord)."""
    data = _get("/places", {"q": query, "count": 1})
    places = data.get("places") or []
    if not places:
        raise NavitiaError(f"navitia: no place for {query!r}")
    return places[0]["id"]


def _map_journey(j: dict) -> Connection:
    pt_sections = [s for s in j.get("sections", []) if s.get("type") == "public_transport"]
    abschnitte: list[Section] = []
    produkte: list[str] = []
    for s in pt_sections:
        di = s.get("display_informations") or {}
        mode = di.get("commercial_mode") or di.get("physical_mode")
        if mode:
            produkte.append(mode)
        abschnitte.append(Section(
            abfahrt=_from_navitia_dt(s.get("departure_date_time")),
            abfahrt_ort=(s.get("from") or {}).get("name"),
            ankunft=_from_navitia_dt(s.get("arrival_date_time")),
            ankunft_ort=(s.get("to") or {}).get("name"),
            dauer_minuten=(s.get("duration") or 0) // 60 or None,
            produkt=mode,
            typ=di.get("label") or di.get("headsign"),
        ))
    first, last = (pt_sections[0] if pt_sections else {}), (pt_sections[-1] if pt_sections else {})
    return Connection(
        abfahrt=_from_navitia_dt(j.get("departure_date_time")),
        abfahrt_ort=(first.get("from") or {}).get("name"),
        ankunft=_from_navitia_dt(j.get("arrival_date_time")),
        ankunft_ort=(last.get("to") or {}).get("name"),
        dauer_minuten=(j.get("duration") or 0) // 60 or None,
        umstiege=j.get("nb_transfers"),
        produkte=produkte,
        abschnitte=abschnitte,
        preis=None,
        waehrung=None,
    )


def search_journeys_navitia(
    from_station, to_station, *, when_iso, arrival=False, limit=5,
) -> SearchResult:
    """Search journeys via navitia. Returns a SearchResult with price-less Connections."""
    frm = resolve_place(from_station)
    to = resolve_place(to_station)
    params = {
        "from": frm, "to": to,
        "datetime": _to_navitia_dt(when_iso),
        "datetime_represents": "arrival" if arrival else "departure",
        "count": limit,
    }
    data = _get("/journeys", params)
    journeys = [j for j in (data.get("journeys") or []) if j.get("sections")]
    conns = [_map_journey(j) for j in journeys][:limit]
    notices = ["Ergebnisse via navitia.io (Fahrplan, keine Preise). Quelle: navitia.io / OpenData."]
    if not conns:
        notices.append("navitia fand keine Verbindung.")
    return SearchResult(connections=conns, notices=notices)
