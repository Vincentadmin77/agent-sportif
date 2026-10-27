"""Match context SofaScore shows and the agent did not (owner, 2026-10-21): the referee's card profile, the
standings and what is at stake, the teams' streaks, and the weather at the stadium.

Context only: none of it changes a probability or a selection until a backtest says it should. Each piece is
written to the match document's ``contexte`` field, with one plain sentence per piece in ``contexte.phrases``.

- Referee: yellow and red cards and penalties per match, from the API-Football fixtures already cached in
  data/api-football (no request). The index is rebuilt once a day by goals-refresh (state/arbitres.json).
- Standings: API-Football /standings, one request per competition (cached 6 h).
- Streaks: the teams' last 10 matches, all competitions (the request the form dots already make).
- Weather: Open-Meteo (free for non-commercial use; a commercial licence before going public), the stadium
  located once with Open-Meteo's geocoding and kept in state/stades.json.
- Next match (owner, 2026-10-26): each team's match after tonight's (API-Football /fixtures?team=&next=2, all
  competitions, domestic cups included; the season calendars of data/calendrier when it fails). A team that plays
  a more important match, or a clearly stronger opponent, within 4 days may hold back tonight: "risque de
  rotation", docs/15-prochain-match.md. Unlike the rest of the context, a high risk is used by the tickets
  (pronostic.rotation_block), never by the probabilities.
"""

from __future__ import annotations

import gzip
import json
import os
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ARCHIVE = Path(os.getenv("SPORTAI_GOALS_CACHE", "/mnt/project-files/agent-sportif/data/api-football"))
REFEREE_INDEX = Path(os.getenv("REFEREE_INDEX_PATH", "/mnt/project-files/agent-sportif/state/arbitres.json"))
STADIUMS = Path(os.getenv("STADIUMS_PATH", "/mnt/project-files/agent-sportif/state/stades.json"))
FINISHED = ("FT", "AET", "PEN")
MIN_REFEREE_MATCHES = 8
# Walk-forward check on 10 953 cached league matches (2026-10-21, docs/14-contexte-match.md): referees rated
# "sévère" before the match then showed 5.09 card points per match against 4.60 for their leagues (over 4.5:
# 54 %), "clément" 3.98 against 4.63 (over 4.5: 36 %), the others 4.64 against 4.57 (47 %).
SHRINK = 10
STRICT, LENIENT = 1.15, 0.87  # referee's cards per match against the average of the leagues he refereed
COUNTRY_CODES = {"France": "FR", "England": "GB", "Spain": "ES", "Germany": "DE", "Italy": "IT", "Brazil": "BR",
                 "Portugal": "PT", "Netherlands": "NL", "Belgium": "BE", "Turkey": "TR", "Scotland": "GB",
                 "Austria": "AT", "Switzerland": "CH", "Greece": "GR", "Czech-Republic": "CZ", "Denmark": "DK",
                 "Croatia": "HR", "Serbia": "RS", "Norway": "NO", "Ukraine": "UA", "Poland": "PL",
                 "Sweden": "SE", "Cyprus": "CY", "Israel": "IL", "Kazakhstan": "KZ", "Azerbaijan": "AZ",
                 "Bulgaria": "BG", "Hungary": "HU", "Romania": "RO", "Slovenia": "SI", "Slovakia": "SK",
                 "Wales": "GB"}


# ---------------------------------------------------------------- referee

def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("-", " ").replace(".", ". ").split())


def referee_keys(name: str | None) -> tuple[str, str] | None:
    """(full name, initial + surname): API-Football writes "Jeremie Pignard, France" or "J. Pignard"."""
    if not name or not name.split(",")[0].strip():
        return None
    words = _plain(name.split(",")[0]).split()
    return " ".join(words), f"{words[0][0]}. {' '.join(words[1:])}" if len(words) > 1 else words[0]


def _cards(f: dict) -> tuple[int, int] | None:
    """(yellow, red) cards of a finished match, both teams, from its statistics; None when not reported."""
    y = r = 0
    seen = False
    for block in f.get("statistics") or []:
        for s in block.get("statistics") or []:
            v = s.get("value")
            if s.get("type") == "Yellow Cards":
                seen = True
                y += int(v or 0)
            elif s.get("type") == "Red Cards":
                r += int(v or 0)
    return (y, r) if seen else None


def _penalties(f: dict) -> int:
    return sum(1 for e in f.get("events") or []
               if e.get("type") == "Goal" and e.get("detail") in ("Penalty", "Missed Penalty"))


def referee_rows(folder: Path = ARCHIVE) -> list[dict]:
    """One row per cached finished match with a referee and card statistics."""
    rows, seen = [], set()
    for p in folder.glob("fixtures_ids_*.json.gz"):
        try:
            with gzip.open(p, "rt") as fh:
                data = json.load(fh)["data"]
        except (OSError, ValueError, KeyError):
            continue
        for f in data.get("response") or []:
            fid = f["fixture"]["id"]
            keys = referee_keys(f["fixture"].get("referee"))
            cards = _cards(f)
            if fid in seen or not keys or not cards or f["fixture"]["status"]["short"] not in FINISHED:
                continue
            seen.add(fid)
            rows.append({"id": fid, "date": f["fixture"]["date"][:10], "ligue": f["league"]["id"],
                         "nom": f["fixture"]["referee"].split(",")[0].strip(), "cle": keys[0], "court": keys[1],
                         "jaunes": cards[0], "rouges": cards[1], "penalties": _penalties(f)})
    return sorted(rows, key=lambda r: (r["date"], r["id"]))


def build_referee_index(rows: list[dict], now: datetime) -> dict:
    """Totals per referee (full name, with the short form pointing at it) and per league."""
    leagues: dict[int, Counter] = defaultdict(Counter)
    refs: dict[str, dict] = {}
    short: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        lg = leagues[r["ligue"]]
        lg["matchs"] += 1
        for k in ("jaunes", "rouges", "penalties"):
            lg[k] += r[k]
        ref = refs.setdefault(r["cle"], {"nom": r["nom"], "matchs": 0, "jaunes": 0, "rouges": 0, "penalties": 0,
                                         "ligues": Counter(), "dernier": r["date"]})
        ref["matchs"] += 1
        for k in ("jaunes", "rouges", "penalties"):
            ref[k] += r[k]
        ref["ligues"][str(r["ligue"])] += 1
        ref["dernier"] = max(ref["dernier"], r["date"])
        if len(r["nom"]) > len(ref["nom"]):  # keep the fullest spelling for the page
            ref["nom"] = r["nom"]
        short[r["court"]][r["cle"]] += 1
    # "A. Taylor" and "Anthony Taylor" are the same man when the short form matches one full name only
    full = {s: [k for k in c if not k.split()[0].endswith(".")] for s, c in short.items()}
    alias = {s: ks[0] for s, ks in full.items() if len(ks) == 1}
    merged = {k: r for k, r in refs.items() if not (k.split()[0].endswith(".") and alias.get(k, k) != k)}
    for key, ref in refs.items():
        if key not in merged:
            m = merged[alias[key]]
            for k in ("matchs", "jaunes", "rouges", "penalties"):
                m[k] += ref[k]
            m["ligues"].update(ref["ligues"])
            m["dernier"] = max(m["dernier"], ref["dernier"])
    for ref in merged.values():
        ref["ligues"] = dict(ref["ligues"])
    return {"maj": now.isoformat(), "matchs": len(rows), "alias": alias,
            "ligues": {str(k): dict(v) for k, v in leagues.items()}, "arbitres": merged}


def refresh_referee_index(now: datetime, folder: Path = ARCHIVE, path: Path = REFEREE_INDEX) -> dict:
    index = build_referee_index(referee_rows(folder), now)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False))
    tmp.replace(path)
    return {"arbitres": len(index["arbitres"]), "matchs": index["matchs"]}


def load_referee_index(path: Path = REFEREE_INDEX) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _fr(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def referee_profile(index: dict | None, name: str | None) -> dict | None:
    """The referee of a match against the leagues he refereed: cards and penalties per match."""
    keys = referee_keys(name)
    if not keys:
        return None
    shown = name.split(",")[0].strip()
    ref = None
    if index:
        arb = index.get("arbitres") or {}
        ref = arb.get(keys[0]) or arb.get((index.get("alias") or {}).get(keys[1], "")) or arb.get(keys[1])
        if ref is None:  # "J. Pignard" on the fixture, "Jeremie Pignard" in the archive
            hits = [k for k in arb if referee_keys(k) and referee_keys(k)[1] == keys[1]]
            ref = arb[hits[0]] if len(hits) == 1 else None
    if not ref or ref["matchs"] < MIN_REFEREE_MATCHES:
        n = ref["matchs"] if ref else 0
        return {"nom": shown, "matchs": n, "profil": "inconnu",
                "phrase": f"Arbitre : {shown} (trop peu de matchs connus pour juger sa sévérité)."}
    n = ref["matchs"]
    lg = index["ligues"]
    weights = {k: v for k, v in ref["ligues"].items() if k in lg and lg[k]["matchs"]}
    total = sum(weights.values()) or 1
    avg = {k: sum(w * lg[l][k] / lg[l]["matchs"] for l, w in weights.items()) / total
           for k in ("jaunes", "rouges", "penalties")}
    mine = {k: ref[k] / n for k in ("jaunes", "rouges", "penalties")}
    ref_cards = avg["jaunes"] + 2 * avg["rouges"]  # a red card counts as two, as the bookmakers' card points do
    cards = (ref["jaunes"] + 2 * ref["rouges"] + SHRINK * ref_cards) / (n + SHRINK)  # few matches: pulled to the mean
    ratio = cards / ref_cards if ref_cards else 1.0
    profil = "sévère" if ratio >= STRICT else "clément" if ratio <= LENIENT else "dans la moyenne"
    pen = "" if not avg["penalties"] else (
        " et siffle souvent des penalties" if mine["penalties"] >= 1.3 * avg["penalties"] else
        " et siffle peu de penalties" if mine["penalties"] <= 0.7 * avg["penalties"] else "")
    return {"nom": ref["nom"] or shown, "matchs": n, "profil": profil,
            "jaunes_par_match": round(mine["jaunes"], 2), "rouges_par_match": round(mine["rouges"], 3),
            "penalties_par_match": round(mine["penalties"], 3),
            "moyenne": {"jaunes_par_match": round(avg["jaunes"], 2), "rouges_par_match": round(avg["rouges"], 3),
                        "penalties_par_match": round(avg["penalties"], 3)},
            "phrase": f"Arbitre : {ref['nom'] or shown}, {profil} ({_fr(mine['jaunes'])} cartons jaunes par match "
                      f"contre {_fr(avg['jaunes'])} en moyenne, sur {n} matchs){pen}."}


# ---------------------------------------------------------------- standings and stakes

def zone_label(description: str | None) -> str | None:
    d = (description or "").strip().lower()
    if not d:
        return None
    if "relegation" in d:
        return "barrage pour le maintien" if "play" in d else "relégation"
    if "champions league" in d:
        if "1/8" in d:
            return "qualification directe pour les huitièmes"
        if "1/16" in d or "play" in d:
            return "barrages de la phase finale"
        return "Ligue des champions"
    if "europa league" in d:
        if "1/8" in d:
            return "qualification directe pour les huitièmes"
        if "1/16" in d or "play" in d and "promotion" in d:
            return "barrages de la phase finale"
        return "Ligue Europa"
    if "conference league" in d:
        return "Ligue Conférence"
    if "qualifying" in d or "qualification" in d:
        return "tour préliminaire européen"
    if "play" in d:
        return "barrages européens"
    if "promotion" in d:
        return "qualification"
    return description.strip()


def _group_of(standings: list[dict], team_id: int) -> list[dict] | None:
    for league in standings:
        for group in (league.get("league") or {}).get("standings") or []:
            if any(r["team"]["id"] == team_id for r in group):
                return group
    return None


def stakes(standings: list[dict], team_id: int, cup: bool = False) -> dict | None:
    """Where a team stands: rank, points, gap to the zone above and the zone below, and whether the match
    still matters (only said late in the season, when the arithmetic settles it)."""
    group = _group_of(standings, team_id)
    if not group:
        return None
    group = sorted(group, key=lambda r: r["rank"])
    me = next(r for r in group if r["team"]["id"] == team_id)
    played = me["all"]["played"] or 0
    total = 8 if cup else 2 * (len(group) - 1)
    left = max(total - played, 0)
    # a cup's league phase has no description below the play-off places: those teams are out
    zones = [zone_label(r.get("description")) or ("élimination" if cup else None) for r in group]
    mine = zones[group.index(me)]
    out = {"rang": me["rank"], "points": me["points"], "joues": played, "restants": left, "zone": mine,
           "equipes": len(group), "forme": me.get("form")}
    i = group.index(me)
    above = next((j for j in range(i - 1, -1, -1) if zones[j] != mine), None)
    below = next((j for j in range(i + 1, len(group)) if zones[j] != mine), None)
    if above is not None:
        out["au_dessus"] = {"rang": group[above]["rank"], "zone": zones[above] or "milieu de tableau",
                            "ecart": group[above]["points"] - me["points"]}
    if below is not None:
        out["en_dessous"] = {"rang": group[below]["rank"], "zone": zones[below] or "milieu de tableau",
                             "ecart": me["points"] - group[below]["points"]}
    if me["rank"] != 1:
        out["ecart_leader"] = group[0]["points"] - me["points"]
    late = left <= (2 if cup else 6)
    if late:
        can_climb = above is not None and me["points"] + 3 * left >= group[above]["points"]
        can_fall = below is not None and group[below]["points"] + 3 * left >= me["points"]
        close = (above is not None and out["au_dessus"]["ecart"] <= 3) or (below is not None and out["en_dessous"]["ecart"] <= 3)
        out["enjeu"] = "sans enjeu" if not (can_climb or can_fall) else "fort" if close else "normal"
    return out


def _pts(n: int) -> str:
    return f"{n} pt" if abs(n) <= 1 else f"{n} pts"


def stakes_sentence(team: str, s: dict | None) -> str | None:
    if not s:
        return None
    rank = "1er" if s["rang"] == 1 else f"{s['rang']}e"
    bits = [f"{team} est {rank} avec {_pts(s['points'])} ({s['joues']} matchs)"]
    if s.get("zone"):
        bits[0] += f", place « {s['zone']} »"
    gaps = []
    if s.get("au_dessus"):
        a = s["au_dessus"]
        gaps.append(f"{_pts(a['ecart'])} derrière la {a['rang']}e place ({a['zone']})" if a["ecart"] > 0 else
                    f"à égalité avec la {a['rang']}e place ({a['zone']})")
    elif s["rang"] != 1 and s.get("ecart_leader") is not None:
        gaps.append(f"{_pts(s['ecart_leader'])} du leader")
    if s.get("en_dessous"):
        b = s["en_dessous"]
        gaps.append(f"{_pts(b['ecart'])} d'avance sur la {b['rang']}e place ({b['zone']})" if b["ecart"] > 0 else
                    f"à égalité avec la {b['rang']}e place ({b['zone']})")
    text = bits[0] + (" : " + ", ".join(gaps) if gaps else "") + "."
    if s.get("enjeu") == "sans enjeu":
        text += " Plus rien à jouer au classement : motivation à surveiller."
    elif s.get("enjeu") == "fort":
        text += " Fin de saison serrée : gros enjeu."
    return text


# ---------------------------------------------------------------- streaks

def _played(fx: dict, team_id: int) -> tuple[str, bool, int, int] | None:
    if fx["fixture"]["status"]["short"] not in FINISHED:
        return None
    g = (fx.get("score") or {}).get("fulltime") or {}
    if g.get("home") is None:
        g = fx.get("goals") or {}
    if g.get("home") is None:
        return None
    home = fx["teams"]["home"]["id"] == team_id
    mine, theirs = (g["home"], g["away"]) if home else (g["away"], g["home"])
    return fx["fixture"]["date"], home, int(mine), int(theirs)


def series(fixtures: list[dict], team_id: int, team: str) -> dict:
    """Streaks over a team's last matches (all competitions): counts and the notable ones as sentences."""
    games = sorted(filter(None, (_played(f, team_id) for f in fixtures)), key=lambda g: g[0])[-10:]
    n = len(games)
    if n < 5:
        return {"matchs": n, "phrases": []}
    c = {"victoires": sum(m > t for _, _, m, t in games), "nuls": sum(m == t for _, _, m, t in games),
         "defaites": sum(m < t for _, _, m, t in games), "plus_2_5": sum(m + t >= 3 for _, _, m, t in games),
         "deux_marquent": sum(m > 0 and t > 0 for _, _, m, t in games),
         "clean_sheets": sum(t == 0 for _, _, m, t in games), "sans_marquer": sum(m == 0 for _, _, m, t in games)}
    out = {"matchs": n, **c, "phrases": []}
    run = []  # current run, most recent first
    for _, _, m, t in reversed(games):
        run.append("V" if m > t else "N" if m == t else "D")
    def streak(ok) -> int:
        k = 0
        for r in run:
            if not ok(r):
                break
            k += 1
        return k
    ph = out["phrases"]
    for label, k in (("victoires de suite", streak(lambda r: r == "V")),
                     ("matchs sans défaite", streak(lambda r: r != "D")),
                     ("matchs sans victoire", streak(lambda r: r != "V")),
                     ("défaites de suite", streak(lambda r: r == "D"))):
        if k >= 3 and not (label == "matchs sans défaite" and streak(lambda r: r == "V") == k) \
                and not (label == "matchs sans victoire" and streak(lambda r: r == "D") == k):
            ph.append(f"{team} : {k} {label}.")
    hi, lo = 0.7 * n, 0.2 * n
    for key, text in (("plus_2_5", "plus de 2,5 buts"), ("deux_marquent", "les deux équipes ont marqué"),
                      ("clean_sheets", "aucun but encaissé"), ("sans_marquer", "aucun but marqué")):
        if c[key] >= hi:
            ph.append(f"{team} : {text} dans {c[key]} de ses {n} derniers matchs.")
    if c["plus_2_5"] <= lo:
        ph.append(f"{team} : moins de 2,5 buts dans {n - c['plus_2_5']} de ses {n} derniers matchs.")
    out["serie"] = "".join(reversed(run))
    return out


# ---------------------------------------------------------------- weather

WEATHER_CODES = {0: "ciel dégagé", 1: "peu nuageux", 2: "partiellement nuageux", 3: "couvert", 45: "brouillard",
                 48: "brouillard givrant", 51: "bruine légère", 53: "bruine", 55: "bruine forte",
                 61: "pluie faible", 63: "pluie", 65: "forte pluie", 66: "pluie verglaçante", 67: "pluie verglaçante",
                 71: "neige faible", 73: "neige", 75: "forte neige", 77: "grésil", 80: "averses",
                 81: "averses", 82: "violentes averses", 85: "averses de neige", 86: "fortes averses de neige",
                 95: "orage", 96: "orage avec grêle", 99: "orage avec grêle"}


def _stadiums(path: Path = STADIUMS) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def stadium_position(http_geo, venue: dict | None, country: str | None, path: Path = STADIUMS) -> dict | None:
    """Latitude and longitude of a stadium's city (Open-Meteo geocoding), kept for good in state/stades.json."""
    if not venue or not venue.get("city"):
        return None
    key = str(venue.get("id") or f"{venue.get('name')}|{venue['city']}")
    known = _stadiums(path)
    if key in known:
        return known[key]
    city = venue["city"].split(",")[0].strip()
    params = {"name": city, "count": 5, "language": "fr", "format": "json"}
    code = COUNTRY_CODES.get(country or "")
    if code:
        params["countryCode"] = code
    res = (http_geo.get_json("/v1/search", params, ttl=86400) or {}).get("results") or []
    if not res:
        return None
    best = max(res, key=lambda r: r.get("population") or 0)
    pos = {"lat": best["latitude"], "lon": best["longitude"], "ville": best.get("name"), "stade": venue.get("name")}
    known = _stadiums(path)
    known[key] = pos
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(known, ensure_ascii=False))
    tmp.replace(path)
    return pos


def weather_at(http_meteo, pos: dict, kickoff: datetime) -> dict | None:
    """Forecast for the kick-off hour and the next one (Open-Meteo), with the effect worth saying."""
    ko = kickoff.astimezone(timezone.utc)
    data = http_meteo.get_json("/v1/forecast", {
        "latitude": pos["lat"], "longitude": pos["lon"], "timezone": "UTC",
        "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m,weather_code",
        "start_date": ko.date().isoformat(), "end_date": ko.date().isoformat() if ko.hour < 22 else
        datetime.fromtimestamp(ko.timestamp() + 7200, timezone.utc).date().isoformat()}, ttl=3 * 3600)
    h = (data or {}).get("hourly") or {}
    times = h.get("time") or []
    idx = [i for i, t in enumerate(times)
           if 0 <= (datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - ko.replace(minute=0)).total_seconds() <= 3600]
    if not idx:
        return None
    def vals(k):
        return [h[k][i] for i in idx if h.get(k) and h[k][i] is not None]
    temp, rain, wind, gust = vals("temperature_2m"), vals("precipitation"), vals("wind_speed_10m"), vals("wind_gusts_10m")
    codes = vals("weather_code")
    out = {"temperature": round(sum(temp) / len(temp)) if temp else None,
           "pluie_mm_h": round(max(rain), 1) if rain else None, "vent_kmh": round(max(wind)) if wind else None,
           "rafales_kmh": round(max(gust)) if gust else None,
           "ciel": WEATHER_CODES.get(int(max(codes))) if codes else None}
    notes = []
    if (out["pluie_mm_h"] or 0) >= 4:
        notes.append("forte pluie : terrain lourd, jeu plus haché")
    elif (out["pluie_mm_h"] or 0) >= 1:
        notes.append("pluie : ballon plus rapide, erreurs possibles")
    if (out["vent_kmh"] or 0) >= 35 or (out["rafales_kmh"] or 0) >= 60:
        notes.append("vent fort : jeu long et centres plus difficiles")
    if out["temperature"] is not None and out["temperature"] >= 30:
        notes.append("forte chaleur : rythme qui baisse en seconde période")
    if out["temperature"] is not None and out["temperature"] <= -2:
        notes.append("grand froid : terrain dur")
    out["effet"] = "; ".join(notes) or None
    bits = [b for b in (out["ciel"], f"{out['temperature']} °C" if out["temperature"] is not None else None,
                        f"vent {out['vent_kmh']} km/h" if out["vent_kmh"] is not None else None) if b]
    out["phrase"] = "Météo au coup d'envoi : " + ", ".join(bits) + (f" ({out['effet']})." if out["effet"] else ".")
    return out


# ---------------------------------------------------------------- next match and rotation risk

ROTATION_DAYS = 4  # next match at most this many days after tonight's (betting days, Paris)
HIGH_DAYS = 3  # a more important match this close: high risk
STRONGER_PPM = 0.5  # next opponent at least this many points per match above tonight's: clearly stronger
MIN_PLAYED = 3  # fewer matches in the table: strength not judged
DROPPED = ("PST", "CANC", "ABD", "SUSP")
CONTINENTAL = {2: "Ligue des champions", 3: "Ligue Europa", 848: "Ligue Conférence", 13: "Copa Libertadores",
               11: "Copa Sudamericana"}
LEAGUE_NAMES = {61: "Ligue 1", 39: "Premier League", 140: "LaLiga", 78: "Bundesliga", 135: "Serie A",
                71: "Brasileirão", 203: "Süper Lig", 66: "Coupe de France", 45: "FA Cup", 48: "Coupe de la Ligue anglaise",
                143: "Coupe du Roi", 81: "Coupe d'Allemagne", 137: "Coupe d'Italie", 73: "Coupe du Brésil",
                206: "Coupe de Turquie",
                **CONTINENTAL}
# the season calendars kept by calendrier.py, used when API-Football does not answer
CALENDAR_LEAGUES = {"FR1": 61, "EN1": 39, "ES1": 140, "DE1": 78, "IT1": 135, "BR1": 71, "TR1": 203, "UCL": 2, "UEL": 3}
KNOCKOUT = ("round of", "quarter", "semi", "final", "knockout", "play-off", "playoff", "8th", "16th", "1/8", "1/16")


def is_league(league: dict) -> bool:
    """A championship rather than a cup: API-Football's fixtures carry no type, their rounds say it
    ("Regular Season - 12" against "Round of 32", "Quarter-finals")."""
    if league.get("id") in CONTINENTAL:
        return False
    return (league.get("type") == "League" or league.get("id") in CALENDAR_LEAGUES.values()
            or (league.get("round") or "").lower().startswith("regular season"))


def importance(league: dict, stake: dict | None = None) -> int:
    """How much a match counts, simply: 4 European knockout round (play-offs included), 3 European league phase, a
    domestic cup's semi-final or final, or a league match with a big stake late in the season (title, Europe,
    relegation), 2 league match or cup quarter-final, 1 earlier cup rounds and super cups, 0 friendlies."""
    lid, name = league.get("id"), (league.get("name") or "").lower()
    rnd = (league.get("round") or "").lower()
    if "friendl" in name:
        return 0
    if lid in CONTINENTAL:
        return 4 if any(k in rnd for k in KNOCKOUT) else 3
    if is_league(league):
        return 3 if (stake or {}).get("enjeu") == "fort" else 2
    if any(k in name for k in ("super", "trophée", "community shield")):
        return 1
    if "semi" in rnd or rnd.startswith("final"):
        return 3
    if "quarter" in rnd:
        return 2
    return 1


def _kickoff(s: str) -> datetime:
    return datetime.fromisoformat(s)  # Python 3.11+ reads the "Z" itself


def _betting_day(ko: datetime) -> date:
    # the model's module, imported only when needed
    from sportai.services.prematch import journee

    return journee(ko)


def next_from_fixtures(fixtures: list[dict], team_id: int, tonight: dict) -> dict | None:
    """The team's first match after tonight's, from API-Football's next fixtures (raw), as a plain row."""
    ko = _kickoff(tonight["fixture"]["date"])
    for f in sorted(fixtures or [], key=lambda f: f["fixture"]["date"]):
        if (f["fixture"]["id"] == tonight["fixture"]["id"] or _kickoff(f["fixture"]["date"]) <= ko
                or f["fixture"]["status"]["short"] in DROPPED):
            continue
        home = f["teams"]["home"]["id"] == team_id
        opp = f["teams"]["away" if home else "home"]
        lg = f.get("league") or {}
        return {"id": f["fixture"]["id"], "coup_envoi": f["fixture"]["date"], "domicile": home,
                "adversaire": opp["name"], "adversaire_id": opp.get("id"),
                "ligue": {"id": lg.get("id"), "name": lg.get("name"), "type": lg.get("type"),
                          "round": lg.get("round"), "season": lg.get("season")}, "source": "api"}
    return None


def next_from_calendars(calendars: dict[str, dict], team: str, tonight: dict) -> dict | None:
    """The same from the season calendars (team names only, no domestic cup): the fallback."""
    from sportai.connectors.teams import same_team

    ko = _kickoff(tonight["fixture"]["date"])
    best = None
    for comp, cal in (calendars or {}).items():
        for m in (cal or {}).get("matchs") or []:
            if m["id"] == tonight["fixture"]["id"] or m["statut"] in DROPPED or _kickoff(m["coup_envoi"]) <= ko:
                continue
            home = same_team(m["domicile"], team)
            if not (home or same_team(m["exterieur"], team)):
                continue
            if best is None or _kickoff(m["coup_envoi"]) < _kickoff(best["coup_envoi"]):
                lid = CALENDAR_LEAGUES.get(comp)
                best = {"id": m["id"], "coup_envoi": m["coup_envoi"], "domicile": home,
                        "adversaire": m["exterieur" if home else "domicile"], "adversaire_id": None,
                        "ligue": {"id": lid, "name": LEAGUE_NAMES.get(lid, comp),
                                  "type": "Cup" if lid in CONTINENTAL else "League", "round": m.get("journee"),
                                  "season": cal.get("saison")}, "source": "calendrier"}
    return best


def strength(standings: list | None, team_id: int | None, name: str | None) -> tuple[dict, int] | None:
    """Rank and points per match of a team in a table (by id, else by name) with its group's position in the
    table; None when absent or too early in the season."""
    from sportai.connectors.teams import same_team

    for league in standings or []:
        for g, group in enumerate((league.get("league") or {}).get("standings") or []):
            for r in group:
                t = r.get("team") or {}
                if (team_id is not None and t.get("id") == team_id) or (
                        team_id is None and name and t.get("name") and same_team(t["name"], name)):
                    played = (r.get("all") or {}).get("played") or 0
                    if played < MIN_PLAYED:
                        return None
                    return {"rang": r["rank"], "points_par_match": round(r["points"] / played, 2)}, g
    return None


def compare(tables: list, tonight_opp: dict, next_opp: dict) -> tuple[dict | None, dict | None]:
    """Both opponents' strength from the first table holding them both in the same group."""
    for t in tables:
        a = strength(t, tonight_opp.get("id"), tonight_opp.get("name"))
        b = strength(t, next_opp.get("id"), next_opp.get("name"))
        if a and b and a[1] == b[1]:
            return a[0], b[0]
    return None, None


def _after(n: int) -> str:
    return "le lendemain" if n == 1 else f"{n} jours après"


def next_match(team: dict, tonight_opp: dict, fx: dict, nxt: dict | None, tonight_importance: int,
               tables: list, next_stake: dict | None = None) -> dict | None:
    """What the team plays next and whether it may hold back tonight.

    Risk when the next match is at most ROTATION_DAYS days after tonight's (betting days) and either counts more
    (importance) or is against a clearly stronger opponent (STRONGER_PPM more points per match in the same table)
    in a match that counts at least as much as tonight's: "élevé" when the more important match is at most
    HIGH_DAYS days away or both reasons hold, "modéré" otherwise."""
    if not nxt:
        return None
    ko, ko2 = _kickoff(fx["fixture"]["date"]), _kickoff(nxt["coup_envoi"])
    days = (_betting_day(ko2) - _betting_day(ko)).days
    imp = importance(nxt["ligue"], next_stake)
    a, b = compare(tables, tonight_opp, {"id": nxt.get("adversaire_id"), "name": nxt["adversaire"]})
    stronger = None if not (a and b) else b["points_par_match"] - a["points_par_match"] >= STRONGER_PPM
    more = imp > tonight_importance
    reasons = []
    if days <= ROTATION_DAYS:
        if more:
            reasons.append("match plus important")
        if stronger and imp >= tonight_importance:
            reasons.append("adversaire plus fort")
    risk = None
    if reasons:
        risk = "élevé" if more and (days <= HIGH_DAYS or len(reasons) == 2) else "modéré"
    comp = LEAGUE_NAMES.get(nxt["ligue"].get("id")) or nxt["ligue"].get("name") or "autre compétition"
    out = {"equipe": team["name"], "date": ko2.isoformat(), "jours": days,
           "heures": round((ko2 - ko).total_seconds() / 3600), "adversaire": nxt["adversaire"],
           "lieu": "domicile" if nxt["domicile"] else "exterieur", "competition": comp,
           "tour": nxt["ligue"].get("round"), "importance": imp, "importance_ce_soir": tonight_importance,
           "plus_important": more, "adversaire_plus_fort": stronger,
           "force_adversaire_ce_soir": a, "force_adversaire_suivant": b,
           "risque_rotation": risk, "raisons": reasons, "source": nxt["source"]}
    if risk:
        out["phrase"] = (f"{team['name']} joue {nxt['adversaire']} {_after(days)} ({comp}, "
                         f"{', '.join(reasons)}) : risque de rotation {risk}.")
    return out


# ---------------------------------------------------------------- the whole context of one match

def phrases(ctx: dict, home: str, away: str) -> list[str]:
    out = []
    if (ctx.get("arbitre") or {}).get("phrase"):
        out.append(ctx["arbitre"]["phrase"])
    for side, name in (("domicile", home), ("exterieur", away)):
        s = stakes_sentence(name, (ctx.get("classement") or {}).get(side))
        if s:
            out.append(s)
    for side in ("domicile", "exterieur"):
        out += ((ctx.get("series") or {}).get(side) or {}).get("phrases") or []
    if (ctx.get("meteo") or {}).get("phrase"):
        out.append(ctx["meteo"]["phrase"])
    for side in ("domicile", "exterieur"):
        p = ((ctx.get("prochain_match") or {}).get(side) or {}).get("phrase")
        if p:
            out.append(p)
    return out


class ContextSources:
    """What one pass needs to build the context of its matches; each source fails on its own."""

    def __init__(self, api, now: datetime, referees: dict | None = None, geo=None, meteo=None,
                 calendars: dict | None = None):
        self.api, self.now = api, now
        self.referees = referees if referees is not None else load_referee_index()
        if geo is None or meteo is None:
            from sportai.connectors.http import CachedClient
            geo = geo or CachedClient("https://geocoding-api.open-meteo.com", timeout=20.0)
            meteo = meteo or CachedClient("https://api.open-meteo.com", timeout=15.0)
        self.geo, self.meteo = geo, meteo
        self._standings: dict[tuple[int, int], list | None] = {}
        self._next: dict[int, list | None] = {}
        self._calendars = calendars  # data/calendrier, read when API-Football does not give a next match
        self.weather_down = False

    def next_fixtures(self, team_id: int) -> list | None:
        """The team's next two matches (raw), one request per team and pass; None when API-Football fails."""
        if team_id not in self._next:
            try:
                self._next[team_id] = self.api.team_next(team_id, 2)
            except Exception:  # noqa: BLE001 - context only: the calendars take over
                self._next[team_id] = None
        return self._next[team_id]

    def calendars(self) -> dict:
        if self._calendars is None:
            from sportai.services import calendrier

            self._calendars = {}
            for comp in CALENDAR_LEAGUES:
                try:
                    self._calendars[comp] = calendrier.load(comp) or {}
                except (OSError, ValueError):
                    self._calendars[comp] = {}
        return self._calendars

    def standings(self, league_id: int, season: int) -> list | None:
        key = (league_id, season)
        if key not in self._standings:
            try:
                self._standings[key] = self.api.standings(league_id, season)
            except Exception:  # noqa: BLE001 - context only
                self._standings[key] = None
        return self._standings[key]


def next_matches(src: ContextSources, fx: dict, tonight_table: list | None, tonight_stakes: dict) -> dict:
    """Both teams' next matches with their rotation risk; a side not found is left out."""
    league = fx.get("league") or {}
    out = {}
    for side, me, opp in (("domicile", "home", "away"), ("exterieur", "away", "home")):
        team, other = fx["teams"][me], fx["teams"][opp]
        raw = src.next_fixtures(team["id"])
        nxt = next_from_fixtures(raw, team["id"], fx) if raw is not None else None
        if nxt is None:
            nxt = next_from_calendars(src.calendars(), team["name"], fx)
        if nxt is None:
            continue
        lg = nxt["ligue"]
        table = None
        if lg.get("id") and lg.get("season") and (is_league(lg) or lg.get("id") in CONTINENTAL):
            table = src.standings(lg["id"], lg["season"])
        stake = stakes(table, team["id"]) if table and is_league(lg) else None
        info = next_match(team, other, fx, nxt, importance(league, tonight_stakes.get(side)),
                          [table, tonight_table], stake)
        if info:
            out[side] = info
    return out


def build_context(src: ContextSources, fx: dict, previous: dict | None = None,
                  last_home: list[dict] | None = None, last_away: list[dict] | None = None,
                  next_refresh: bool = True) -> dict:
    """The context of one raw API-Football fixture; what an earlier pass found is kept when a source fails.
    next_refresh: look the teams' next matches up again (one request per team), else keep the earlier pass's."""
    prev = dict(previous or {})
    ctx: dict = {"maj": src.now.isoformat()}
    home, away = fx["teams"]["home"], fx["teams"]["away"]
    referee = fx["fixture"].get("referee")
    ctx["arbitre"] = referee_profile(src.referees, referee) if referee else prev.get("arbitre")
    league = fx.get("league") or {}
    table = src.standings(league.get("id"), league.get("season")) if league.get("id") else None
    cup = league.get("type") == "Cup" or league.get("id") in (2, 3, 848)
    if table:
        ctx["classement"] = {"domicile": stakes(table, home["id"], cup), "exterieur": stakes(table, away["id"], cup)}
        if not any(ctx["classement"].values()):
            ctx.pop("classement")
    if "classement" not in ctx and prev.get("classement"):
        ctx["classement"] = prev["classement"]
    if last_home is not None and last_away is not None:
        ctx["series"] = {"domicile": series(last_home, home["id"], home["name"]),
                         "exterieur": series(last_away, away["id"], away["name"])}
    elif prev.get("series"):
        ctx["series"] = prev["series"]
    ctx["prochain_match"] = prev.get("prochain_match")
    if next_refresh:
        try:
            found = next_matches(src, fx, table, ctx.get("classement") or {})
        except Exception:  # noqa: BLE001 - context only: keep the earlier pass's
            found = {}
        old = prev.get("prochain_match") or {}
        both = {side: found.get(side) or old.get(side) for side in ("domicile", "exterieur")}
        ctx["prochain_match"] = {k: v for k, v in both.items() if v} or None
    ctx["meteo"] = prev.get("meteo")
    if not src.weather_down:
        try:
            pos = stadium_position(src.geo, (fx["fixture"].get("venue") or {}), league.get("country"))
            ko = datetime.fromisoformat(fx["fixture"]["date"].replace("Z", "+00:00"))
            if pos:
                ctx["meteo"] = weather_at(src.meteo, pos, ko) or ctx["meteo"]
        except Exception:  # noqa: BLE001 - Open-Meteo unreachable: say it once per pass, keep the last forecast
            src.weather_down = True
    ctx = {k: v for k, v in ctx.items() if v is not None}
    ctx["phrases"] = phrases(ctx, home["name"], away["name"])
    ctx["note"] = ("Contexte affiché pour l'analyse, pas encore pris en compte dans les probabilités. Seul un risque "
                   "de rotation élevé compte : il écarte des idées les paris qui demandent à cette équipe de gagner "
                   "ou de marquer beaucoup.")
    return ctx
