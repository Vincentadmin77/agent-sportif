from sportai.engine.tennis.elo import (SurfaceElo, games_distribution, match_from_set, p_games_handicap,
                                       p_total_games_over, set_from_match, set_scores)


def test_elo_learns_and_surface_counts():
    e = SurfaceElo()
    for _ in range(30):
        e.update("nadal", "x", "clay")
        e.update("y", "nadal", "grass")
    assert e.p_win("nadal", "x", "clay") > 0.8
    assert e.p_win("nadal", "y", "clay") > e.p_win("nadal", "y", "grass")


def test_sets_and_games_are_coherent():
    for bo in (3, 5):
        assert abs(match_from_set(set_from_match(0.7, bo), bo) - 0.7) < 1e-6
        assert abs(sum(set_scores(0.7, bo).values()) - 1) < 1e-9
    d = games_distribution(0.5, 3, "ATP")
    assert abs(sum(d.values()) - 1) < 1e-6
    assert 0.4 < p_games_handicap(0.5, 3, "ATP", 0.5) < 0.6
    assert p_total_games_over(0.5, 3, "ATP", 20.5) > p_total_games_over(0.9, 3, "ATP", 20.5)
    assert p_games_handicap(0.8, 3, "WTA", -3.5) > 0.4
