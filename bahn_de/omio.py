"""Omio als Preis-Provider für ausländische Bahnrelationen.

Warum: bahn.de liefert für rein ausländische Strecken Fahrplan, aber ``preis: null``,
und Flix deckt nur Bus/FlixTrain ab. Omio verkauft fremdes Bahninventar (ÖBB, RENFE,
PKP, HŽ) und schließt damit genau die Lücke, die in STATUS.md offen stand.

Der Spike vom 2026-07-29 scheiterte am falschen Endpunkt: ``/growth/search-trigger/search``
antwortet 503, auch im echten Browser. Der Weg, den das Frontend tatsächlich geht, ist
zweistufig:

1. ``POST /GoEuroAPI/rest/api/v5/searches`` mit ``{"searchOptions": {...}}`` -> 201 + searchId.
   Das Wrapping ist Pflicht; ein flaches Objekt gibt 400 ohne Fehlertext.
2. ``GET /bff-core-service/search-experience/results/v1?search_id=...`` -> Ergebnisse.
   Die Suche läuft asynchron, also pollen bis Preise da sind.

Kein Key, kein Login. Cloudflare blockt nackte Clients (403), ``curl_cffi`` mit
Browser-Fingerprint kommt durch - dieselbe Mechanik wie in ``backend.py``.

Vertrag wie ``flix.py``: ``available()``, ``search_journeys_omio()``, ``OmioError``.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from curl_cffi import requests as cffi_requests

from .backend import _IMPERSONATE_FALLBACKS, BahnApiError
from .models import Connection, Section, SearchResult

_BASE = "https://www.omio.com"
_TIMEOUT = 30

# Die Suche ist asynchron: der erste Poll liefert oft leere oder preislose Ergebnisse.
_POLL_TRIES = 15
_POLL_WAIT = 2.0

# Pflichtfelder von searchOptions, empirisch ermittelt (2026-07-29): departurePosition,
# arrivalPosition, departureDate, travelModes, passengers[].age, userInfo mit
# currency/domain/locale/identifier. Alles andere ist optional.
_MODES = ("Train", "Bus", "Ferry")

# ponytail: Prozess-lokaler Cache wie in flix.py. positionIds sind stabil,
# ein dict reicht für einen CLI-Lauf.
_position_cache: dict[str, dict] = {}


class OmioError(BahnApiError):
    """Fehler der Omio-API oder Ort nicht auflösbar."""


def available() -> bool:
    """Omio braucht keinen API-Key, ist also immer verfügbar."""
    return True


def _session(impersonate=None):
    s = cffi_requests.Session(impersonate=impersonate or _IMPERSONATE_FALLBACKS[0])
    s.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _BASE,
        "Referer": f"{_BASE}/",
    })
    # Merkt sich das aktive Target, damit _request nach einem 403 rotieren kann.
    s._omio_impersonate = impersonate or _IMPERSONATE_FALLBACKS[0]
    return s


def _request(session, method, path, **kwargs):
    """Führt die Anfrage aus und rotiert bei 403 durch die Fingerprint-Targets.

    Cloudflare blockt sowohl unbekannte TLS-Fingerprints als auch zu schnelle
    Folgen von derselben IP. Dieselbe Rotation wie in ``backend.py``, weil beide
    Seiten am gleichen Schutz hängen.
    """
    url = f"{_BASE}{path}"
    start = _IMPERSONATE_FALLBACKS.index(getattr(session, "_omio_impersonate",
                                                 _IMPERSONATE_FALLBACKS[0]))
    targets = _IMPERSONATE_FALLBACKS[start:] + _IMPERSONATE_FALLBACKS[:start]

    last = None
    for target in targets:
        if target != getattr(session, "_omio_impersonate", None):
            session.impersonate = target
            session._omio_impersonate = target
        try:
            resp = session.request(method, url, timeout=_TIMEOUT, **kwargs)
        except Exception as e:
            raise OmioError(f"Anfrage an Omio fehlgeschlagen: {e}")
        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except ValueError as e:
                raise OmioError(f"Omio hat ungueltiges JSON geliefert: {e}")
        last = resp
        if resp.status_code != 403:
            break

    raise OmioError(
        f"Omio API-Fehler (HTTP {last.status_code}): {last.text[:200]}",
        status_code=last.status_code,
    )


def resolve_position(session, query: str) -> dict:
    """Ortsname -> Omio-Position (positionId + type).

    Nimmt den ersten Treffer des Suggesters. Der sortiert nach Relevanz, und ein
    Stadt-Treffer ("location") schlägt die Einzelbahnhöfe, was für einen
    Preisvergleich die richtige Granularität ist.
    """
    key = query.strip().lower()
    if key in _position_cache:
        return _position_cache[key]

    data = _request(session, "GET", "/suggester-api/v5/position",
                    params={"term": query, "locale": "en", "hierarchical": "true"})
    if not isinstance(data, list) or not data:
        raise OmioError(f"Omio kennt keinen Ort '{query}'.")

    hit = data[0]
    position = {
        "id": hit["positionId"],
        "type": hit.get("type"),
        "name": hit.get("displayName") or hit.get("defaultName") or query,
    }
    _position_cache[key] = position
    return position


def _create_search(session, origin, destination, day, identifier) -> str:
    body = {"searchOptions": {
        "departurePosition": {"id": origin["id"], "type": origin["type"]},
        "arrivalPosition": {"id": destination["id"], "type": destination["type"]},
        "departureDate": day,
        "travelModes": list(_MODES),
        "passengers": [{"type": "adult", "age": 26}],
        "userInfo": {
            "currency": "EUR",
            "domain": "com",
            "locale": "en",
            # Pflichtfeld. Ohne identifier antwortet die API 400 ohne Fehlertext.
            "identifier": identifier,
        },
    }}
    data = _request(session, "POST", "/GoEuroAPI/rest/api/v5/searches", json=body)
    search_id = data.get("searchId")
    if not search_id:
        raise OmioError("Omio hat keine searchId geliefert.")
    return search_id


def _poll_results(session, search_id: str) -> dict:
    """Pollt bis Verbindungen mit Preisen da sind, sonst der letzte Stand."""
    params = {
        "direction": "outbound",
        "search_id": search_id,
        "sort_by": "updateTime",
        "include_segment_positions": "true",
        "sort_variants": "smart",
        "source_page": "srp",
        "use_stats": "true",
        "updated_since": "0",
    }
    data = {}
    for attempt in range(_POLL_TRIES):
        data = _request(session, "GET",
                        "/bff-core-service/search-experience/results/v1", params=params)
        outbounds = data.get("outbounds") or {}
        priced = [o for o in outbounds.values() if o.get("price")]
        # Ein paar Runden weiterpollen, auch wenn schon Preise da sind: Omio
        # schiebt Bahnangebote oft später nach als Bus.
        if priced and attempt >= 3:
            break
        if attempt + 1 < _POLL_TRIES:
            time.sleep(_POLL_WAIT)
    return data


def _iso(value):
    """Omio liefert '2026-08-05T07:15:00.000+02:00'; das Schema will lokale naive Zeit."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError):
        return value


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_outbound(outbound, segment_details, positions, companies) -> Connection:
    def place(position_id):
        node = positions.get(str(position_id)) or {}
        return node.get("name") or node.get("cityName")

    segments = [
        segment_details.get(str(sid))
        for sid in (outbound.get("segments") or [])
    ]
    segments = [s for s in segments if s]

    sections = [
        Section(
            abfahrt=_iso(seg.get("departureTime")),
            abfahrt_ort=place(seg.get("departurePosition")),
            ankunft=_iso(seg.get("arrivalTime")),
            ankunft_ort=place(seg.get("arrivalPosition")),
            dauer_minuten=_int_or_none(seg.get("duration")),
            # transportId ist die Zug-/Liniennummer ("RJ 73"), sonst der Modus.
            produkt=seg.get("transportId") or (seg.get("type") or "").upper() or None,
            typ="PUBLICTRANSPORT",
        )
        for seg in segments
    ]

    produkte = sorted({(seg.get("type") or "").upper() for seg in segments if seg.get("type")})
    if not produkte:
        produkte = [(outbound.get("mode") or "").upper()] if outbound.get("mode") else []

    price = outbound.get("price")
    company = (companies.get(str(outbound.get("companyId"))) or {}).get("name")

    return Connection(
        trip_id=outbound.get("outboundId"),
        abfahrt=_iso(outbound.get("departureTime")),
        abfahrt_ort=place(segments[0].get("departurePosition")) if segments else None,
        ankunft=_iso(outbound.get("arrivalTime")),
        ankunft_ort=place(segments[-1].get("arrivalPosition")) if segments else None,
        dauer_minuten=_int_or_none(outbound.get("duration")),
        umstiege=_int_or_none(outbound.get("stops")) or 0,
        produkte=produkte or ([company] if company else []),
        abschnitte=sections,
        # Omio rechnet in Cent. /100 ist der Betrag, der im Checkout steht.
        preis=price / 100 if price else None,
        waehrung="EUR",
    )


def search_journeys_omio(from_station, to_station, *, when_iso,
                         arrival=False, limit=5) -> SearchResult:
    """Sucht Omio-Verbindungen. Gibt Connections mit echten Preisen zurück."""
    day = when_iso[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise OmioError(f"Ungueltiges Datum '{when_iso}' (erwartet YYYY-MM-DD).")

    session = _session()
    origin = resolve_position(session, from_station)
    destination = resolve_position(session, to_station)

    search_id = _create_search(session, origin, destination, day, str(uuid.uuid4()))
    data = _poll_results(session, search_id)

    segment_details = data.get("segmentDetails") or {}
    positions = data.get("positions") or {}
    companies = data.get("companies") or {}

    connections = [
        _map_outbound(o, segment_details, positions, companies)
        for o in (data.get("outbounds") or {}).values()
        if o.get("status") == "available"
    ]
    # Preislose Treffer sind für einen Preisvergleich wertlos; Omio liefert sie
    # nur, solange die Suche noch nachlädt.
    connections = [c for c in connections if c.preis is not None]

    notices = []
    if not connections:
        notices.append(
            f"Omio hat fuer {origin['name']} -> {destination['name']} am {day} "
            "keine buchbare Verbindung."
        )

    connections.sort(key=lambda c: c.abfahrt or "")
    if arrival:
        connections = [c for c in connections if (c.ankunft or "") <= when_iso] or connections

    return SearchResult(connections=connections[:limit], notices=notices)
