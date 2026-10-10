from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sportai.config import get_settings
from sportai.db.models import Base
from sportai.services.dashboard import apply_scores, build_match_docs, doc_id, settle
from sportai.services.runs import execute_prematch

T0 = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def test_settle_bets():
    assert settle("Victoire Lyon", "Lyon", "Nice", 2, 1) == "gagne"
    assert settle("Victoire Nice", "Lyon", "Nice", 2, 1) == "perdu"
    assert settle("Match nul", "Lyon", "Nice", 1, 1) == "gagne"
    assert settle("Nice ou nul", "Lyon", "Nice", 1, 1) == "gagne"
    assert settle("Lyon (remboursé si nul)", "Lyon", "Nice", 0, 0) == "rembourse"
    assert settle("Lyon (remboursé si nul)", "Lyon", "Nice", 0, 1) == "perdu"


def test_page_documents_from_a_run_then_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "simulation")
    get_settings.cache_clear()
    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    run = execute_prematch(db, get_settings(), "SIM", "test", now=T0)
    docs = build_match_docs(db, run, {})
    assert len(docs) == len(run.fixtures)
    for d in docs.values():
        assert d["jour"] == "2026-10-09" and d["statut"] == "a_venir"
        ko = datetime.fromisoformat(d["coup_envoi"])  # simulated kick-offs are 1 h to 2 h 10 after T0
        assert set(d["passages"]) == {"h3" if ko - T0 >= timedelta(minutes=105) else "m45"}
        assert d["verdict"] in ("selection", "aucune")
        assert d["buts"]["buts_attendus"] > 0 and "non validé" in d["buts"]["avertissement"]
        btts = next(m for m in d["buts"]["marches"] if m["pari"] == "Les deux équipes marquent : oui")
        assert 0 < btts["proba"] < 1
        assert d["verdict"] == "aucune" or all(s["cote_45"] > 1 and s["raison"] for s in d["selections"])
    picked = next((d for d in docs.values() if d["selections"]), None)
    if picked:
        ko = datetime.fromisoformat(picked["coup_envoi"])
        event = {"commence_time": ko.isoformat(), "home_team": picked["domicile"], "away_team": picked["exterieur"],
                 "completed": True, "scores": [{"name": picked["domicile"], "score": "2"},
                                               {"name": picked["exterieur"], "score": "0"}]}
        key = doc_id(ko, picked["domicile"], picked["exterieur"])
        done = apply_scores([event], docs, T0 + timedelta(hours=4))[key]
        assert done["statut"] == "termine" and done["score"] == "2 - 0"
        assert all(s["resultat"] in ("gagne", "perdu", "rembourse") for s in done["selections"])
    get_settings.cache_clear()


def test_match_under_way_keeps_published_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "simulation")
    get_settings.cache_clear()
    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    run = execute_prematch(db, get_settings(), "SIM", "test", now=T0)
    published = build_match_docs(db, run, {})
    run.fixtures = [{**f, "skipped_reason": "status live"} for f in run.fixtures]
    later = build_match_docs(db, run, published)
    assert set(later) == set(published)
    for key, doc in later.items():
        assert doc == {**published[key], "statut": "en_direct"}
    assert build_match_docs(db, run, {}) == {}
    get_settings.cache_clear()


def test_champions_league_is_information_only(tmp_path, monkeypatch):
    from sportai.services.dashboard import UCL_INFO
    monkeypatch.setenv("DATA_MODE", "simulation")
    get_settings.cache_clear()
    eng = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    run = execute_prematch(db, get_settings(), "UCL", "test", now=T0)
    docs = build_match_docs(db, run, {})
    assert docs and run.config["enabled_markets"] == []
    for d in docs.values():
        assert d["selections"] == [] and d["verdict"] == "aucune"
        assert d["rejets"][0] == UCL_INFO and d["rejets"][1].startswith("Modèle : ")
    get_settings.cache_clear()
