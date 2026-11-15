import json

from autonome.store import Store, garder_figees


def test_position_figee_garde_son_choix_mais_recoit_son_resultat():
    ancien = {"paris_retenus": {"m1": {"pari": "Lens", "cote": 1.8, "fige": True, "resultat": None}}}
    nouveau = {"paris_retenus": {"m1": {"pari": "Nul", "cote": 3.1, "fige": True, "resultat": "gagne"}}}
    doc, n = garder_figees(ancien, nouveau)
    assert doc["paris_retenus"]["m1"] == {"pari": "Lens", "cote": 1.8, "fige": True, "resultat": "gagne"}
    assert n == 2


def test_position_figee_disparue_est_remise():
    ancien = {"foot": {"m": {"positions": [{"match_id": 1, "pari": "A", "fige": True}, {"match_id": 2, "pari": "B"}]}}}
    nouveau = {"foot": {"m": {"positions": [{"match_id": 3, "pari": "C"}]}}}
    doc, n = garder_figees(ancien, nouveau)
    ids = [p["match_id"] for p in doc["foot"]["m"]["positions"]]
    assert ids == [3, 1] and n == 1  # la position non figée (2) peut partir, la figée (1) jamais


def test_position_non_figee_libre():
    doc, n = garder_figees({"x": {"pari": "A"}}, {"x": {"pari": "B"}})
    assert doc == {"x": {"pari": "B"}} and n == 0


def test_store_versions_journal_et_sortie(tmp_path):
    st = Store(tmp_path / "base")
    assert st.set("top5", "2026-11-15", {"a": 1})["version"] == 1
    assert st.set("top5", "2026-11-15", {"a": 1})["ecrit"] is False  # identique : rien
    assert st.set("top5", "2026-11-15", {"a": 2})["version"] == 2
    assert st.set("joueur", "x", {"a": 1})["ecrit"] is False  # joueur pro supprimé
    out = tmp_path / "out"
    (out / "matchs").mkdir(parents=True)
    (out / "matchs" / "m.json").write_text(json.dumps({"jour": "2026-11-15"}))
    (out / "joueur").mkdir()
    (out / "joueur" / "j.json").write_text("{}")
    (out / "etat.json").write_text(json.dumps({"message": "ok"}))
    assert st.ecrire_sortie(out, "test") == ["matchs/m", "etat/agent"]
    assert "top5/2026-11-15 v2" in (tmp_path / "base" / "_journal.log").read_text()
