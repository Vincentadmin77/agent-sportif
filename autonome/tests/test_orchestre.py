import os
from datetime import datetime

os.environ.setdefault("ANALYTRACK_BASE", "/tmp/analytrack-test-base")

from autonome import etapes as E  # noqa: E402
from autonome import orchestre as O  # noqa: E402


def cles(h, m):
    return [c for c, _ in O.taches_fixes(datetime(2026, 11, 16, h, m, tzinfo=E.PARIS))]


def test_horaires_des_taches_claude():
    assert cles(7, 50) == ["matin"]
    assert cles(8, 5) == ["tennis-matin"]
    assert cles(12, 40) == ["gel-1240"] and cles(16, 40) == ["gel-1640"]
    assert cles(9, 50) == ["m-0950"] and cles(3, 50) == ["m-0350"]
    assert "m-0650" not in cles(6, 50) and "m-0850" not in cles(8, 50)  # pas de passage du M de 6 h à 8 h
    assert cles(14, 51) == ["spec-1451"]
    assert cles(22, 25) == ["basket-nuit-2225"] and cles(4, 25) == ["basket-nuit-0425"] and cles(15, 25) == []


def test_jours():
    t = datetime(2026, 11, 16, 1, 30, tzinfo=E.PARIS)  # 01:30 Paris
    assert E.jour_foot(t) == "2026-11-15"  # journée foot jusqu'à 2 h
    assert E.jour_paris(t, 6) == "2026-11-15"  # le M : la veille avant 6 h
    assert E.jour_paris(t, 4) == "2026-11-15"  # spécialistes : la veille avant 4 h
    assert E.jour_paris(datetime(2026, 11, 16, 4, 30, tzinfo=E.PARIS), 4) == "2026-11-16"


def test_planning_aiguillage(monkeypatch):
    vus = []
    monkeypatch.setattr(O, "executer", lambda nom, fn, *a, **k: vus.append(nom))
    monkeypatch.setattr(O, "demarrer_direct_foot", lambda comps: vus.append(f"boucle foot {comps}"))
    monkeypatch.setattr(E, "credits_odds", lambda: 50)
    O.etape_planning({"kind": "h3", "competitions": ["FR1", "NBA"]})
    O.etape_planning({"kind": "m15", "competitions": ["FR1"]})  # crédits < 100 : sauté
    O.etape_planning({"kind": "live", "competitions": ["FR1", "NBA"]})
    O.etape_planning({"kind": "final", "competitions": ["NBA"]})
    assert vus == ["h3 FR1 NBA", "boucle foot ['FR1']", "direct basket NBA", "final NBA"]


def test_championnats_toujours_frc_unl_basket(tmp_path):
    st = E.Store(tmp_path)
    st.set("reglages", "championnats", {"actifs": ["FR1", "XX"]})
    assert E.championnats(st) == ["FR1", "FRC", "UNL", "NBA", "WNBA"]
