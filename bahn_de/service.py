"""Orchestration layer shared by the CLI, a future frontend, and agents.

Ties together station resolution, payload building, the search/day-best
calls, recon enrichment for via-connections, and parsing into pydantic
models (see ``models.py``). Performs no click/terminal I/O - hints meant
for the end user are returned as plain German strings in ``notices`` so
each caller (CLI, web handler, ...) can render them as it sees fit.
"""
from __future__ import annotations

import os
import re
import time as _time
from dataclasses import dataclass, field
from typing import Optional

from . import backend
from . import formatters
from . import flix as _flix
from . import omio as _omio
from . import heuristic as _heuristic
from . import interrail as _interrail
from . import navitia as _navitia
from . import predictor as _predictor
from .models import (
    Connection,
    FareParams,
    GroupLeg,
    GroupPlan,
    MeetingCandidate,
    MeetingResult,
    SearchResult,   # re-exported: lived here until providers multiplied
    Station,
)


def _score_connections(parsed: list[dict]) -> list[tuple]:
    """Wählt Backend anhand SCORE_BACKEND (heuristic|predictor|none).

    Default: heuristic (kein Container nötig).
    predictor: ruft den bahnvorhersage.de-Container auf (PREDICTOR_URL nötig).
    none: gibt immer (None, None) zurück.
    """
    backend_name = os.environ.get("SCORE_BACKEND", "heuristic").lower()
    if backend_name == "predictor":
        return _predictor.score_connections(parsed)
    if backend_name == "none":
        return [(None, None)] * len(parsed)
    return _heuristic.score_connections(parsed)

_VIA_AT_RE = re.compile(
    r'^(.+?)@(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)$'
)
_VIA_DUR_RE = re.compile(r'^(.+?):(\d+)([hdm]?)$')


@dataclass
class ResolvedStation:
    station: Station
    notices: list[str] = field(default_factory=list)


@dataclass
class CheapestResult:
    """Quellenuebergreifende Preisliste plus Interrail-Verdikt."""

    connections: list[dict]          # dicts, weil jede Zeile ein "quelle"-Feld traegt
    interrail: dict
    notices: list[str] = field(default_factory=list)


@dataclass
class DayBestResult:
    connections: list[Connection]
    source: str
    notices: list[str] = field(default_factory=list)


def resolve_fare(opts: dict) -> FareParams:
    """Resolve fare parameters from CLI option values (CLI > config > defaults).

    ``opts`` carries the raw values of the ``--bahncard``, ``--bahncard-class``,
    ``--first-class``/``--no-first-class`` and ``--deutschlandticket``/
    ``--no-deutschlandticket`` options (``None`` means "not set on the CLI").
    """
    cli_overrides = {
        "bahncard": opts.get("bahncard"),
        "bahncard_class": int(opts["bahncard_class"]) if opts.get("bahncard_class") else None,
        "first_class": opts.get("first_class"),
        "deutschlandticket": opts.get("deutschlandticket"),
    }
    return FareParams.model_validate(backend.effective_params(cli_overrides))


def resolve_station(name_or_lid) -> ResolvedStation:
    """Resolve a station name/LID and surface an ambiguity hint, if any."""
    raw = backend.resolve_station(name_or_lid)
    station = Station.model_validate(raw)
    notices = []
    if station.alternatives:
        alts = ", ".join(a for a in station.alternatives if a)
        if alts:
            notices.append(
                f"Hinweis: '{name_or_lid}' wurde zu '{station.name}' aufgelöst "
                f"(weitere Treffer: {alts})."
            )
    return ResolvedStation(station=station, notices=notices)


def _parse_via(value, from_lid, when_iso, fare: FareParams):
    """Parse a ``--via`` value to ``({"id", "aufenthaltsdauer"}, notices)``.

    Two forms accepted:
      'Station@YYYY-MM-DDTHH:MM'  -- weiterreise timestamp, dwell computed via pre-search
      'Station:72h' / ':3d' / ':4320m' / ':4320'  -- fixed minimum dwell
    """
    at_match = _VIA_AT_RE.match(value)
    if at_match:
        station_name = at_match.group(1)
        weiterreise_iso = at_match.group(2)
        if len(weiterreise_iso) == 16:
            weiterreise_iso += ":00"
        resolved = resolve_station(station_name)
        dwell = backend.resolve_via_dwell(
            from_lid, resolved.station.id, when_iso, weiterreise_iso,
            {
                "first_class": fare.first_class,
                "bahncard": fare.bahncard,
                "bahncard_class": fare.bahncard_class,
                "deutschlandticket": fare.deutschlandticket,
            },
        )
        return {"id": resolved.station.id, "aufenthaltsdauer": dwell}, resolved.notices

    dur_match = _VIA_DUR_RE.match(value)
    if dur_match:
        station_name = dur_match.group(1)
        amount = int(dur_match.group(2))
        unit = dur_match.group(3) or "m"
        if unit == "h":
            dwell = amount * 60
        elif unit == "d":
            dwell = amount * 24 * 60
        else:
            dwell = amount
        resolved = resolve_station(station_name)
        return {"id": resolved.station.id, "aufenthaltsdauer": dwell}, resolved.notices

    raise ValueError(
        f"Ungültiges --via Format: {value!r}. "
        "Verwende 'Station@YYYY-MM-DDTHH:MM' (Weiterreise-Zeitpunkt) "
        "oder 'Station:72h' / 'Station:3d' / 'Station:4320m' (Mindest-Aufenthalt)."
    )


def _build_payload(
    from_station, to_station, when_iso, *,
    fare: FareParams,
    arrival=False,
    max_transfers=None,
    min_transfer_time=None,
    bike=False,
    dticket_only=False,
    zwischenhalte=None,
    src_override: Optional[Station] = None,
):
    """Resolve stations and build the journey payload. Returns ``(payload, notices)``."""
    notices = []
    if src_override is not None:
        src = src_override
    else:
        resolved_src = resolve_station(from_station)
        src = resolved_src.station
        notices += resolved_src.notices

    resolved_dst = resolve_station(to_station)
    dst = resolved_dst.station
    notices += resolved_dst.notices

    payload = backend.build_journey_payload(
        src.id,
        dst.id,
        when_iso,
        arrival=arrival,
        first_class=fare.first_class,
        bahncard=fare.bahncard,
        bahncard_class=fare.bahncard_class,
        deutschlandticket=fare.deutschlandticket,
        dticket_only=dticket_only,
        max_transfers=max_transfers,
        min_transfer_time=min_transfer_time,
        bike=bike,
        zwischenhalte=zwischenhalte,
    )
    return payload, notices


def search_journeys(
    from_station, to_station, *,
    when_iso,
    fare: FareParams,
    arrival=False,
    max_transfers=None,
    min_transfer_time=None,
    max_transfer_time=None,
    bike=False,
    dticket_only=False,
    via_values=(),
    before=0,
    after=0,
    limit=10,
    source="auto",
) -> SearchResult:
    """Search connections, enrich via-stops with recon prices, and parse them.

    Mirrors the former ``search`` command body: resolves via-stops (dwell
    calculation), builds the payload, runs a window or plain search, enriches
    via-connections with ``recon_price`` (politely, ~1.2s/connection), parses
    and filters the raw results, and validates them into ``Connection`` models.

    ``source``: ``bahn`` (bahn.de only), ``flix`` (Flixbus/FlixTrain, real prices),
    ``omio`` (foreign rail inventory with real prices), ``navitia`` (pan-European, no
    prices), or ``auto`` (default: bahn.de first, then Flixbus, then navitia — for
    foreign-domestic routes bahn.de cannot price).
    """
    if source == "navitia":
        return _navitia.search_journeys_navitia(
            from_station, to_station, when_iso=when_iso, arrival=arrival, limit=limit)

    if source == "flix":
        return _flix.search_journeys_flix(
            from_station, to_station, when_iso=when_iso, arrival=arrival, limit=limit)

    if source == "omio":
        return _omio.search_journeys_omio(
            from_station, to_station, when_iso=when_iso, arrival=arrival, limit=limit)

    notices = []
    zwischenhalte = None
    src_override = None

    if via_values:
        if limit == 10:
            limit = 5  # default reduced for via: recon needed per connection
        resolved_src = resolve_station(from_station)
        src_override = resolved_src.station
        notices += resolved_src.notices
        zwischenhalte = []
        for v in via_values:
            zh, via_notices = _parse_via(v, src_override.id, when_iso, fare)
            zwischenhalte.append(zh)
            notices += via_notices
        notices.append(
            f"Hinweis: {len(via_values)} Zwischenhalt(e) gesetzt, "
            f"Preis wird per recon abgerufen ({limit} Verbindungen, ~1.2 s/Verbindung)."
        )

    payload, build_notices = _build_payload(
        from_station, to_station, when_iso,
        fare=fare, arrival=arrival, max_transfers=max_transfers,
        min_transfer_time=min_transfer_time, bike=bike, dticket_only=dticket_only,
        zwischenhalte=zwischenhalte, src_override=src_override,
    )
    notices += build_notices

    if before > 0 or after > 0:
        raw_list = backend.search_window(payload, when_iso, before=before, after=after)
    else:
        raw_list = (backend.search_connections(payload).get("verbindungen") or [])

    # Recon enrichment: via-connections have no price in /fahrplan response
    if zwischenhalte and raw_list:
        raw_list = list(raw_list[:limit])
        reisende = payload["reisende"]
        for raw in raw_list:
            ctx = raw.get("ctxRecon")
            if not ctx:
                continue
            try:
                rec = backend.recon_price(
                    ctx,
                    first_class=fare.first_class,
                    deutschlandticket=fare.deutschlandticket,
                    reisende=reisende,
                )
                if rec["preis"] is not None:
                    raw["angebotsPreis"] = {"betrag": rec["preis"], "waehrung": rec["waehrung"]}
                if rec["has_teilpreis"]:
                    raw["_has_teilpreis"] = True
                if rec["teilpreise"]:
                    raw["_teilpreise"] = rec["teilpreise"]
            except backend.BahnApiError:
                pass
            _time.sleep(1.2)

    parsed = [formatters.parse_connection(v) for v in raw_list]
    if max_transfer_time is not None:
        parsed = formatters.filter_by_transfer_time(parsed, max_minutes=max_transfer_time)
    if not zwischenhalte:
        parsed = parsed[:limit]

    scores = _score_connections(parsed)
    for p, (vs, pk) in zip(parsed, scores):
        if vs is not None:
            p["verbindungsscore"] = vs
        if pk is not None:
            p["puenktlichkeit"] = pk

    connections = [Connection.model_validate(c) for c in parsed]

    # auto fallback: bahn.de found nothing (typical for foreign-domestic legs).
    # Flix first, because it carries real prices; navitia only as a last resort,
    # since it answers with schedules alone.
    if source == "auto" and not connections and not via_values:
        try:
            alt = _flix.search_journeys_flix(
                from_station, to_station, when_iso=when_iso, arrival=arrival, limit=limit)
            if alt.connections:
                alt.notices.insert(0, "bahn.de fand keine Verbindung — Fallback auf Flixbus.")
                return alt
        except (_flix.FlixError, ValueError):
            pass
        if _navitia.available():
            try:
                alt = _navitia.search_journeys_navitia(
                    from_station, to_station, when_iso=when_iso, arrival=arrival, limit=limit)
                if alt.connections:
                    alt.notices.insert(0, "bahn.de fand keine Verbindung — Fallback auf navitia.io (ohne Preise).")
                    return alt
            except _navitia.NavitiaError:
                pass

    return SearchResult(connections=connections, notices=notices)


def price_sort_key(connection):
    """Nach Preis sortieren, Verbindungen ohne Preis ans Ende.

    ``preis is None`` heisst "online nicht verkaeuflich", nicht "kostenlos" - ohne
    das erste Tupelglied wanderten genau diese Verbindungen nach ganz oben.
    Akzeptiert Connection und dict.
    """
    preis = (connection.get("preis") if isinstance(connection, dict)
             else connection.preis)
    return (preis is None, preis if preis is not None else 0)


def search_cheapest(
    from_station, to_station, *,
    when_iso,
    fare: FareParams,
    limit=5,
    interrail_trips=1,
    alter=None,
) -> "CheapestResult":
    """Faechert ueber alle Preisquellen aus und mischt zu einer Preisliste.

    bahn.de und Flixbus laufen parallel, weil beide netzgebunden sind und der
    bahn.de-Call die lange Stange ist. Faellt eine Quelle aus, wird das eine Notiz,
    kein Abbruch: eine halbe Antwort ist hier deutlich besser als gar keine.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _bahn():
        return search_journeys(
            from_station, to_station, when_iso=when_iso, fare=fare,
            limit=limit, source="bahn")

    def _flix_search():
        return _flix.search_journeys_flix(
            from_station, to_station, when_iso=when_iso, limit=limit)

    def _omio_search():
        # Omio liefert Bus und Bahn gemischt und sortiert wie alle Provider nach
        # Abfahrt. Bei limit=5 fuellen die haeufigen Busse alle Plaetze und die
        # Bahnverbindung - der Grund fuer die Anbindung - faellt raus. Also breit
        # holen und die guenstigsten behalten.
        result = _omio.search_journeys_omio(
            from_station, to_station, when_iso=when_iso, limit=limit * 20)
        cheapest = sorted(result.connections, key=price_sort_key)[:limit]
        return SearchResult(connections=cheapest, notices=result.notices)

    notices: list[str] = []
    rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            "bahn.de": pool.submit(_bahn),
            "Flixbus": pool.submit(_flix_search),
            "Omio": pool.submit(_omio_search),
        }
        for label, future in futures.items():
            try:
                result = future.result()
            except Exception as e:                      # noqa: BLE001 - jede Quelle darf ausfallen
                notices.append(f"{label} nicht erreichbar: {e}")
                continue
            notices.extend(result.notices)
            for connection in result.connections:
                row = connection.model_dump(mode="json")
                row["quelle"] = label
                rows.append(row)

    rows.sort(key=price_sort_key)

    ohne_preis = [r for r in rows if r.get("preis") is None]
    if ohne_preis:
        # Ursache nicht behaupten: 'kein Preis' trifft Regionalverbindungen
        # (nicht online verkaeuflich) genauso wie rein auslaendische Relationen.
        notices.append(
            f"{len(ohne_preis)} Verbindung(en) ohne Online-Preis - nicht kostenlos, "
            "sondern ueber diese Quelle nicht buchbar."
        )

    # Ehrlichkeitsklausel: ohne Trainline-Partnervertrag deckt keine Quelle den
    # europaeischen Bahnvertrieb vollstaendig ab. Das gehoert in die Ausgabe.
    notices.append(
        "Abdeckung: bahn.de (Bahn, wo die DB vertreibt) + Flixbus (Fernbus/FlixTrain) "
        "+ Omio (auslaendisches Bahn-/Businventar). Vollstaendigkeit fuer den "
        "europaeischen Bahnvertrieb ist damit nicht garantiert."
    )

    # Je Quelle eine Vergleichszeile: die guenstigste mit Preis, sonst die erste ohne.
    # Quellen ohne Preis muessen mit, sonst vergleicht der Interrail-Check auf
    # Paris->Lissabon den 26-Stunden-Bus gegen den Pass und meldet "Einzeltickets
    # guenstiger", obwohl fuer die Bahn ueberhaupt kein Preis vorliegt.
    guenstigste_je_quelle: dict[str, dict] = {}
    for row in rows:                                   # rows ist bereits preis-sortiert
        guenstigste_je_quelle.setdefault(row["quelle"], row)

    pass_verdikt = _interrail.evaluate(
        list(guenstigste_je_quelle.values()) or rows[:1],
        trips=interrail_trips, alter=alter,
    )

    return CheapestResult(
        connections=rows[: limit * 2],
        interrail=pass_verdikt,
        notices=notices,
    )


def day_best(
    from_station, to_station, *,
    date,
    fare: FareParams,
    max_transfers=None,
    min_transfer_time=None,
    max_transfer_time=None,
    bike=False,
    dticket_only=False,
    top=5,
) -> DayBestResult:
    """Find the cheapest fares across a whole day, sorted and limited to ``top``."""
    payload, notices = _build_payload(
        from_station, to_station, f"{date}T00:00:00",
        fare=fare, max_transfers=max_transfers,
        min_transfer_time=min_transfer_time, bike=bike, dticket_only=dticket_only,
    )
    raw, source = backend.day_best_prices(payload)
    if source == "paging-fallback":
        notices.append(
            "Hinweis: Bestpreis-Endpunkt nicht verfügbar, Tag wird seitenweise "
            "abgefragt (das dauert ein paar Sekunden)."
        )

    parsed = [formatters.parse_connection(v) for v in raw]
    if max_transfer_time is not None:
        parsed = formatters.filter_by_transfer_time(parsed, max_minutes=max_transfer_time)

    parsed.sort(key=price_sort_key)
    parsed = parsed[:top]

    scores = _score_connections(parsed)
    for p, (vs, pk) in zip(parsed, scores):
        if vs is not None:
            p["verbindungsscore"] = vs
        if pk is not None:
            p["puenktlichkeit"] = pk

    connections = [Connection.model_validate(c) for c in parsed]
    return DayBestResult(connections=connections, source=source, notices=notices)


# ── Group trips ──────────────────────────────────────────────────────────────────
# Several people in different cities heading to one shared target, arrival-coordinated.
# Two entry points: plan_group() (fixed target) and suggest_meeting_point() (find the hub).

# Curated, well-connected hubs used as meeting-point candidates. Origins are added on top.
MEETING_HUBS = [
    "Hannover Hbf",
    "Frankfurt(Main)Hbf",
    "Kassel-Wilhelmshöhe",
    "Köln Hbf",
    "Berlin Hbf",
    "Leipzig Hbf",
    "Nürnberg Hbf",
    "Mannheim Hbf",
    "Fulda",
]


def _pick_arrival(conns: list[Connection], target_iso: str) -> Optional[Connection]:
    """Pick the best connection for a coordinated arrival.

    Prefer the one arriving latest but not after ``target_iso`` (so the group
    bunches up near the target time); if none arrive in time, take the earliest
    arrival. Ties break on fewest transfers, then highest verbindungsscore.
    """
    if not conns:
        return None

    def _sort_key(c: Connection):
        return (
            c.umstiege if c.umstiege is not None else 99,
            -(c.verbindungsscore if c.verbindungsscore is not None else 0.0),
        )

    in_time = [c for c in conns if c.ankunft and c.ankunft <= target_iso]
    if in_time:
        latest = max(c.ankunft for c in in_time)
        best_batch = [c for c in in_time if c.ankunft == latest]
        return sorted(best_batch, key=_sort_key)[0]
    # nobody makes it in time -> earliest arrival
    earliest = min(c.ankunft for c in conns if c.ankunft)
    best_batch = [c for c in conns if c.ankunft == earliest]
    return sorted(best_batch, key=_sort_key)[0]


def plan_group(
    origins,
    target,
    *,
    date,
    arrive_by,
    fare: FareParams,
    max_transfers=None,
    limit=5,
) -> GroupPlan:
    """Coordinated arrival: each origin -> shared ``target`` arriving by ``arrive_by``.

    Runs one arrival search per origin (reusing :func:`search_journeys`), picks the
    best connection per person, and aggregates a summary (earliest departure, latest
    arrival, summed price, worst transfer score). A failing origin is recorded on its
    leg rather than aborting the whole plan.
    """
    when_iso = f"{date}T{arrive_by}:00"
    legs: list[GroupLeg] = []
    for origin in origins:
        try:
            res = search_journeys(
                origin, target,
                when_iso=when_iso, fare=fare, arrival=True,
                max_transfers=max_transfers, limit=limit,
            )
            best = _pick_arrival(res.connections, when_iso)
            legs.append(GroupLeg(
                origin=origin, best=best,
                alternatives=[c for c in res.connections if c is not best][:3],
                notices=res.notices,
            ))
        except backend.BahnApiError as exc:
            legs.append(GroupLeg(origin=origin, error=str(exc)))
        except ValueError as exc:
            legs.append(GroupLeg(origin=origin, error=str(exc)))

    bests = [l.best for l in legs if l.best is not None]
    departures = [c.abfahrt for c in bests if c.abfahrt]
    arrivals = [c.ankunft for c in bests if c.ankunft]
    prices = [c.preis for c in bests if c.preis is not None]
    scores = [c.verbindungsscore for c in bests if c.verbindungsscore is not None]

    notices: list[str] = []
    missing = [l.origin for l in legs if l.best is None]
    if missing:
        notices.append("Keine Verbindung gefunden für: " + ", ".join(missing))
    if any(c.preis is None for c in bests):
        notices.append("Für mindestens eine Verbindung liegt kein Preis vor.")

    return GroupPlan(
        target=target, date=date, arrive_by=arrive_by, legs=legs,
        earliest_departure=min(departures) if departures else None,
        latest_arrival=max(arrivals) if arrivals else None,
        total_price=round(sum(prices), 2) if prices else None,
        worst_score=min(scores) if scores else None,
        notices=notices,
    )


def suggest_meeting_point(
    origins,
    *,
    date,
    arrive_by,
    fare: FareParams,
    max_transfers=None,
    hubs=None,
    top=3,
) -> MeetingResult:
    """Rank candidate meeting hubs by worst-case individual travel time.

    Candidates = ``hubs`` (default :data:`MEETING_HUBS`) plus the origins themselves
    (meeting at someone's home is often optimal). For each candidate, runs
    :func:`plan_group` and scores it by the longest single member's journey
    (tie-break: total). Returns the ``top`` best, reachable-by-everyone first.

    ponytail: O(N origins x M hubs) live searches, curated hubs cap M; parallelise
    or precompute a travel-time matrix if it gets slow.
    """
    candidates_names = list(dict.fromkeys(list(hubs or MEETING_HUBS) + list(origins)))
    candidates: list[MeetingCandidate] = []
    for hub in candidates_names:
        plan = plan_group(
            origins, hub, date=date, arrive_by=arrive_by,
            fare=fare, max_transfers=max_transfers,
        )
        durations = [
            l.best.dauer_minuten for l in plan.legs
            if l.best is not None and l.best.dauer_minuten is not None
        ]
        reachable = all(l.best is not None for l in plan.legs)
        candidates.append(MeetingCandidate(
            hub=hub, plan=plan,
            max_minutes=max(durations) if durations else None,
            total_minutes=sum(durations) if durations else None,
            reachable=reachable,
        ))

    def _rank(c: MeetingCandidate):
        # reachable-by-all first, then lowest worst-case, then lowest total.
        return (
            not c.reachable,
            c.max_minutes if c.max_minutes is not None else 10**9,
            c.total_minutes if c.total_minutes is not None else 10**9,
        )

    candidates.sort(key=_rank)
    return MeetingResult(candidates=candidates[:top])
