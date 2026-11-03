from datetime import datetime, timedelta, timezone

import sportai.services.alerte_m as A

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "STATE", tmp_path)
    monkeypatch.setenv("ALERTE_M_TOPIC", "test-topic")
    sent = []
    monkeypatch.setattr(A, "_post", lambda t, title, body: sent.append((t, title, body)) or True)
    return sent


EVENT = {"home_team": "Paris Saint Germain", "away_team": "Bayern Munich", "bookmakers": [
    {"title": "Winamax (FR)", "markets": [{"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.85},
                                                                         {"name": "Under", "point": 2.5, "price": 1.95}]}]},
    {"title": "Betclic (FR)", "markets": [{"key": "totals", "outcomes": [{"name": "Over", "point": 2.5, "price": 1.80}]}]}]}


def test_foot_new_live_position_pushed_once_with_sites(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(A, "_odds_api", lambda path, **p: [EVENT])
    doc = {"domicile": "PSG", "exterieur": "Bayern Munich", "competition": "Ligue des champions", "jour": "2026-11-03",
           "score": "1 - 0"}
    new = [{"pari": "Plus de 2,5 buts", "cote": 1.9, "minute": 55, "heure": (NOW - timedelta(minutes=1)).isoformat()}]
    assert A.alerter_foot(doc, [], new, NOW, "k1") == 1
    assert A.alerter_foot(doc, [], new, NOW, "k1") == 0  # once
    assert A.alerter_foot(doc, new, new, NOW, "k1") == 0
    (topic, title, body), = sent
    assert topic == "test-topic" and "Le M en direct" in title
    assert "PSG - Bayern Munich" in body and "cote 1,90" in body and "55e minute, score 1-0" in body
    assert "Où la prendre : Winamax 1,85, Betclic 1,80" in body


def test_stale_or_no_topic_not_pushed(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(A, "_odds_api", lambda path, **p: [])
    doc = {"domicile": "A", "exterieur": "B", "jour": "2026-11-03"}
    old = [{"pari": "Victoire A", "cote": 1.7, "minute": 30, "heure": (NOW - timedelta(minutes=40)).isoformat()}]
    assert A.alerter_foot(doc, [], old, NOW, "k2") == 0
    monkeypatch.delenv("ALERTE_M_TOPIC")
    fresh = [{**old[0], "heure": NOW.isoformat()}]
    assert A.alerter_foot(doc, [], fresh, NOW, "k2") == 0 and not sent


def test_failed_push_retried_next_cycle(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(A, "_odds_api", lambda path, **p: [])
    monkeypatch.setattr(A, "_post", lambda *a: False)
    assert not A.alerter("foot", "c", "A - B", "Victoire A", 1.7, now=NOW)
    monkeypatch.setattr(A, "_post", lambda *a: True)
    assert A.alerter("foot", "c", "A - B", "Victoire A", 1.7, now=NOW)


def test_tennis_winner_sites(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    ev = {"home_team": "Andrey Rublev", "away_team": "Tomas Martin Etcheverry", "bookmakers": [
        {"title": "PMU (FR)", "markets": [{"key": "h2h", "outcomes": [{"name": "Andrey Rublev", "price": 1.62},
                                                                       {"name": "Tomas Martin Etcheverry", "price": 2.2}]}]}]}
    monkeypatch.setattr(A, "_odds_api", lambda path, **p: [{"key": "tennis_atp_paris_masters"}] if path == "" else [ev])
    row = {"id": "9", "joueur_1": "A. Rublev", "joueur_2": "T. M. Etcheverry",
           "m_direct": {"position": "Rublev vainqueur", "cle": ["vainqueur", "Home"], "cote": 1.6, "score_au_moment": "6-4 1-0"}}
    assert A.alerter_tennis("2026-11-03", row, NOW)
    assert not A.alerter_tennis("2026-11-03", row, NOW)
    body = sent[0][2]
    assert "Rublev vainqueur · cote 1,60" in body and "score 6-4 1-0" in body and "PMU 1,62" in body
