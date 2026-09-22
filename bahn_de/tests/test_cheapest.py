"""Tests für search_cheapest: Merge, Sortierung, Ausfalltoleranz. Kein Netz."""
from __future__ import annotations

import pytest

from bahn_de import service
from bahn_de.models import Connection, SearchResult


def conn(preis, dep="2026-08-20T08:00:00", produkte=("ICE 1",)):
    return Connection(
        abfahrt=dep, ankunft="2026-08-20T12:00:00",
        abfahrt_ort="A", ankunft_ort="B",
        dauer_minuten=240, umstiege=0,
        produkte=list(produkte), preis=preis,
        waehrung="EUR" if preis is not None else None,
    )


@pytest.fixture
def sources(monkeypatch):
    """Setzt alle Quellen; eine Exception als Wert bedeutet 'Quelle faellt aus'.

    ``omio`` ist optional, damit die bestehenden Faelle zweistellig lesbar bleiben;
    ohne Angabe liefert Omio nichts und faellt aus der Betrachtung.
    """
    def _apply(bahn, flix, omio=()):
        def _stub(value):
            def _call(*a, **k):
                if isinstance(value, Exception):
                    raise value
                return SearchResult(connections=list(value), notices=[])
            return _call

        monkeypatch.setattr(service, "search_journeys", _stub(bahn))
        monkeypatch.setattr(service._flix, "search_journeys_flix", _stub(flix))
        monkeypatch.setattr(service._omio, "search_journeys_omio", _stub(omio))
    return _apply


def _fare():
    return service.resolve_fare({})


def run(**kw):
    return service.search_cheapest(
        "A", "B", when_iso="2026-08-20T08:00:00", fare=_fare(), **kw)


# --- Sortierung ----------------------------------------------------------------

def test_preissortierung_ueber_quellen(sources):
    sources(bahn=[conn(100), conn(50)], flix=[conn(30), conn(80)])
    result = run()
    preise = [r["preis"] for r in result.connections]
    assert preise == sorted(preise)
    assert preise[0] == 30


def test_ohne_preis_ans_ende(sources):
    """preis=None heisst 'nicht online verkaeuflich', nicht 'gratis'."""
    sources(bahn=[conn(None), conn(200)], flix=[conn(40)])
    result = run()
    preise = [r["preis"] for r in result.connections]
    assert preise[-1] is None
    assert preise[0] == 40


def test_quelle_wird_markiert(sources):
    sources(bahn=[conn(100)], flix=[conn(30)])
    result = run()
    quellen = {r["quelle"] for r in result.connections}
    assert quellen == {"bahn.de", "Flixbus"}


def test_price_sort_key_akzeptiert_dict_und_model():
    assert service.price_sort_key({"preis": None}) > service.price_sort_key({"preis": 999})
    assert service.price_sort_key(conn(None)) > service.price_sort_key(conn(999))


# --- Ausfalltoleranz ------------------------------------------------------------

def test_bahn_faellt_aus_flix_bleibt(sources):
    sources(bahn=RuntimeError("403 Akamai"), flix=[conn(30)])
    result = run()
    assert [r["preis"] for r in result.connections] == [30]
    assert any("bahn.de nicht erreichbar" in n for n in result.notices)


def test_flix_faellt_aus_bahn_bleibt(sources):
    sources(bahn=[conn(100)], flix=RuntimeError("timeout"))
    result = run()
    assert [r["preis"] for r in result.connections] == [100]
    assert any("Flixbus nicht erreichbar" in n for n in result.notices)


def test_beide_quellen_aus_wirft_nicht(sources):
    """Eine leere Antwort mit Notizen ist brauchbarer als ein Stacktrace."""
    sources(bahn=RuntimeError("x"), flix=RuntimeError("y"), omio=RuntimeError("z"))
    result = run()
    assert result.connections == []
    assert len([n for n in result.notices if "nicht erreichbar" in n]) == 3


def test_omio_liefert_auslaendischen_bahnpreis(sources):
    """Der Fall, fuer den Omio ueberhaupt angebunden wurde: bahn.de kennt keinen
    Preis, Flix faehrt nur Bus, und die Bahnverbindung kommt von Omio."""
    sources(bahn=[conn(None)], flix=[conn(80, produkte=("BUS",))],
            omio=[conn(31.75, produkte=("TRAIN",))])
    result = run()
    assert result.connections[0]["preis"] == 31.75
    assert result.connections[0]["quelle"] == "Omio"


# --- Interrail-Kopplung ---------------------------------------------------------

def test_interrail_verdikt_immer_dabei(sources):
    sources(bahn=[conn(100)], flix=[conn(30)])
    assert run().interrail["verdikt"]


def test_quelle_ohne_preis_geht_in_den_vergleich_ein(sources):
    """Regression: Paris->Lissabon.

    Hat die Bahn keinen Preis und der Bus schon, darf der Check nicht
    "Einzeltickets guenstiger" melden, sondern muss den Teilvergleich kennzeichnen.
    """
    sources(bahn=[conn(None)], flix=[conn(65)])
    result = run()
    assert result.interrail["ohne_online_preis"] == 1
    assert "Teilvergleich" in result.interrail["verdikt"]


def test_reisetage_steuern_die_passwahl(sources):
    sources(bahn=[conn(100)], flix=[])
    assert run(interrail_trips=1).interrail["pass_preis"] == 283
    assert run(interrail_trips=7).interrail["pass_preis"] == 381


def test_alter_wird_durchgereicht(sources):
    sources(bahn=[conn(100)], flix=[])
    voll = run().interrail["pass_preis"]
    assert run(alter=22).interrail["pass_preis"] < voll


# --- Ehrlichkeit ----------------------------------------------------------------

def test_abdeckungshinweis_immer_vorhanden(sources):
    """Ohne Trainline-Partnervertrag ist der EU-Bahnvertrieb nicht vollstaendig.

    Die Ausgabe darf keine Vollstaendigkeit suggerieren.
    """
    sources(bahn=[conn(100)], flix=[conn(30)])
    assert any("Abdeckung" in n for n in run().notices)


def test_hinweis_auf_fehlende_preise(sources):
    sources(bahn=[conn(None), conn(None)], flix=[conn(30)])
    assert any("ohne Online-Preis" in n for n in run().notices)
