"""bahn_de CLI - search Deutsche Bahn connections with prices.

Uses the unofficial bahn.de web JSON API (no auth). See BAHN_DE.md / README.md
for the disclaimer and details.
"""
from __future__ import annotations

import datetime
import functools
import json
import sys

import click

from . import backend
from . import formatters
from . import models
from . import service

_json_output = False


# --- Dual output -------------------------------------------------------------

def output(data, message: str = ""):
    if _json_output:
        click.echo(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        if message:
            click.echo(message)
        if isinstance(data, str):
            click.echo(data)
        elif isinstance(data, dict):
            _print_dict(data)
        else:
            click.echo(str(data))


def _print_dict(d: dict, indent: int = 0):
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            click.echo(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            click.echo(f"{prefix}{k}:")
            _print_list(v, indent + 1)
        else:
            click.echo(f"{prefix}{k}: {v}")


def _print_list(items: list, indent: int = 0):
    prefix = "  " * indent
    for i, item in enumerate(items):
        if isinstance(item, dict):
            click.echo(f"{prefix}[{i}]")
            _print_dict(item, indent + 1)
        else:
            click.echo(f"{prefix}- {item}")


# --- Error handling ----------------------------------------------------------

def handle_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (backend.BahnApiError, ValueError) as e:
            if _json_output:
                click.echo(json.dumps({"error": str(e), "type": type(e).__name__}))
            else:
                click.echo(f"Fehler: {e}", err=True)
            sys.exit(1)

    return wrapper


# --- Helpers -----------------------------------------------------------------

def _when_iso(date, time):
    """Combine --date (YYYY-MM-DD, default today) and --time (HH:MM, default now)."""
    now = datetime.datetime.now()
    d = date or now.strftime("%Y-%m-%d")
    t = time or now.strftime("%H:%M")
    return f"{d}T{t}:00"


def search_options(func):
    """Shared fare/journey options for `search` and `best`."""
    func = click.option("--bahncard", type=click.Choice(["25", "50"]), default=None,
                        help="BahnCard-Ermaessigung.")(func)
    func = click.option("--bahncard-class", type=click.Choice(["1", "2"]), default=None,
                        help="Klasse der BahnCard (nicht die Reiseklasse).")(func)
    func = click.option("--first-class/--no-first-class", "first_class", default=None,
                        help="In der 1. Klasse reisen.")(func)
    func = click.option("--deutschlandticket/--no-deutschlandticket", "deutschlandticket",
                        default=None, help="Preis mit Deutschlandticket.")(func)
    func = click.option("--d-ticket-only", is_flag=True, default=False,
                        help="Nur Deutschlandticket-faehige Verbindungen.")(func)
    func = click.option("--max-transfers", type=int, default=None, help="Maximale Anzahl Umstiege.")(func)
    func = click.option("--direct-only", is_flag=True, default=False,
                        help="Nur direkte Verbindungen (max. Umstiege 0).")(func)
    func = click.option("--min-transfer-time", type=int, default=None,
                        help="Mindest-Umstiegszeit in Minuten (serverseitig).")(func)
    func = click.option("--max-transfer-time", type=int, default=None,
                        help="Maximale Umstiegszeit in Minuten (clientseitiger Filter).")(func)
    func = click.option("--bike", is_flag=True, default=False, help="Fahrradmitnahme erforderlich.")(func)
    return func


# --- CLI group ---------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Ausgabe als JSON.")
@click.pass_context
def cli(ctx, use_json):
    """bahn_de - Bahnverbindungen mit Preisen suchen (inoffizielle API)."""
    global _json_output
    _json_output = use_json
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.argument("query")
@click.option("--limit", type=int, default=10, help="Maximale Anzahl Ergebnisse.")
@handle_error
def stations(query, limit):
    """Bahnhoefe/Orte suchen und ihre LID anzeigen."""
    raw = backend.search_stations(query, limit=limit)
    if _json_output:
        output(raw)
    else:
        output(formatters.format_stations(raw))


@cli.command()
@click.argument("from_station")
@click.argument("to_station")
@click.option("--date", default=None, help="Abfahrtsdatum YYYY-MM-DD (Standard: heute).")
@click.option("--time", default=None, help="Abfahrtszeit HH:MM (Standard: jetzt).")
@click.option("--arrival", is_flag=True, default=False, help="Datum/Zeit als Ankunftszeit behandeln.")
@click.option("--before", type=int, default=0, help="Auch Abfahrten bis N Minuten vor der Zeit anzeigen.")
@click.option("--after", type=int, default=0, help="Auch Abfahrten bis N Minuten nach der Zeit anzeigen.")
@click.option("--around", type=int, default=None, help="Kurzform: Zeitfenster von N Minuten vor und nach der Zeit.")
@click.option("--limit", type=int, default=10, help="Maximale Anzahl Verbindungen.")
@click.option("--via", "via_values", multiple=True, metavar="VIA",
              help=(
                  "Zwischenhalt fuer eine Reise mit laengerem Aufenthalt: 'Ort@YYYY-MM-DDTHH:MM' "
                  "(Weiterreise zu diesem Zeitpunkt) oder 'Ort:72h' / 'Ort:3d' (Mindest-Aufenthalt). "
                  "Bis zu 2 Mal angeben. Jeder Zwischenhalt erfordert eine Preisabfrage pro Verbindung (langsam)."
              ))
@click.option("--source", type=click.Choice(["auto", "bahn", "flix", "omio", "navitia"]), default="auto",
              help="Datenquelle: auto (bahn.de, Fallback Flix/navitia), bahn, flix, omio "
                   "(Fernbus/FlixTrain, echte Preise) oder navitia (pan-EU, ohne Preise).")
@search_options
@handle_error
def search(from_station, to_station, date, time, arrival, before, after, around, limit, via_values, source, **opts):
    """Verbindungen mit Preisen zwischen zwei Bahnhoefen suchen."""
    if around is not None:
        before = around if before == 0 else before
        after = around if after == 0 else after

    if len(via_values) > 2:
        raise ValueError("Maximal 2 Zwischenhalte erlaubt (--via kann 2 Mal angegeben werden).")

    when_iso = _when_iso(date, time)
    fare = service.resolve_fare(opts)

    result = service.search_journeys(
        from_station, to_station,
        when_iso=when_iso,
        fare=fare,
        arrival=arrival,
        max_transfers=(0 if opts.get("direct_only") else opts.get("max_transfers")),
        min_transfer_time=opts.get("min_transfer_time"),
        max_transfer_time=opts.get("max_transfer_time"),
        bike=opts.get("bike", False),
        dticket_only=opts.get("d_ticket_only", False),
        via_values=via_values,
        before=before,
        after=after,
        limit=limit,
        source=source,
    )

    if not _json_output:
        for notice in result.notices:
            click.echo(notice, err=True)

    if _json_output:
        output([c.model_dump(mode="json") for c in result.connections])
    else:
        output(formatters.format_connections([c.model_dump() for c in result.connections]))


@cli.command()
@click.argument("from_station")
@click.argument("to_station")
@click.option("--date", required=True, help="Zu durchsuchender Tag YYYY-MM-DD.")
@click.option("--top", type=int, default=5, help="Anzahl der guenstigsten Preise.")
@search_options
@handle_error
def best(from_station, to_station, date, top, **opts):
    """Die guenstigsten Preise eines ganzen Tages anzeigen."""
    fare = service.resolve_fare(opts)

    result = service.day_best(
        from_station, to_station,
        date=date,
        fare=fare,
        max_transfers=(0 if opts.get("direct_only") else opts.get("max_transfers")),
        min_transfer_time=opts.get("min_transfer_time"),
        max_transfer_time=opts.get("max_transfer_time"),
        bike=opts.get("bike", False),
        dticket_only=opts.get("d_ticket_only", False),
        top=top,
    )

    if not _json_output:
        for notice in result.notices:
            click.echo(notice, err=True)

    if _json_output:
        output({
            "source": result.source,
            "connections": [c.model_dump(mode="json") for c in result.connections],
        })
    else:
        output(formatters.format_best([c.model_dump() for c in result.connections], result.source, top=top))


@cli.command()
@click.argument("from_station")
@click.argument("to_station")
@click.option("--date", default=None, help="Abfahrtsdatum YYYY-MM-DD (Standard: heute).")
@click.option("--time", default=None, help="Abfahrtszeit HH:MM (Standard: jetzt).")
@click.option("--limit", type=int, default=5, help="Verbindungen je Quelle.")
@click.option("--interrail-trips", type=int, default=1,
              help="Geplante Reisetage insgesamt - Basis fuer den Interrail-Vergleich.")
@click.option("--age", "alter", type=int, default=None,
              help="Alter fuer Interrail Youth-/Senior-Rabatt.")
@search_options
@handle_error
def cheapest(from_station, to_station, date, time, limit, interrail_trips, alter, **opts):
    """Guenstigste Option ueber alle Quellen: Bahn, Fernbus, Interrail-Pass."""
    when_iso = _when_iso(date, time)
    fare = service.resolve_fare(opts)

    result = service.search_cheapest(
        from_station, to_station,
        when_iso=when_iso,
        fare=fare,
        limit=limit,
        interrail_trips=interrail_trips,
        alter=alter,
    )

    if _json_output:
        output({
            "connections": result.connections,
            "interrail": result.interrail,
            "notices": result.notices,
        })
    else:
        output(formatters.format_cheapest(result))


def _fmt_group_plan(plan) -> str:
    """Render a GroupPlan as a compact human table."""
    lines = [f"Gruppe → {plan.target}  (an bis {plan.arrive_by}, {plan.date})", ""]
    for leg in plan.legs:
        if leg.error or leg.best is None:
            lines.append(f"  {leg.origin:<22} — keine Verbindung ({leg.error or 'n/a'})")
            continue
        c = leg.best
        dep = (c.abfahrt or "")[11:16]
        arr = (c.ankunft or "")[11:16]
        um = "direkt" if c.umstiege == 0 else f"{c.umstiege}x"
        preis = f"{c.preis:.2f}€" if c.preis is not None else "—"
        lines.append(f"  {leg.origin:<22} ab {dep} → an {arr}  {um:>6}  {preis}")
    lines.append("")
    lines.append(
        f"  Zusammenfassung: früheste Abfahrt {plan.earliest_departure or '?'}, "
        f"späteste Ankunft {plan.latest_arrival or '?'}, "
        f"Gesamtpreis {f'{plan.total_price:.2f}€' if plan.total_price is not None else '—'}"
    )
    for n in plan.notices:
        lines.append(f"  ! {n}")
    return "\n".join(lines)


@cli.command()
@click.option("--origin", "origins", multiple=True, required=True,
              help="Startort eines Gruppenmitglieds (mehrfach angeben).")
@click.option("--to", "target", default=None, help="Gemeinsames Ziel. Leer = Treffpunkt vorschlagen.")
@click.option("--date", required=True, help="Reisetag YYYY-MM-DD.")
@click.option("--arrive-by", default="12:00", help="Späteste gemeinsame Ankunftszeit HH:MM.")
@click.option("--meeting", is_flag=True, help="Treffpunkt vorschlagen (auch mit --to als 'both').")
@click.option("--top", type=int, default=3, help="Anzahl Treffpunkt-Vorschläge.")
@search_options
@handle_error
def group(origins, target, date, arrive_by, meeting, top, **opts):
    """Gruppenreise planen: mehrere Startorte → gemeinsames Ziel oder Treffpunkt."""
    fare = service.resolve_fare(opts)
    max_transfers = 0 if opts.get("direct_only") else opts.get("max_transfers")
    origins = list(origins)

    want_dest = target is not None
    want_meet = meeting or target is None

    result: dict = {}
    if want_dest:
        plan = service.plan_group(origins, target, date=date, arrive_by=arrive_by,
                                  fare=fare, max_transfers=max_transfers)
        result["plan"] = plan
    if want_meet:
        result["meeting"] = service.suggest_meeting_point(
            origins, date=date, arrive_by=arrive_by, fare=fare,
            max_transfers=max_transfers, top=top)

    if _json_output:
        output({k: v.model_dump(mode="json") for k, v in result.items()})
        return

    if "plan" in result:
        click.echo(_fmt_group_plan(result["plan"]))
    if "meeting" in result:
        click.echo("\nTreffpunkt-Vorschläge (bester zuerst):")
        for cand in result["meeting"].candidates:
            mx = f"{cand.max_minutes}min" if cand.max_minutes is not None else "?"
            reach = "" if cand.reachable else "  (nicht für alle erreichbar)"
            click.echo(f"\n== {cand.hub}  · längste Einzelfahrt {mx}{reach} ==")
            click.echo(_fmt_group_plan(cand.plan))


_SCHEMA_MODELS = {"connection": models.Connection, "station": models.Station}


@cli.command()
@click.option("--model", "model_name", type=click.Choice(sorted(_SCHEMA_MODELS)), default="connection",
              help="Welches Modell als JSON-Schema ausgegeben werden soll.")
@handle_error
def schema(model_name):
    """JSON-Schema eines Modells ausgeben (z.B. als Agenten-Tooldefinition)."""
    output(_SCHEMA_MODELS[model_name].model_json_schema())


# --- Config ------------------------------------------------------------------

@cli.group()
def config():
    """Standard-Tarifparameter verwalten."""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
@handle_error
def config_set(key, value):
    """Einen Standardwert setzen. 'none' loescht ihn. Keys: bahncard, bahncard_class, first_class, deutschlandticket."""
    if key not in backend.ALLOWED_CONFIG_KEYS:
        raise ValueError(
            f"Unbekannter Config-Key '{key}'. Erlaubt: {', '.join(sorted(backend.ALLOWED_CONFIG_KEYS))}."
        )
    cfg = backend.load_config()
    if value == "none":
        cfg.pop(key, None)
        backend.save_config(cfg)
        output({"deleted": key}, f"✓ {key} entfernt")
        return
    if value not in backend.ALLOWED_CONFIG_KEYS[key]:
        allowed = sorted(backend.ALLOWED_CONFIG_KEYS[key] | {"none"})
        raise ValueError(f"Ungueltiger Wert '{value}' fuer '{key}'. Erlaubt: {', '.join(allowed)}.")
    if key in ("first_class", "deutschlandticket"):
        cfg[key] = (value == "true")
    else:
        cfg[key] = value
    backend.save_config(cfg)
    output({key: cfg[key]}, f"✓ {key} = {cfg[key]} gesetzt")


@config.command("get")
@click.argument("key", required=False)
@handle_error
def config_get(key):
    """Einen oder alle Config-Werte anzeigen."""
    cfg = backend.load_config()
    if key:
        output({key: cfg.get(key)})
    else:
        output(cfg if cfg else {}, "Config" if cfg else "Config ist leer.")


@config.command("path")
@handle_error
def config_path():
    """Pfad der Config-Datei anzeigen."""
    output({"path": str(backend.CONFIG_FILE)}, str(backend.CONFIG_FILE))


def main():
    cli()


if __name__ == "__main__":
    main()
