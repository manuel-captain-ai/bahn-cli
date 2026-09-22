"""Tests für den Interrail-Rechner. Kein Netz: statische Preistabelle."""
from __future__ import annotations

import pytest

from bahn_de import interrail


def conn(preis=None, produkte=()):
    return {"preis": preis, "produkte": list(produkte)}


# --- Passauswahl ---------------------------------------------------------------

def test_waehlt_guenstigsten_passenden_pass():
    r = interrail.evaluate([conn(100)], trips=4)
    assert r["pass_typ"] == "4 Reisetage in 1 Monat"
    assert r["pass_preis"] == 283


def test_zu_kleiner_pass_wird_uebersprungen():
    """3 Reisetage passen in den 4-Tage-Pass, 6 nicht - dann muss der 7er kommen."""
    assert interrail.evaluate([conn(100)], trips=3)["pass_preis"] == 283
    assert interrail.evaluate([conn(100)], trips=6)["pass_preis"] == 381


def test_youth_rabatt():
    voll = interrail.evaluate([conn(100)], trips=4)["pass_preis"]
    jung = interrail.evaluate([conn(100)], trips=4, alter=24)
    assert jung["pass_preis"] == pytest.approx(voll * 0.75)
    assert "Youth" in jung["pass_tarif"]


def test_senior_rabatt():
    voll = interrail.evaluate([conn(100)], trips=4)["pass_preis"]
    alt = interrail.evaluate([conn(100)], trips=4, alter=67)
    assert alt["pass_preis"] == pytest.approx(voll * 0.90)
    assert "Senior" in alt["pass_tarif"]


def test_mittleres_alter_ohne_rabatt():
    voll = interrail.evaluate([conn(100)], trips=4)["pass_preis"]
    assert interrail.evaluate([conn(100)], trips=4, alter=40)["pass_preis"] == voll


def test_erste_klasse_ist_teurer():
    zweite = interrail.evaluate([conn(100)], trips=4)["pass_preis"]
    erste = interrail.evaluate([conn(100)], trips=4, first_class=True)["pass_preis"]
    assert erste > zweite


# --- Reservierungsentgelte ------------------------------------------------------

def test_reservierung_tgv_kostet():
    entgelt, posten = interrail.reservierungsentgelt(["TGV 9552"])
    assert entgelt == 20
    assert posten


def test_reservierung_ice_kostenlos():
    """Ein Pass gilt im ICE ohne Aufpreis - sonst rechnet der Pass sich zu schlecht."""
    assert interrail.reservierungsentgelt(["ICE 371"]) == (0, [])


def test_reservierung_summiert_ueber_abschnitte():
    entgelt, _ = interrail.reservierungsentgelt(["ICE 371", "TGV 9552", "FR 9325"])
    assert entgelt == 20 + 13


def test_unbekanntes_produkt_kostet_nichts():
    assert interrail.reservierungsentgelt(["XYZ 1"])[0] == 0


def test_leere_produkte():
    assert interrail.reservierungsentgelt([]) == (0, [])
    assert interrail.reservierungsentgelt(None) == (0, [])


def test_reservierung_fliesst_in_pass_gesamt():
    ohne = interrail.evaluate([conn(100, ["ICE 371"])], trips=4)
    mit = interrail.evaluate([conn(100, ["TGV 9552"])], trips=4)
    assert mit["pass_gesamt"] == ohne["pass_gesamt"] + 20


# --- Verdikt: der eigentliche Zweck --------------------------------------------

def test_verdikt_kippt_am_break_even():
    """Der Kern des Rechners: unterhalb der Passkosten gewinnen Einzeltickets, darüber der Pass."""
    pass_preis = interrail.evaluate([conn(1)], trips=4)["pass_preis"]   # 283, keine Reservierung

    knapp_darunter = interrail.evaluate(
        [conn(pass_preis - 10)], trips=4)
    knapp_darueber = interrail.evaluate(
        [conn(pass_preis + 10)], trips=4)

    assert knapp_darunter["ersparnis"] < 0
    assert "Einzeltickets sind guenstiger" in knapp_darunter["verdikt"]
    assert knapp_darueber["ersparnis"] > 0
    assert "Pass lohnt" in knapp_darueber["verdikt"]


def test_summiert_mehrere_einzelpreise():
    r = interrail.evaluate([conn(150), conn(150), conn(150)], trips=3)
    assert r["einzeltickets"] == 450
    assert r["ersparnis"] == round(450 - r["pass_gesamt"], 2)


def test_ohne_jeden_preis_kein_scheinvergleich():
    """Paris->Lissabon-Fall: bahn.de kennt keinen Preis.

    Ein fehlender Preis darf nicht als 0 EUR durchgehen, sonst gewinnen die
    Einzeltickets rechnerisch immer.
    """
    r = interrail.evaluate([conn(None), conn(None)], trips=2)
    assert r["einzeltickets"] is None
    assert r["ersparnis"] is None
    assert r["ohne_online_preis"] == 2
    assert "kein" in r["verdikt"].lower()


def test_teilweise_preise_werden_als_teilvergleich_markiert():
    r = interrail.evaluate([conn(100), conn(None)], trips=2)
    assert r["einzeltickets"] == 100
    assert r["ohne_online_preis"] == 1
    assert "Teilvergleich" in r["verdikt"]


def test_leere_eingabe_stuerzt_nicht_ab():
    r = interrail.evaluate([], trips=1)
    assert r["einzeltickets"] is None
    assert r["pass_preis"] > 0


def test_stand_wird_durchgereicht():
    """Ohne sichtbares Stand-Datum kann niemand einschaetzen, wie alt die Tabelle ist."""
    assert interrail.evaluate([conn(100)], trips=1)["stand"]
