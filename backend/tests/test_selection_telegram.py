import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import selection_telegram as T  # noqa: E402


def test_settle_team_or_draw_and_goals():
    assert T._settle("hx_u3.5", 1, 1, 0, 0) == "gagne"
    assert T._settle("hx_u3.5", 2, 2, 1, 1) == "perdu"
    assert T._settle("ax_o3.5", 2, 2, 1, 1) == "gagne"
    assert T._settle("h_o1.5", 2, 0, 1, 0) == "gagne"
    assert T._settle("dnb_a", 1, 1, 0, 0) == "rembourse"
    assert T._settle("btts", 1, 0, 0, 0) == "perdu"
    assert T._settle("ht_o1.5", 3, 0, 1, 0) == "perdu"


def test_team_or_draw_price_covers_two_outcomes_of_one_bookmaker():
    q = {(25, "Home/Under 3.5"): 2.5, (25, "Draw/Under 3.5"): 7.5, (25, "Away/Under 3.5"): 9.5,
         (25, "Home/Over 3.5"): 2.5, (25, "Draw/Over 3.5"): 13.0, (25, "Away/Over 3.5"): 19.0}
    c = {x["cle"]: x for x in T._candidates(q, "A", "B")}
    assert round(c["hx_u3.5"]["cote"], 3) == round(1 / (1 / 2.5 + 1 / 7.5), 3)
    assert c["hx_u3.5"]["pari"] == "A ou nul et moins de 3,5 buts"
    assert 0 < c["hx_u3.5"]["chance"] < 1


def test_unstable_teams_are_skipped():
    assert T._form_ok("a_o1.5", "", "V V V D N")
    assert not T._form_ok("a_o1.5", "", "V V D D N")
    assert not T._form_ok("hx_u3.5", "D D D V V", "")
    assert T._form_ok("btts", "", "")
