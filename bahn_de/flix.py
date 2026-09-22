"""Flixbus/FlixTrain als Preis-Provider.

Warum: bahn.de verkauft nur, was die DB vertreibt. Rein ausländische Relationen
(Krakau->Budapest, Paris->Lissabon, Wien->Zagreb) liefern dort Fahrplan, aber
``preis: null``. Genau dort hat Flix echte, buchbare Preise.

Die öffentliche Such-API von Flix braucht keinen Key und hat keinen Bot-Schutz
(geprüft 2026-07-29). Endpunkte sind undokumentiert, aber seit Jahren stabil.

Vertrag wie ``navitia.py``: ``available()``, ``search_journeys_flix()``, ``FlixError``.
"""
from __future__ import annotations

from datetime import datetime

from curl_cffi import requests as cffi_requests

from .backend import BahnApiError
from .models import Connection, Section, SearchResult

_BASE = "https://global.api.flixbus.com"
_TIMEOUT = 30

# ponytail: Prozess-lokaler Cache. Städte-IDs sind stabile UUIDs, ein dict reicht;
# ein Cache mit TTL/Persistenz wäre Aufwand ohne Nutzen für einen CLI-Lauf.
_city_cache: dict[str, dict] = {}


class FlixError(BahnApiError):
    """Fehler der Flix-API oder Ort nicht auflösbar."""


def available() -> bool:
    """Flix braucht keinen API-Key, ist also immer verfügbar."""
    return True


def _get(path, params):
    try:
        resp = cffi_requests.get(
            f"{_BASE}{path}",
            params=params,
            headers={"Accept": "application/json"},
            impersonate="chrome124",
            timeout=_TIMEOUT,
        )
    except Exception as e:
        raise FlixError(f"Anfrage an Flixbus fehlgeschlagen: {e}")
    if resp.status_code != 200:
        raise FlixError(
            f"Flixbus API-Fehler (HTTP {resp.status_code}): {resp.text[:200]}",
            status_code=resp.status_code,
        )
    try:
        return resp.json()
    except ValueError as e:
        raise FlixError(f"Flixbus hat ungueltiges JSON geliefert: {e}")


def resolve_city(query: str) -> dict:
    """Ortsname -> Flix-Stadt (``{id, name}``). Nimmt den Top-Treffer.

    Flix kennt nur Städte, keine Bahnhöfe, und matcht unscharf. Der Bahnhofszusatz
    muss deshalb VOR der Abfrage weg: "Paris Gare de Lyon" liefert sonst als
    Top-Treffer Lyon statt Paris, also stillschweigend die falsche Stadt.
    """
    if not query or not str(query).strip():
        raise ValueError("Die Ortssuche darf nicht leer sein.")
    key = str(query).strip().lower()
    if key in _city_cache:
        return _city_cache[key]

    city_name = _strip_station_suffix(query)
    data = _get("/search/autocomplete/cities", {"q": city_name, "lang": "de"})
    if not data and city_name != str(query).strip():
        # Normalisierung war zu aggressiv - Rohform versuchen.
        data = _get("/search/autocomplete/cities", {"q": query, "lang": "de"})
    if not data:
        raise FlixError(f"Flixbus kennt keinen Ort '{query}'.")

    top = data[0]
    result = {"id": top["id"], "name": top.get("name") or city_name}
    _city_cache[key] = result
    return result


# Bahnhofszusätze, die vor der Flix-Ortssuche entfernt werden. Reihenfolge zählt:
# Längeres zuerst, damit " Gare de Lyon" nicht als " Gare" halb stehen bleibt.
_STATION_SUFFIXES = (
    " Glavni kolodvor", " Glavni kol.", " Gare de Lyon", " Gare du Nord",
    " Gare de l'Est", " Centraal Station", " Hauptbahnhof", " Centralna",
    " Centraal", " Centrale", " Termini", " Sants", " Oriente", " Keleti",
    " Nyugati", " Glowny", " Główny", " hl.n.", " Hbf.", " Hbf", " Bhf", " Bf",
)


def _strip_station_suffix(name: str) -> str:
    """"Berlin Hbf" -> "Berlin", "Paris Gare de Lyon" -> "Paris".

    Nur echte Bahnhofszusätze fallen weg; Städtenamen bleiben unangetastet.
    """
    # Klammerzusätze zuerst, damit "Frankfurt(Main)Hbf" ein trennbares " Hbf" bekommt.
    out = str(name).replace("(Main)", " ").replace("(M)", " ")
    out = " ".join(out.split()).strip()
    for suffix in _STATION_SUFFIXES:
        if out.lower().endswith(suffix.lower()):
            out = out[: -len(suffix)]
            break
    return " ".join(out.split()).strip(" ,-") or str(name).strip()


def _duration_minutes(dur) -> int | None:
    if not isinstance(dur, dict):
        return None
    return (dur.get("hours") or 0) * 60 + (dur.get("minutes") or 0)


def _iso(value):
    """Flix liefert '2026-08-20T07:15:00+02:00'; das Schema will lokale naive Zeit."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None).isoformat()
    except (TypeError, ValueError):
        return value


def _price(result, platform_fee_required: bool):
    """Der Preis, den man tatsächlich zahlt.

    Flix weist ``total`` ohne und ``total_with_platform_fee`` mit Servicegebühr aus.
    Ist die Gebühr Pflicht, wäre ``total`` ein zu niedriger Vergleichswert, und beim
    Vergleich gegen einen DB-Endpreis genau die falsche Zahl.
    """
    price = result.get("price") or {}
    if platform_fee_required and price.get("total_with_platform_fee") is not None:
        return price["total_with_platform_fee"]
    return price.get("total")


def _map_result(result, cities, stations, platform_fee_required) -> Connection:
    def place(node):
        sid, cid = node.get("station_id"), node.get("city_id")
        station = stations.get(sid) or {}
        city = cities.get(cid) or {}
        return station.get("name") or city.get("name")

    legs = result.get("legs") or []
    sections = [
        Section(
            abfahrt=_iso((leg.get("departure") or {}).get("date")),
            abfahrt_ort=place(leg.get("departure") or {}),
            ankunft=_iso((leg.get("arrival") or {}).get("date")),
            ankunft_ort=place(leg.get("arrival") or {}),
            produkt=(leg.get("means_of_transport") or "bus").upper(),
            typ="PUBLICTRANSPORT",
        )
        for leg in legs
    ]

    operators = {leg.get("operator_id") for leg in legs if leg.get("operator_id")}
    produkte = sorted(
        {(leg.get("means_of_transport") or "bus").upper() for leg in legs}
    ) or ["BUS"]

    return Connection(
        trip_id=result.get("uid"),
        abfahrt=_iso((result.get("departure") or {}).get("date")),
        abfahrt_ort=place(result.get("departure") or {}),
        ankunft=_iso((result.get("arrival") or {}).get("date")),
        ankunft_ort=place(result.get("arrival") or {}),
        dauer_minuten=_duration_minutes(result.get("duration")),
        umstiege=max(len(legs) - 1, 0),
        produkte=produkte,
        abschnitte=sections,
        preis=_price(result, platform_fee_required),
        waehrung="EUR",
    )


def search_journeys_flix(from_station, to_station, *, when_iso,
                         arrival=False, limit=5) -> SearchResult:
    """Sucht Flix-Verbindungen. Gibt Connections mit echten Preisen zurück."""
    origin = resolve_city(from_station)
    destination = resolve_city(to_station)

    day = when_iso[:10]
    try:
        date_str = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        raise FlixError(f"Ungueltiges Datum '{when_iso}' (erwartet YYYY-MM-DD).")

    data = _get("/search/service/v4/search", {
        "from_city_id": origin["id"],
        "to_city_id": destination["id"],
        "departure_date": date_str,
        "products": '{"adult":1}',
        "currency": "EUR",
        "locale": "de",
        "search_by": "cities",
        "include_after_midnight_rides": 1,
    })

    cities = data.get("cities") or {}
    stations = data.get("stations") or {}
    fee_required = bool(data.get("platform_fee_in_price_required"))

    connections = []
    for trip in data.get("trips") or []:
        for result in (trip.get("results") or {}).values():
            if result.get("status") != "available":
                continue
            connections.append(_map_result(result, cities, stations, fee_required))

    notices = []
    if not connections:
        notices.append(
            f"Flixbus hat fuer {origin['name']} -> {destination['name']} am {day} "
            "keine buchbare Verbindung."
        )

    # Flix sortiert nach Abfahrt; für die Preisvergleichs-Sicht ist das die
    # erwartete Reihenfolge, das Sortieren nach Preis macht `cheapest`.
    connections.sort(key=lambda c: c.abfahrt or "")
    if arrival:
        connections = [c for c in connections if (c.ankunft or "") <= when_iso] or connections

    if fee_required:
        notices.append("Flix-Preise inkl. Servicegebuehr (wie im Checkout).")

    return SearchResult(connections=connections[:limit], notices=notices)
