"""Tests für die impersonate-Rotation bei HTTP 403 (Akamai). Kein Netz: Session gemockt.

Hintergrund: Akamai sortiert Fingerprints regelmäßig aus (chrome136 starb 2026-07-29).
Ein einzelnes totes Target darf das Tool nicht lahmlegen.
"""
from __future__ import annotations

import pytest

from bahn_de import backend


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = "blocked" if status_code != 200 else "ok"

    def json(self):
        return self._payload


class _FakeSession:
    """Antwortet 200 nur für Targets in ``good``, sonst 403."""

    def __init__(self, good):
        self.good = set(good)
        self.tried = []

    def request(self, method, url, *, params=None, json=None, headers=None,
                impersonate=None, timeout=None):
        self.tried.append(impersonate)
        if impersonate in self.good:
            return _FakeResponse(200, {"ok": True})
        return _FakeResponse(403)


@pytest.fixture
def patched(monkeypatch):
    """Frischer Rotationszustand pro Test, damit die Tests sich nicht beeinflussen."""
    def _apply(good, targets=("chrome124", "safari180", "firefox135")):
        session = _FakeSession(good)
        monkeypatch.setattr(backend, "_get_session", lambda: session)
        monkeypatch.setattr(backend, "_IMPERSONATE_FALLBACKS", targets)
        monkeypatch.setattr(backend, "_ROTATION", targets[1:])
        monkeypatch.setattr(backend, "_active_impersonate", targets[0])
        monkeypatch.setattr(backend, "MAX_RETRIES", 0)
        return session
    return _apply


def test_erstes_target_gut_keine_rotation(patched):
    session = patched(good={"chrome124"})
    assert backend._request("GET", "https://x") == {"ok": True}
    assert session.tried == ["chrome124"]


def test_totes_target_rotiert_weiter(patched):
    """chrome136-Szenario: erstes Target tot, zweites lebt."""
    session = patched(good={"safari180"})
    assert backend._request("GET", "https://x") == {"ok": True}
    assert session.tried == ["chrome124", "safari180"]


def test_rotation_bleibt_beim_guten_target(patched):
    """Kosten eines toten Fingerprints fallen einmal pro Prozess an, nicht pro Request."""
    session = patched(good={"firefox135"})
    backend._request("GET", "https://x")
    session.tried.clear()
    backend._request("GET", "https://x")
    assert session.tried == ["firefox135"]


def test_alle_targets_tot_wirft_mit_klarer_meldung(patched):
    session = patched(good=set())
    with pytest.raises(backend.BahnApiError) as exc:
        backend._request("GET", "https://x")
    assert exc.value.status_code == 403
    assert session.tried == ["chrome124", "safari180", "firefox135"]
    # Die Meldung muss beide Ursachen nennen, sonst diagnostiziert die nächste
    # Session wieder von vorn.
    assert "Residential-Proxy" in str(exc.value)
    assert "_IMPERSONATE_FALLBACKS" in str(exc.value)


def test_retry_budget_erst_nach_erschoepfter_rotation(patched, monkeypatch):
    """MAX_RETRIES ist die IP-Reputations-Mitigation und kommt nach der Rotation."""
    session = patched(good=set())
    monkeypatch.setattr(backend, "MAX_RETRIES", 2)
    monkeypatch.setattr(backend.time, "sleep", lambda s: None)
    with pytest.raises(backend.BahnApiError):
        backend._request("GET", "https://x")
    # 3 Targets, danach 2 Retries auf dem letzten Target.
    assert session.tried == [
        "chrome124", "safari180", "firefox135", "firefox135", "firefox135",
    ]
