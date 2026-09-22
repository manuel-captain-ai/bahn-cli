"""Pure formatting / parsing for bahn.de responses. No HTTP here.

Field names are verified against live responses (2026-06-12):
  - connection.tripId, .umstiegsAnzahl, .verbindungsDauerInSeconds
  - connection.angebotsPreis = {betrag, waehrung}     (from /fahrplan)
  - connection.abPreis        = {betrag, waehrung}     (from /tagesbestpreis)
  - abschnitt.abfahrt.sollzeit / .ankunft.sollzeit
  - abschnitt.abfahrtsOrt / .ankunftsOrt               (plain strings)
  - abschnitt.abschnittsDauer (seconds)
  - abschnitt.verkehrsmittel = {name, produktGattung, typ}
"""
from __future__ import annotations

from datetime import datetime


def _short_time(iso):
    """Return HH:MM from an ISO timestamp, or '?' if missing."""
    if not iso or "T" not in iso:
        return "?"
    return iso.split("T", 1)[1][:5]


def _parse_iso(iso):
    """Parse an ISO timestamp to a datetime, or None if unparseable."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _price_obj(raw):
    """Return the price dict from a connection, regardless of source key."""
    return raw.get("angebotsPreis") or raw.get("abPreis") or None


def _parse_section(sec):
    """Parse a single journey section (leg) into a flat dict."""
    vm = sec.get("verkehrsmittel") or {}
    dauer_s = sec.get("abschnittsDauer")
    return {
        "abfahrt": (sec.get("abfahrt") or {}).get("sollzeit"),
        "abfahrt_ort": sec.get("abfahrtsOrt"),
        "ankunft": (sec.get("ankunft") or {}).get("sollzeit"),
        "ankunft_ort": sec.get("ankunftsOrt"),
        "dauer_minuten": (dauer_s // 60) if isinstance(dauer_s, int) else None,
        # `mittelText` ist die menschenlesbare Linie ("RE4", "ICE 847"); `name`
        # liefert bei manchen NX-Regionallinien nur die nackte Laufnummer ("26412").
        # Bei ICE/IC/S-Bahn sind beide identisch, daher ist der Fallback sicher.
        "produkt": vm.get("mittelText") or vm.get("name"),
        "typ": vm.get("typ"),
    }


def _layover_minutes(public_legs):
    """Layover minutes at each transfer, between consecutive public-transport legs."""
    layovers = []
    for prev, nxt in zip(public_legs, public_legs[1:]):
        arr = _parse_iso(prev.get("ankunft"))
        dep = _parse_iso(nxt.get("abfahrt"))
        if arr and dep:
            layovers.append(int((dep - arr).total_seconds() // 60))
        else:
            layovers.append(None)
    return layovers


_VIA_GAP_THRESHOLD_MINUTES = 180  # gaps >= 3h between public legs indicate a via stay


def _via_halte(public_legs):
    """Detect via stops: gaps >= 3h between consecutive public-transport legs.

    Returns a list of dicts with the ort, ankunft, abfahrt, and
    aufenthalt_minuten for each detected stay.
    """
    halte = []
    for prev, nxt in zip(public_legs, public_legs[1:]):
        arr = _parse_iso(prev.get("ankunft"))
        dep = _parse_iso(nxt.get("abfahrt"))
        if arr and dep:
            gap = int((dep - arr).total_seconds() // 60)
            if gap >= _VIA_GAP_THRESHOLD_MINUTES:
                halte.append({
                    "ort": prev.get("ankunft_ort"),
                    "ankunft": prev.get("ankunft"),
                    "abfahrt": nxt.get("abfahrt"),
                    "aufenthalt_minuten": gap,
                })
    return halte


def parse_connection(raw):
    """Parse a raw connection dict into a stable, flat schema.

    Defensive: every access uses .get; a missing price yields preis=None.
    Footpaths (sections without a public-transport verkehrsmittel) are filtered
    out of the product list but still count for the journey endpoints.

    ``abschnitte`` lists every leg (incl. footpaths) with its own times.
    ``umstiegszeiten_minuten`` holds the layover at each transfer, computed
    between consecutive public-transport legs.
    ``via_halte`` lists detected overnight/long stays (gap >= 3h between legs).
    ``teilpreise`` holds per-segment prices injected by the CLI after a recon
    call (present only for via-connections enriched with recon_price).
    """
    sections = raw.get("verbindungsAbschnitte", []) or []
    first = sections[0] if sections else {}
    last = sections[-1] if sections else {}

    legs = [_parse_section(sec) for sec in sections]
    public_legs = [leg for leg in legs if leg["typ"] == "PUBLICTRANSPORT"]
    produkte = [leg["produkt"] for leg in public_legs if leg["produkt"]]
    umstiegszeiten = _layover_minutes(public_legs)

    price = _price_obj(raw)
    dauer_s = raw.get("verbindungsDauerInSeconds")

    return {
        "trip_id": raw.get("tripId"),
        "abfahrt": (first.get("abfahrt") or {}).get("sollzeit"),
        "abfahrt_ort": first.get("abfahrtsOrt"),
        "ankunft": (last.get("ankunft") or {}).get("sollzeit"),
        "ankunft_ort": last.get("ankunftsOrt"),
        "dauer_minuten": (dauer_s // 60) if isinstance(dauer_s, int) else None,
        "umstiege": raw.get("umstiegsAnzahl"),
        "umstiegszeiten_minuten": umstiegszeiten,
        "produkte": produkte,
        "abschnitte": legs,
        "preis": price.get("betrag") if price else None,
        "waehrung": price.get("waehrung") if price else None,
        "via_halte": _via_halte(public_legs),
        "has_teilpreis": bool(raw.get("_has_teilpreis")),
        "teilpreise": list(raw.get("_teilpreise") or []),
        "ctx_recon": raw.get("ctxRecon"),
        "alterseingabe_erforderlich": bool(raw.get("isAlterseingabeErforderlich")),
        # Raw sections kept for predictor enrichment (halte[]/routeIdx/adminID).
        # Stripped by Connection.model_validate (extra="ignore").
        "_raw_abschnitte": sections,
    }


def filter_by_transfer_time(connections, *, min_minutes=None, max_minutes=None):
    """Keep connections whose every layover is within [min, max] minutes.

    Pure helper. Direct connections (no layovers) always pass. A layover that
    could not be computed (None) is treated as passing, to avoid dropping
    connections on incomplete data.
    """
    out = []
    for c in connections:
        layovers = [m for m in (c.get("umstiegszeiten_minuten") or []) if m is not None]
        if min_minutes is not None and any(m < min_minutes for m in layovers):
            continue
        if max_minutes is not None and any(m > max_minutes for m in layovers):
            continue
        out.append(c)
    return out


def _fmt_price(c):
    if c.get("preis") is None:
        return "-"
    price_str = f"{c['preis']:.2f} {c.get('waehrung') or 'EUR'}"
    if c.get("has_teilpreis"):
        price_str += " (Teilstreckenpreis)"
    return price_str


def _fmt_dauer(minutes):
    if minutes is None:
        return "?"
    return f"{minutes // 60}h{minutes % 60:02d}"


def format_stations(stations, *, full_lid=False):
    """Human table: Name / extId / LID (LID shortened unless full_lid)."""
    if not stations:
        return "Keine Bahnhöfe gefunden."
    lines = [f"{'Name':<32} {'extId':<10} LID"]
    lines.append("-" * 60)
    for s in stations:
        name = (s.get("name") or "")[:31]
        ext = str(s.get("extId") or "")
        lid = s.get("id") or ""
        if not full_lid and len(lid) > 30:
            lid = lid[:30] + "…"
        lines.append(f"{name:<32} {ext:<10} {lid}")
    return "\n".join(lines)


def _fmt_umstiege(c):
    """Transfer count with layover minutes, e.g. '1 (13m)' or '2 (13/24m)'."""
    n = c.get("umstiege")
    if n is None:
        return "?"
    layovers = [m for m in (c.get("umstiegszeiten_minuten") or []) if m is not None]
    if not layovers:
        return str(n)
    return f"{n} ({'/'.join(str(m) for m in layovers)}m)"


def _fmt_via_halt(halt):
    """One-line summary of a detected via stop."""
    ort = halt.get("ort") or "?"
    dep = halt.get("abfahrt")
    minuten = halt.get("aufenthalt_minuten")
    weiter = _short_time(dep)
    if dep and "T" in dep:
        tag = dep.split("T")[0][5:]  # MM-DD
        weiter = f"{tag} {weiter}"
    if minuten is not None:
        h = minuten // 60
        m = minuten % 60
        dauer_str = f"{h}h{m:02d}" if m else f"{h}h"
    else:
        dauer_str = "?"
    return f"  Halt: {ort}, weiter {weiter} (Aufenthalt {dauer_str})"


def format_connections(connections):
    """Human table: Ab / An / Dauer / Umstiege / Produkte / Preis."""
    if not connections:
        return "Keine Verbindungen gefunden."
    header = f"{'Ab':<6} {'An':<6} {'Dauer':<7} {'Umst':<10} {'Produkte':<24} Preis"
    lines = [header, "-" * 75]
    for c in connections:
        ab = _short_time(c.get("abfahrt"))
        an = _short_time(c.get("ankunft"))
        dauer = _fmt_dauer(c.get("dauer_minuten"))
        umst = _fmt_umstiege(c)
        prod = ", ".join(c.get("produkte") or [])[:23]
        lines.append(f"{ab:<6} {an:<6} {dauer:<7} {umst:<10} {prod:<24} {_fmt_price(c)}")
        if c.get("has_teilpreis"):
            lines.append("  ! Achtung: Nur Teilstreckenpreis (z.B. Eurostar-Anteil). Inlandssegmente benoetigen separate Tickets.")
        for halt in (c.get("via_halte") or []):
            lines.append(_fmt_via_halt(halt))
    return "\n".join(lines)


def format_best(connections, source, *, top=5):
    """Human table sorted by price (None to the end), with the source noted."""
    if not connections:
        return f"Keine Preise gefunden (Quelle: {source})."

    def sort_key(c):
        p = c.get("preis")
        return (p is None, p if p is not None else 0)

    ordered = sorted(connections, key=sort_key)[:top]
    body = format_connections(ordered)
    return f"{body}\n\nQuelle: {source}"


def format_cheapest(result) -> str:
    """Quellenuebergreifende Preisliste plus Interrail-Verdikt als Textblock.

    Jede Zeile nennt ihre Quelle, sonst liest sich ein 26-EUR-Fernbus mit 7 Stunden
    wie ein gleichwertiges Angebot zu einem 109-EUR-Zug mit 4 Stunden.
    """
    rows = result.connections
    if not rows:
        return "Keine Verbindung gefunden."

    lines = ["Preisvergleich (guenstigste zuerst)", ""]
    for row in rows:
        dep = (row.get("abfahrt") or "")[11:16]
        arr = (row.get("ankunft") or "")[11:16]
        dauer = row.get("dauer_minuten")
        dauer_txt = f"{dauer // 60}:{dauer % 60:02d}h" if dauer else "  ?  "
        umstiege = row.get("umstiege")
        um = "direkt" if umstiege == 0 else (f"{umstiege}x" if umstiege is not None else "?")
        preis = f"{row['preis']:>7.2f} EUR" if row.get("preis") is not None else "      —    "
        lines.append(
            f"  {preis}  {dep}->{arr}  {dauer_txt:>7}  {um:>6}  "
            f"[{row.get('quelle', '?')}]  {', '.join(row.get('produkte') or [])[:40]}"
        )

    pass_info = result.interrail
    lines += ["", "Interrail-Check", f"  {pass_info['verdikt']}"]
    lines.append(
        f"  Pass: {pass_info['pass_typ']} ({pass_info['pass_tarif']}) "
        f"{pass_info['pass_preis']:.2f} EUR"
        + (f" + {pass_info['reservierungen']:.2f} EUR Reservierungen"
           if pass_info["reservierungen"] else "")
        + f" = {pass_info['pass_gesamt']:.2f} EUR"
    )
    lines.append(f"  Passpreise Stand {pass_info['stand']}.")

    if result.notices:
        lines += ["", "Hinweise"]
        lines += [f"  - {n}" for n in result.notices]

    return "\n".join(lines)
