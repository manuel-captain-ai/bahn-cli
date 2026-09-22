"""Interrail-Rechner: lohnt ein Global Pass gegenüber Einzeltickets?

Warum kein API-Provider: Interrail hat keine öffentliche API und braucht auch keine.
Passpreise erscheinen einmal im Jahr, das ist eine Tabelle, kein Livedienst. Die
eigentliche Frage ("Pass oder Einzelfahrscheine?") ist Arithmetik.

Genau dort ist der Pass am stärksten, wo bahn.de ``preis: null`` liefert: rein
ausländische Relationen, die die DB nicht vertreibt (Paris->Lissabon, Wien->Zagreb).
Deshalb ist ein fehlender Preis hier ein Argument FÜR den Pass, kein fehlender Wert.

# ponytail: statische Preistabelle, keine API. Feld `stand` macht Veralten sichtbar.
# Upgrade-Pfad, wenn sie rottet: einmal pro Quartal interrail.com nachziehen.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA_FILE = Path(__file__).parent / "data" / "interrail.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def _rabatt(alter):
    """Passfaktor und Label für Youth/Senior. Ohne Alter: voller Erwachsenenpreis."""
    if alter is None:
        return 1.0, "Erwachsener"
    rabatte = _data()["rabatte"]
    youth, senior = rabatte["youth"], rabatte["senior"]
    if alter <= youth["bis_alter"]:
        return youth["faktor"], youth["label"]
    if alter >= senior["ab_alter"]:
        return senior["faktor"], senior["label"]
    return 1.0, "Erwachsener"


def _passt(pass_option, trips: int) -> bool:
    return pass_option["reisetage"] >= trips


def _guenstigster_pass(trips: int, alter, first_class: bool):
    """Billigster Pass, der ``trips`` Reisetage abdeckt."""
    faktor, label = _rabatt(alter)
    preis_key = "preis_1klasse" if first_class else "preis"
    passend = [p for p in _data()["global_pass"] if _passt(p, trips)]
    if not passend:
        passend = [_data()["global_pass"][-1]]   # längster Pass als Obergrenze
    beste = min(passend, key=lambda p: p[preis_key])
    return {
        "key": beste["key"],
        "label": beste["label"],
        "listenpreis": beste[preis_key],
        "preis": round(beste[preis_key] * faktor, 2),
        "tarif": label,
        "reisetage": beste["reisetage"],
    }


def reservierungsentgelt(produkte) -> tuple[float, list[str]]:
    """Summe der Reservierungsentgelte für die Produkte einer Verbindung.

    Ein Pass deckt die Fahrt, nicht die Reservierungspflicht. Wer die weglässt,
    rechnet den Pass systematisch zu billig, gerade auf TGV- und Nachtzugstrecken.
    """
    tabelle = _data()["reservierung"]["pro_produkt"]
    standard = _data()["reservierung"]["standard"]
    summe = 0.0
    posten = []
    for produkt in produkte or []:
        # produkte sind "TGV 9552", "ICE 371" - die Gattung ist das erste Token.
        gattung = str(produkt).split()[0].upper() if str(produkt).strip() else ""
        eintrag = tabelle.get(gattung)
        entgelt = eintrag["entgelt"] if eintrag else standard
        if entgelt:
            summe += entgelt
            posten.append(f"{gattung} {entgelt:.0f} EUR")
    return round(summe, 2), posten


def evaluate(connections, *, trips: int = 1, alter=None, first_class: bool = False) -> dict:
    """Vergleicht Pass gegen Einzeltickets.

    ``connections``: die verglichenen Verbindungen (Connection oder dict), üblicherweise
    je Relation die günstigste. ``trips``: Anzahl geplanter Reisetage - bei einer
    einzelnen Fahrt lohnt ein Pass fast nie, das ist die ehrliche Antwort.
    """
    rows = [c if isinstance(c, dict) else c.model_dump() for c in (connections or [])]

    einzelpreise = [r.get("preis") for r in rows if r.get("preis") is not None]
    ohne_preis = [r for r in rows if r.get("preis") is None]
    einzelsumme = round(sum(einzelpreise), 2) if einzelpreise else None

    reservierung = 0.0
    posten: list[str] = []
    for r in rows:
        entgelt, teil = reservierungsentgelt(r.get("produkte"))
        reservierung += entgelt
        posten.extend(teil)
    reservierung = round(reservierung, 2)

    pass_info = _guenstigster_pass(max(trips, 1), alter, first_class)
    pass_gesamt = round(pass_info["preis"] + reservierung, 2)

    if einzelsumme is None:
        verdikt = (
            "Fuer diese Strecke gibt es online keinen Einzelpreis - typisch fuer rein "
            "auslaendische Relationen. Ein Interrail-Pass ist hier oft die praktikable "
            "Option, ein belastbarer Vergleich ist ohne Einzelpreis aber nicht moeglich."
        )
        ersparnis = None
    elif ohne_preis:
        verdikt = (
            f"Nur {len(einzelpreise)} von {len(rows)} Verbindungen haben einen Online-Preis. "
            f"Teilvergleich: Einzeltickets {einzelsumme:.2f} EUR gegen Pass {pass_gesamt:.2f} EUR "
            "- die preislosen Abschnitte fehlen in der Rechnung."
        )
        ersparnis = round(einzelsumme - pass_gesamt, 2)
    else:
        ersparnis = round(einzelsumme - pass_gesamt, 2)
        if ersparnis > 0:
            verdikt = (
                f"Pass lohnt: {pass_gesamt:.2f} EUR gegen {einzelsumme:.2f} EUR Einzeltickets, "
                f"Ersparnis {ersparnis:.2f} EUR."
            )
        else:
            verdikt = (
                f"Einzeltickets sind guenstiger: {einzelsumme:.2f} EUR gegen {pass_gesamt:.2f} EUR "
                f"Pass, Differenz {abs(ersparnis):.2f} EUR."
            )

    return {
        "pass_typ": pass_info["label"],
        "pass_tarif": pass_info["tarif"],
        "pass_preis": pass_info["preis"],
        "reservierungen": reservierung,
        "reservierungs_posten": posten,
        "pass_gesamt": pass_gesamt,
        "einzeltickets": einzelsumme,
        "ohne_online_preis": len(ohne_preis),
        "ersparnis": ersparnis,
        "verdikt": verdikt,
        "stand": _data()["stand"],
    }
