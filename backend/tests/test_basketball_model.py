from datetime import date, timedelta

from sportai.engine.basketball.ratings import Game, RatingsModel, rest_flags


def _season():
    teams = ["A", "B", "C", "D"]
    strength = {"A": 8, "B": 2, "C": -2, "D": -8}
    games, d = [], date(2025, 11, 1)
    for i in range(240):
        h, a = teams[i % 4], teams[(i // 4 + i + 1) % 4]
        if h == a:
            continue
        noise = ((i * 37) % 21) - 10
        games.append(Game(d, h, a, 112 + 2 + strength[h] + noise, 112 + strength[a] - noise))
        d += timedelta(days=1)
    return games


def test_stronger_team_is_favourite_and_probabilities_are_coherent():
    games = _season()
    m = RatingsModel().fit(games, games[-1].day + timedelta(days=1))
    p = m.predict("A", "D")
    assert p.margin > 10
    assert 0.8 < p.p_home_win() < 1
    assert abs(m.predict("D", "A").p_home_win() + p.p_home_win() - 1) < 0.2
    assert p.p_over(p.total) == 0.5 or abs(p.p_over(p.total) - 0.5) < 1e-9
    assert p.p_home_cover(-p.margin) == 0.5 or abs(p.p_home_cover(-p.margin) - 0.5) < 1e-9


def test_back_to_back_flag():
    g = [Game(date(2025, 11, 1), "A", "B", 100, 99), Game(date(2025, 11, 2), "A", "C", 100, 99)]
    assert rest_flags(g)[1][0] == 1
