"""Backend for the unofficial DB APIs (DB Navigator app API + bahn.de web API).

This module is the ONLY place that performs HTTP. It must not import click.

Search and station lookup use the DB Navigator app API (see "DB Navigator app API"
below), because since 2026-09 the bahn.de web API needs an Akamai `_abck` cookie
that only browser JavaScript can mint. The remaining web calls (vbid share link,
recon) still go through ``_request`` with ``curl_cffi`` impersonation and fail
soft: callers fall back to the search link or skip the price enrichment.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_BERLIN = ZoneInfo("Europe/Berlin")

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover
    raise RuntimeError(
        "curl_cffi not found. Install with: pip install 'curl_cffi>=0.6'"
    )

# --- Constants ---------------------------------------------------------------

WWW_HOST = "https://www.bahn.de"
INT_HOST = "https://int.bahn.de"
# Akamai ages impersonation targets out every few months. Known dead: the bare "chrome"
# alias, chrome110/120/131/136 and the newest builds (142/145/146). Verified passing
# 2026-07-29: chrome124, safari180, firefox135.
# To test a candidate: POST /web/api/angebote/fahrplan and read the status.
#   422 = fingerprint PASSED (payload was wrong)   403 = blocked
# ponytail: a 3-target fallback list beats a residential-proxy bill. When all three
# start 403ing, replace them here; BAHN_IMPERSONATE pins one target for a quick test.
_IMPERSONATE_FALLBACKS = ("chrome124", "safari180", "firefox135")
IMPERSONATE = os.environ.get("BAHN_IMPERSONATE") or _IMPERSONATE_FALLBACKS[0]

# Targets still worth trying after a 403. An explicit BAHN_IMPERSONATE means "use exactly
# this", so rotation is disabled in that case.
_ROTATION = () if os.environ.get("BAHN_IMPERSONATE") else _IMPERSONATE_FALLBACKS[1:]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.bahn.de",
    "Referer": "https://www.bahn.de/buchung/fahrplan/suche",
}

# Datacenter IPs (e.g. Hetzner prod) get probabilistically blocked with 403 by Akamai
# even with a valid TLS fingerprint. A bounded retry on the same session lands the
# request statistically (1 good try out of N). Default 0 = no retry (unchanged local
# behaviour); set on prod. ponytail: env knob, no redeploy to tune.
MAX_RETRIES = int(os.environ.get("BAHN_MAX_RETRIES", "0"))

# All long-distance and local product categories, as the bahn.de frontend sends them.
ALL_PRODUCTS = [
    "ICE", "EC_IC", "IR", "REGIONAL", "SBAHN",
    "BUS", "SCHIFF", "UBAHN", "TRAM", "ANRUFPFLICHTIG",
]

CONFIG_DIR = Path.home() / ".config" / "bahn-de"
CONFIG_FILE = CONFIG_DIR / "config.json"
# Former location (pre-0.3.0, when the package was "cli-anything-bahn-de").
_LEGACY_CONFIG_FILE = Path.home() / ".config" / "cli-anything-bahn-de" / "config.json"

ALLOWED_CONFIG_KEYS = {
    "bahncard": {"none", "25", "50"},
    "bahncard_class": {"1", "2"},
    "first_class": {"true", "false"},
    "deutschlandticket": {"true", "false"},
}


# --- Errors ------------------------------------------------------------------

class BahnApiError(RuntimeError):
    """Raised when the bahn.de API returns an error or is unreachable."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# --- HTTP --------------------------------------------------------------------

_session = None

# The target currently believed good. Rotates on 403 and then sticks, so the cost of a
# dead fingerprint is paid once per process, not once per request.
_active_impersonate = IMPERSONATE


def _get_session():
    global _session
    if _session is None:
        _session = cffi_requests.Session()
    return _session


def _request(method, url, *, params=None, json_body=None, timeout=30,
             _retried=False, _attempt=0, _rotation=None):
    """Single HTTP entry point. Returns parsed JSON.

    Politeness: no parallel requests, one retry on 429. On 403 (Akamai) first rotate
    through the remaining impersonation targets, then fall back to ``BAHN_MAX_RETRIES``
    retries with a small capped backoff, then raise.

    Two different 403 causes, handled in that order: a fingerprint Akamai has aged out
    (rotation fixes it) and datacenter-IP reputation (retry barely helps; on the Hetzner
    prod IP the block is sticky and the real fix is a residential proxy, see STATUS.md).
    """
    global _active_impersonate
    if _rotation is None:
        _rotation = [t for t in _ROTATION if t != _active_impersonate]
    session = _get_session()
    try:
        resp = session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=_HEADERS,
            impersonate=_active_impersonate,
            timeout=timeout,
        )
    except Exception as e:  # network-level failure
        raise BahnApiError(f"Anfrage an bahn.de fehlgeschlagen: {e}")

    status = resp.status_code

    if status in (200, 201):
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise BahnApiError(f"bahn.de hat ungueltiges JSON geliefert: {e}", status_code=status)

    if status == 403:
        if _rotation:
            # Cheapest explanation first: this fingerprint died. Swap and retry at once,
            # no backoff, since a different TLS signature is a genuinely different request.
            _active_impersonate = _rotation[0]
            return _request(
                method, url, params=params, json_body=json_body,
                timeout=timeout, _retried=_retried, _attempt=_attempt,
                _rotation=_rotation[1:],
            )
        if _attempt < MAX_RETRIES:
            time.sleep(min(0.5 * 2 ** _attempt, 2))  # 0.5, 1, 2, 2, ...
            return _request(
                method, url, params=params, json_body=json_body,
                timeout=timeout, _retried=_retried, _attempt=_attempt + 1,
                _rotation=(),
            )
        raise BahnApiError(
            "bahn.de hat die Anfrage blockiert (HTTP 403, Akamai-Bot-Schutz). "
            f"Alle impersonate-Targets erschoepft ({', '.join(_IMPERSONATE_FALLBACKS)}), "
            "Retry-Budget (BAHN_MAX_RETRIES) ebenfalls. Entweder IP-Reputation "
            "(Datacenter-IP -> Residential-Proxy) oder alle Fingerprints sind veraltet "
            "(curl_cffi aktualisieren, neue Targets in _IMPERSONATE_FALLBACKS eintragen).",
            status_code=403,
        )

    if status == 429 and not _retried:
        time.sleep(5)
        return _request(
            method, url, params=params, json_body=json_body,
            timeout=timeout, _retried=True,
        )

    if status == 422:
        raise BahnApiError(
            f"bahn.de hat die Anfrage abgelehnt (HTTP 422). Antwort: {resp.text[:300]}",
            status_code=422,
        )

    raise BahnApiError(
        f"bahn.de API-Fehler (HTTP {status}): {resp.text[:300]}",
        status_code=status,
    )


# --- DB Navigator app API ------------------------------------------------------
# Since 2026-09 Akamai demands a JS-generated `_abck` cookie on www.bahn.de/web/api,
# so no TLS fingerprint gets through any more (all 44 curl_cffi targets 403). The
# DB Navigator app API serves the same data without that check. Search and station
# lookup go there; the adapters below translate web-format payloads in and
# web-format responses out, so formatters/service/web stay untouched.
# Request format: github.com/public-transport/db-vendo-client, profile p/dbnav.

MOB_HOST = "https://app.services-bahn.de/mob"
_MOB_JOURNEY_CT = "application/x.db.vendo.mob.verbindungssuche.v9+json"
_MOB_LOCATION_CT = "application/x.db.vendo.mob.location.v3+json"


def _mob_post(path, body, content_type, timeout=30):
    headers = {
        "X-Correlation-ID": f"{uuid.uuid4()}_{uuid.uuid4()}",
        "Accept": content_type,
        "Content-Type": content_type,
    }
    try:
        resp = cffi_requests.post(f"{MOB_HOST}/{path}", json=body, headers=headers, timeout=timeout)
    except Exception as e:
        raise BahnApiError(f"Anfrage an die DB-App-API fehlgeschlagen: {e}")
    if resp.status_code != 200:
        raise BahnApiError(
            f"DB-App-API-Fehler (HTTP {resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise BahnApiError(f"DB-App-API hat ungueltiges JSON geliefert: {e}", status_code=200)


def _with_offset(naive_iso):
    """'2026-11-16T08:00:00' (German local) -> '2026-11-16T08:00:00+01:00'."""
    dt = datetime.fromisoformat(naive_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BERLIN)
    return dt.isoformat()


def _naive(iso):
    """Drop the offset again: the web API returned local time without one."""
    return iso[:19] if iso else iso


# Web product names -> app names (db-vendo-client lib/products.js, `dbnav` field).
_MOB_PRODUCTS = {
    "ICE": "HOCHGESCHWINDIGKEITSZUEGE", "EC_IC": "INTERCITYUNDEUROCITYZUEGE",
    "IR": "INTERREGIOUNDSCHNELLZUEGE", "REGIONAL": "NAHVERKEHRSONSTIGEZUEGE",
    "SBAHN": "SBAHNEN", "BUS": "BUSSE", "SCHIFF": "SCHIFFE", "UBAHN": "UBAHN",
    "TRAM": "STRASSENBAHN", "ANRUFPFLICHTIG": "ANRUFPFLICHTIGEVERKEHRE",
}


def _mob_products(web_products):
    if not web_products or set(web_products) >= set(ALL_PRODUCTS):
        return ["ALL"]
    return [_MOB_PRODUCTS[p] for p in web_products if p in _MOB_PRODUCTS]


def _mob_journey_body(p):
    """Web /angebote/fahrplan payload -> app /angebote/fahrplan body."""
    reisende = []
    for r in p.get("reisende") or []:
        erm = [f"{e['art']} {e['klasse']}" for e in r.get("ermaessigungen") or []]
        reisende += [{"ermaessigungen": erm, "reisendenTyp": r.get("typ", "ERWACHSENER")}] * int(r.get("anzahl", 1))
    wunsch = {
        "abgangsLocationId": p["abfahrtsHalt"],
        "zielLocationId": p["ankunftsHalt"],
        "verkehrsmittel": _mob_products(p.get("produktgattungen")),
        "alternativeHalteBerechnung": True,
        "zeitWunsch": {"reiseDatum": _with_offset(p["anfrageZeitpunkt"]), "zeitPunktArt": p.get("ankunftSuche", "ABFAHRT")},
        "fahrradmitnahme": bool(p.get("bikeCarriage")),
    }
    if p.get("maxUmstiege") is not None:
        wunsch["maxUmstiege"] = p["maxUmstiege"]
    if p.get("minUmstiegszeit") is not None:
        wunsch["minUmstiegsdauer"] = p["minUmstiegszeit"]
    if p.get("zwischenhalte"):
        wunsch["viaLocations"] = [
            {"locationId": z["id"], "minUmstiegsdauer": z.get("aufenthaltsdauer")} for z in p["zwischenhalte"]
        ]
    return {
        "autonomeReservierung": False,
        "einstiegsTypList": ["STANDARD"],
        "fahrverguenstigungen": {
            "deutschlandTicketVorhanden": bool(p.get("deutschlandTicketVorhanden")),
            "nurDeutschlandTicketVerbindungen": bool(p.get("nurDeutschlandTicketVerbindungen")),
        },
        "klasse": p.get("klasse", "KLASSE_2"),
        "reisendenProfil": {"reisende": reisende},
        "reservierungsKontingenteVorhanden": False,
        "reiseHin": {"wunsch": wunsch},
    }


def _web_section(s):
    """App verbindungsAbschnitt -> the web shape formatters/predictor read."""
    fahrzeug = s.get("typ") == "FAHRZEUG"
    return {
        **s,
        "abfahrt": {"sollzeit": _naive(s.get("abgangsDatum"))},
        "ankunft": {"sollzeit": _naive(s.get("ankunftsDatum"))},
        "abfahrtsOrt": (s.get("abgangsOrt") or {}).get("name"),
        "ankunftsOrt": (s.get("ankunftsOrt") or {}).get("name"),
        "verkehrsmittel": {
            "typ": "PUBLICTRANSPORT" if fahrzeug else s.get("typ"),
            "name": s.get("mitteltext") or s.get("langtext"),
            "mittelText": s.get("mitteltext"),
            "kurzText": s.get("kurztext"),
            "nummer": s.get("zugNummer"),
            "produktGattung": s.get("produktGattung"),
        },
    }


def _web_connection(v):
    """App verbindungen[] element -> web-format connection dict."""
    vb = v.get("verbindung") or {}
    ang = v.get("angebote") or {}
    preise = ang.get("preise") or {}
    ab = (preise.get("gesamt") or {}).get("ab")
    return {
        "tripId": vb.get("kontext"),
        "ctxRecon": vb.get("kontext"),
        "verbindungsDauerInSeconds": vb.get("reiseDauer"),
        "umstiegsAnzahl": vb.get("umstiegeAnzahl"),
        "verbindungsAbschnitte": [_web_section(s) for s in vb.get("verbindungsAbschnitte") or []],
        "angebotsPreis": {"betrag": ab.get("betrag"), "waehrung": ab.get("waehrung")} if ab else None,
        "hasTeilpreis": bool(preise.get("istTeilpreis")),
        "isAlterseingabeErforderlich": bool(ang.get("alterseingabeErforderlich")),
    }


# --- Stations ----------------------------------------------------------------

def search_stations(query, limit=10):
    """Search stations/places. Returns web-orte-shaped dicts (id, name, extId)."""
    if not query or not str(query).strip():
        raise ValueError("Die Bahnhofssuche darf nicht leer sein.")
    data = _mob_post(
        "location/search",
        {"locationTypes": ["ST", "ADR", "POI"], "searchTerm": query, "maxResults": limit},
        _MOB_LOCATION_CT,
    )
    if not isinstance(data, list):
        raise BahnApiError("Unerwartete Orts-Antwort (Liste erwartet).")
    return [{**d, "id": d.get("locationId"), "extId": d.get("evaNr")} for d in data]


def resolve_station(name_or_lid):
    """Resolve a name or LID string to a station dict.

    A value containing ``@O=`` is treated as a LID and passed through. Otherwise
    the first match of search_stations() is used.

    Returns ``{"id": <lid>, "name": <str>, "extId": <str>, "alternatives": [...]}``.
    """
    value = (name_or_lid or "").strip()
    if not value:
        raise ValueError("Bahnhofsname oder LID darf nicht leer sein.")

    if "@O=" in value:
        return {"id": value, "name": value, "extId": None, "alternatives": []}

    matches = search_stations(value, limit=5)
    if not matches:
        raise BahnApiError(f"Kein Bahnhof gefunden fuer '{value}'.")
    first = matches[0]
    return {
        "id": first.get("id"),
        "name": first.get("name"),
        "extId": first.get("extId"),
        "alternatives": [m.get("name") for m in matches[1:]],
    }


# --- Payload builder ---------------------------------------------------------

def build_journey_payload(
    from_lid,
    to_lid,
    when_iso,
    *,
    arrival=False,
    first_class=False,
    bahncard=None,
    bahncard_class=2,
    deutschlandticket=False,
    dticket_only=False,
    max_transfers=None,
    min_transfer_time=None,
    bike=False,
    passengers=1,
    zwischenhalte=None,
):
    """Build the /angebote/fahrplan request body.

    Pure function (no HTTP). Optional fields are only set when they differ from
    the verified minimal payload, to keep the request close to the browser's.

    ``when_iso`` is local German time without offset, e.g. ``2026-06-19T09:00:00``.
    ``bahncard`` is None, 25 or 50. ``bahncard_class`` is the class OF THE BAHNCARD.
    """
    if bahncard in (None, "none"):
        ermaessigungen = [{"art": "KEINE_ERMAESSIGUNG", "klasse": "KLASSENLOS"}]
    else:
        art = f"BAHNCARD{int(bahncard)}"
        bc_klasse = "KLASSE_1" if str(bahncard_class) == "1" else "KLASSE_2"
        ermaessigungen = [{"art": art, "klasse": bc_klasse}]

    payload = {
        "abfahrtsHalt": from_lid,
        "ankunftsHalt": to_lid,
        "anfrageZeitpunkt": when_iso,
        "ankunftSuche": "ANKUNFT" if arrival else "ABFAHRT",
        "klasse": "KLASSE_1" if first_class else "KLASSE_2",
        "produktgattungen": list(ALL_PRODUCTS),
        "reisende": [
            {
                "typ": "ERWACHSENER",
                "ermaessigungen": ermaessigungen,
                "alter": [],
                "anzahl": int(passengers),
            }
        ],
        "schnelleVerbindungen": True,
        "sitzplatzOnly": False,
        "bikeCarriage": bool(bike),
        "reservierungsKontingenteVorhanden": False,
    }

    if deutschlandticket:
        payload["deutschlandTicketVorhanden"] = True
    if dticket_only:
        payload["nurDeutschlandTicketVerbindungen"] = True
    if max_transfers is not None:
        payload["maxUmstiege"] = int(max_transfers)
    if min_transfer_time is not None:
        payload["minUmstiegszeit"] = int(min_transfer_time)
    if zwischenhalte:
        payload["zwischenhalte"] = list(zwischenhalte)

    return payload


# --- Connection search -------------------------------------------------------

def search_connections(payload):
    """Search via the app API. Takes and returns the web /angebote/fahrplan shapes."""
    data = _mob_post("angebote/fahrplan", _mob_journey_body(payload), _MOB_JOURNEY_CT)
    return {"verbindungen": [_web_connection(v) for v in data.get("verbindungen") or []]}


def create_share_vbid(*, start_ort, ziel_ort, hinfahrt_datum, ctx_recon):
    """POST /angebote/verbindung/teilen to mint a shareable Verbindungs-ID.

    This is the same call the bahn.de frontend makes for "Verbindung teilen".
    The returned ``vbid`` (a UUID) plugs into ``/buchung/start?vbid=<vbid>``,
    which reconstructs the exact connection and opens the offer selection page
    directly - i.e. as if the user had clicked "Weiter" on the search results.

    ``ctx_recon`` is the connection's ``ctxRecon`` token from the search
    response; ``hinfahrt_datum`` its departure ISO timestamp. ``start_ort`` and
    ``ziel_ort`` are display labels only (the recon token encodes the actual
    stops). Returns the vbid string, or ``None`` if the response lacks one.
    """
    body = {
        "startOrt": start_ort or "",
        "zielOrt": ziel_ort or "",
        "hinfahrtDatum": hinfahrt_datum,
        "hinfahrtRecon": ctx_recon,
    }
    data = _request(
        "POST", f"{WWW_HOST}/web/api/angebote/verbindung/teilen", json_body=body
    )
    return (data or {}).get("vbid")


def recon_price(ctx_recon, *, first_class=False, deutschlandticket=False, reisende):
    """POST /angebote/recon to retrieve the price for a via-connection.

    Returns ``{"preis", "waehrung", "has_teilpreis", "teilpreise": [...]}``.
    ``teilpreise`` contains individual segment prices from ``fahrtAngebote``.
    All field access is defensive; a missing price yields ``preis=None``.
    """
    body = {
        "ctxRecon": ctx_recon,
        "klasse": "KLASSE_1" if first_class else "KLASSE_2",
        "reisende": reisende,
        "deutschlandTicketVorhanden": bool(deutschlandticket),
    }
    data = _request("POST", f"{WWW_HOST}/web/api/angebote/recon", json_body=body)
    conns = data.get("verbindungen") or []
    if not conns:
        return {"preis": None, "waehrung": None, "has_teilpreis": False, "teilpreise": []}
    c = conns[0]
    price_obj = (c.get("angebotsPreis") or {})
    teilpreise = []
    for angebot in (c.get("reiseAngebote") or []):
        hinfahrt = (angebot.get("hinfahrt") or {})
        for fa in (hinfahrt.get("fahrtAngebote") or []):
            p = (fa.get("preis") or {})
            if p.get("betrag") is not None:
                teilpreise.append({"betrag": p["betrag"], "waehrung": p.get("waehrung")})
    return {
        "preis": price_obj.get("betrag"),
        "waehrung": price_obj.get("waehrung"),
        "has_teilpreis": bool(c.get("hasTeilpreis")),
        "teilpreise": teilpreise,
    }


def resolve_via_dwell(from_lid, via_lid, when_iso, weiterreise_iso, fare_params):
    """Compute aufenthaltsdauer (minutes) for a via stop.

    Does a pre-search from_lid -> via_lid starting at when_iso, takes the
    earliest arrival as reference. If weiterreise_iso falls on or before that
    arrival (the LLM passed a time without the correct date), it is advanced
    day-by-day until it lies after the arrival. The result is
    ``max(1, floor((weiterreise - reference).total_seconds() / 60) - 1)``;
    the -1 ensures the first departure AFTER weiterreise_iso is selected, and
    the floor of 1 guards against a zero/negative dwell yielding no results.

    ``fare_params`` is a dict with keys accepted by ``build_journey_payload``
    (first_class, bahncard, bahncard_class, deutschlandticket).
    """
    payload = build_journey_payload(from_lid, via_lid, when_iso, **fare_params)
    data = search_connections(payload)
    verbindungen = data.get("verbindungen") or []
    if not verbindungen:
        raise BahnApiError(
            f"Keine Verbindung fuer Zwischenhalt-Kalkulation gefunden (via LID: {via_lid})."
        )

    earliest_arrival = None
    for v in verbindungen:
        sections = v.get("verbindungsAbschnitte") or []
        if sections:
            arr_iso = (sections[-1].get("ankunft") or {}).get("sollzeit")
            if arr_iso:
                try:
                    arr_dt = datetime.fromisoformat(arr_iso)
                    if earliest_arrival is None or arr_dt < earliest_arrival:
                        earliest_arrival = arr_dt
                except (ValueError, TypeError):
                    pass

    if earliest_arrival is None:
        raise BahnApiError("Ankunftszeit am Zwischenhalt konnte nicht ermittelt werden.")

    try:
        weiterreise_dt = datetime.fromisoformat(weiterreise_iso)
    except (ValueError, TypeError):
        raise ValueError(f"Ungueltige Weiterreise-ISO-Zeit: {weiterreise_iso!r}")

    # Wenn Weiterreise-Zeit vor oder gleich Ankunft liegt, muss das LLM den nächsten
    # Tag oder einen späteren Zeitpunkt gemeint haben. Tage addieren bis sinnvoll.
    adjusted = weiterreise_dt
    while adjusted <= earliest_arrival:
        adjusted += timedelta(days=1)
    weiterreise_dt = adjusted

    minutes = int((weiterreise_dt - earliest_arrival).total_seconds() / 60)
    return max(1, minutes - 1)


def _departure_iso(raw):
    """Departure timestamp of a raw connection (first section's sollzeit)."""
    sections = raw.get("verbindungsAbschnitte") or []
    if not sections:
        return None
    return (sections[0].get("abfahrt") or {}).get("sollzeit")


def search_window(payload, anchor_iso, *, before=0, after=0, polite_delay=1.5):
    """Return raw connections departing within [anchor-before, anchor+after].

    The /fahrplan endpoint only returns departures from ``anfrageZeitpunkt``
    onward, so the "before" side needs a second call at the earlier edge. At
    most two polite calls (one when before == 0): an anchor call covering the
    "after" side, plus an earlier call for the "before" side. Results are
    deduped by tripId, filtered to the window, and sorted by departure.
    """
    anchor = datetime.fromisoformat(anchor_iso)
    lo = anchor - timedelta(minutes=before)
    hi = anchor + timedelta(minutes=after)

    starts = [anchor]
    if before > 0:
        starts.insert(0, lo)

    seen = {}
    for i, start in enumerate(starts):
        page = dict(payload)
        page["anfrageZeitpunkt"] = start.strftime("%Y-%m-%dT%H:%M:%S")
        data = search_connections(page)
        for v in data.get("verbindungen") or []:
            tid = v.get("tripId")
            if tid and tid not in seen:
                seen[tid] = v
        if i < len(starts) - 1:
            time.sleep(polite_delay)

    windowed = []
    for v in seen.values():
        dep = _departure_iso(v)
        dt = datetime.fromisoformat(dep) if dep else None
        if dt and lo <= dt <= hi:
            windowed.append(v)
    windowed.sort(key=lambda v: _departure_iso(v) or "")
    return windowed


# --- Day best prices ---------------------------------------------------------

def day_best_prices(payload, *, polite_delay=1.5):
    """Return ``(verbindungen_raw, source)`` for the cheapest fares of a day.

    Pages the search at 05/09/13/17/21h and dedupes by tripId. The day is taken
    from ``payload['anfrageZeitpunkt']`` (its date part).

    ponytail: the web /tagesbestpreis stages were dropped when www.bahn.de/web/api
    became unreachable (2026-09, Akamai `_abck`). The app has its own
    /mob/angebote/tagesbestpreis; wire it up if five paging calls get too slow.
    """
    day = payload["anfrageZeitpunkt"][:10]
    seen = {}
    for hour in ("05", "09", "13", "17", "21"):
        page = dict(payload)
        page["anfrageZeitpunkt"] = f"{day}T{hour}:00:00"
        data = search_connections(page)
        for v in data.get("verbindungen", []) or []:
            tid = v.get("tripId")
            if tid and tid not in seen:
                seen[tid] = v
        time.sleep(polite_delay)
    return list(seen.values()), "paging-fallback"


def _flatten_bestpreis(data):
    """Flatten a tagesbestpreis response into a list of connection-like dicts.

    Each interval (key ``intervalle`` or ``tagesbestPreisIntervalle``) holds
    ``verbindungen``; each element embeds a ``verbindung`` object plus price
    fields (``abPreis``) alongside it. Merge both levels so the formatter can
    read a flat dict.
    """
    intervals = data.get("intervalle") or data.get("tagesbestPreisIntervalle") or []
    merged = []
    for interval in intervals:
        for el in interval.get("verbindungen", []) or []:
            inner = el.get("verbindung", {}) or {}
            flat = {**el, **inner}
            merged.append(flat)
    return merged


# --- Config ------------------------------------------------------------------

def get_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return CONFIG_DIR


def _migrate_legacy_config():
    """One-time move of the pre-0.3.0 config into the new ~/.config/bahn-de location."""
    if CONFIG_FILE.exists() or not _LEGACY_CONFIG_FILE.exists():
        return
    try:
        get_config_dir()
        _LEGACY_CONFIG_FILE.replace(CONFIG_FILE)
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def load_config():
    _migrate_legacy_config()
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_config(config):
    get_config_dir()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    CONFIG_FILE.chmod(0o600)


def effective_params(cli_overrides):
    """Merge precedence CLI flags > config.json > defaults.

    ``cli_overrides`` is a dict that may contain keys: bahncard, bahncard_class,
    first_class, deutschlandticket. Values that are None mean "not set on CLI".
    Returns a fully-resolved dict with the same keys.
    """
    defaults = {
        "bahncard": None,
        "bahncard_class": 2,
        "first_class": False,
        "deutschlandticket": False,
    }
    cfg = load_config()
    result = dict(defaults)

    if cfg.get("bahncard") in ("25", "50"):
        result["bahncard"] = cfg["bahncard"]
    if str(cfg.get("bahncard_class")) in ("1", "2"):
        result["bahncard_class"] = int(cfg["bahncard_class"])
    if isinstance(cfg.get("first_class"), bool):
        result["first_class"] = cfg["first_class"]
    if isinstance(cfg.get("deutschlandticket"), bool):
        result["deutschlandticket"] = cfg["deutschlandticket"]

    for key in ("bahncard", "bahncard_class", "first_class", "deutschlandticket"):
        val = cli_overrides.get(key)
        if val is not None:
            result[key] = val

    return result
