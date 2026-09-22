"""pydantic v2 models for bahn_de's stable output schema.

These models formalize the dict shape that ``formatters.parse_connection``
and ``backend.resolve_station`` have produced since 0.3.0. Field names are
deutsch and part of the ``--json`` contract used by the ``/bahn`` skill and
other agent consumers - they must not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, ConfigDict


class Teilpreis(BaseModel):
    """One segment price, as injected by recon enrichment for via-connections."""

    model_config = ConfigDict(extra="ignore")

    betrag: float
    waehrung: Optional[str] = None


class ViaHalt(BaseModel):
    """A detected via stop (gap >= 3h between two public-transport legs)."""

    model_config = ConfigDict(extra="ignore")

    ort: Optional[str] = None
    ankunft: Optional[str] = None
    abfahrt: Optional[str] = None
    aufenthalt_minuten: Optional[int] = None


class Section(BaseModel):
    """One leg of a journey (a "Abschnitt"), including footpaths."""

    model_config = ConfigDict(extra="ignore")

    abfahrt: Optional[str] = None
    abfahrt_ort: Optional[str] = None
    ankunft: Optional[str] = None
    ankunft_ort: Optional[str] = None
    dauer_minuten: Optional[int] = None
    produkt: Optional[str] = None
    typ: Optional[str] = None


class Connection(BaseModel):
    """A single journey (Verbindung) with price and leg details."""

    model_config = ConfigDict(extra="ignore")

    trip_id: Optional[str] = None
    abfahrt: Optional[str] = None
    abfahrt_ort: Optional[str] = None
    ankunft: Optional[str] = None
    ankunft_ort: Optional[str] = None
    dauer_minuten: Optional[int] = None
    umstiege: Optional[int] = None
    umstiegszeiten_minuten: list[Optional[int]] = []
    produkte: list[str] = []
    abschnitte: list[Section] = []
    preis: Optional[float] = None
    waehrung: Optional[str] = None
    via_halte: list[ViaHalt] = []
    has_teilpreis: bool = False
    teilpreise: list[Teilpreis] = []
    # Reconstruction token for this exact connection. Feeds the vbid share link
    # (/buchung/start?vbid=...) that lands directly on the bahn.de offer page.
    ctx_recon: Optional[str] = None
    # True for connections (typically international) where bahn.de requires the
    # traveller's age before showing bookable offers.
    alterseingabe_erforderlich: bool = False
    # Reliability scores from bahnvorhersage.de (None when predictor unavailable).
    # verbindungsscore: minimum transfer probability across all changes (0–1).
    # puenktlichkeit: reserved for future punctuality signal.
    verbindungsscore: Optional[float] = None
    puenktlichkeit: Optional[float] = None


class Station(BaseModel):
    """A resolved station/place with its LID and alternative matches."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    name: Optional[str] = None
    extId: Optional[str] = None
    alternatives: list[str] = []


class FareParams(BaseModel):
    """Resolved fare parameters (CLI flags > config.json > defaults)."""

    model_config = ConfigDict(extra="ignore")

    bahncard: Optional[str] = None
    bahncard_class: int = 2
    first_class: bool = False
    deutschlandticket: bool = False


# ── Group trips ─────────────────────────────────────────────────────────────────

class GroupLeg(BaseModel):
    """One group member's journey from their origin to the shared target."""

    model_config = ConfigDict(extra="ignore")

    origin: str
    best: Optional[Connection] = None
    alternatives: list[Connection] = []
    notices: list[str] = []
    error: Optional[str] = None


class GroupPlan(BaseModel):
    """Everyone travelling from their origin to one shared target (arrival-coordinated)."""

    model_config = ConfigDict(extra="ignore")

    target: str
    date: str
    arrive_by: str
    legs: list[GroupLeg] = []
    earliest_departure: Optional[str] = None
    latest_arrival: Optional[str] = None
    total_price: Optional[float] = None
    worst_score: Optional[float] = None
    notices: list[str] = []


class MeetingCandidate(BaseModel):
    """A candidate meeting hub, with the coordinated plan to reach it and its ranking."""

    model_config = ConfigDict(extra="ignore")

    hub: str
    plan: GroupPlan
    max_minutes: Optional[int] = None   # worst individual travel time (the ranking key)
    total_minutes: Optional[int] = None
    reachable: bool = True


class MeetingResult(BaseModel):
    """Ranked meeting-hub suggestions, best (lowest worst-case travel time) first."""

    model_config = ConfigDict(extra="ignore")

    candidates: list[MeetingCandidate] = []
    notices: list[str] = []


# --- Provider return type -----------------------------------------------------

@dataclass
class SearchResult:
    """What every journey provider returns: connections plus human-readable notices.

    Lives here rather than in service.py so provider modules (navitia, flix, omio)
    can import it without importing service, which imports them back.
    """

    connections: list[Connection]
    notices: list[str] = field(default_factory=list)
