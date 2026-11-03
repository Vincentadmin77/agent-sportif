"""Phone alert as soon as Le M takes a live position (owner 2026-11-03: « c'est justement ça qui m'intéresse »).

Admin only: one push to the owner's phone through ntfy (free app « ntfy », subscribed to the house topic), with the
match, the market, the odds and the moment. Nothing is shown on the site, no bookmaker name, never an automatic bet:
the owner places the position himself if he wants it.

Standard library only, so any loop can import it (football live cycle, tennis live loop, later basket). It never
raises: a failed push is only printed and retried on the next cycle while the position is still fresh (FRESH).
Each position is pushed once (ledger in the project folder, one file per sport).

Topic: env ALERTE_M_TOPIC, else the first line of state/alerte_m_topic.txt. No topic -> nothing is sent.
Server: env ALERTE_M_SERVEUR (default https://ntfy.sh). Token: env ALERTE_M_TOKEN (ntfy account access token).
The cloud sessions share their outgoing address, and ntfy limits anonymous publishing per address (HTTP 429 seen
2026-11-03 13:52Z): a push is retried a few seconds later, then kept waiting (state/alertes_m_attente.json) and
retried at every later call while fresh. With a token, ntfy counts the owner's account instead of the address.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(os.environ.get("ALERTE_M_STATE", "/mnt/project-files/agent-sportif/state"))
FRESH = timedelta(minutes=15)  # an older position is not worth a push any more (odds have moved)
# The owner's own accounts (2026-11-03: Winamax and Betify), always listed first when they quote the bet. Betify is
# in none of our odds feeds, so it can never appear: the owner checks it himself.
MES_SITES = ("Winamax",)
ICONS = {"foot": "⚽", "basket": "🏀", "tennis": "🎾"}


def topic() -> str | None:
    t = os.environ.get("ALERTE_M_TOPIC")
    if not t:
        f = STATE / "alerte_m_topic.txt"
        t = f.read_text().strip().splitlines()[0].strip() if f.exists() and f.read_text().strip() else None
    return t or None


def _fmt_cote(c) -> str:
    return f"{c:.2f}".replace(".", ",") if isinstance(c, (int, float)) else str(c)


def message(sport: str, match: str, pari: str, cote, moment: str | None = None,
            sites: list[tuple[str, float]] | None = None) -> tuple[str, str]:
    """Title and body of the push, in French, plain. sites: French sites quoting the same bet live, best first."""
    title = f"{ICONS.get(sport, '')} Le M en direct".strip()
    body = f"{match}\n{pari} · cote {_fmt_cote(cote)}"
    if moment:
        body += f"\n{moment}"
    if sites:
        mine = [x for x in sites if x[0] in MES_SITES]
        shown = mine + [x for x in sites if x not in mine][:max(0, 3 - len(mine))]
        body += "\nOù la prendre : " + ", ".join(f"{n} {_fmt_cote(c)}" for n, c in shown)
    else:
        body += "\nOù la prendre : aucun site français ne l'affiche en ce moment (cote du flux international)"
    return title, body


def _ledger(sport: str) -> Path:
    return STATE / f"alertes_m_envoyees_{sport}.json"


def _load(sport: str) -> dict:
    try:
        return json.loads(_ledger(sport).read_text())
    except (OSError, ValueError):
        return {}


RETRY_WAITS = (3, 8, 15)  # seconds between tries when ntfy answers 429 or 5xx


def _post(t: str, title: str, body: str) -> bool:
    url = os.environ.get("ALERTE_M_SERVEUR", "https://ntfy.sh").rstrip("/") + "/" + t
    headers = {"Title": _h(title), "Priority": "high", "Tags": "stopwatch"}
    if os.environ.get("ALERTE_M_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["ALERTE_M_TOKEN"]
    for wait in (0,) + RETRY_WAITS:
        time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=body.encode(), method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                return 200 <= r.status < 300
        except urllib.error.HTTPError as e:
            print("alerte M non envoyée:", e, flush=True)
            if e.code != 429 and e.code < 500:
                return False
        except Exception as e:  # noqa: BLE001 - an alert must never break a live loop
            print("alerte M non envoyée:", e, flush=True)
    return False


def _attente() -> Path:
    return STATE / "alertes_m_attente.json"


def _save(path: Path, data: dict) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    except OSError as e:
        print("fichier des alertes M non écrit:", path.name, e, flush=True)


def relancer(now: datetime | None = None) -> int:
    """Retry the pushes kept waiting, while fresh. Called by every alerter() call; loops may call it each pass."""
    now = now or datetime.now(timezone.utc)
    try:
        wait = json.loads(_attente().read_text())
    except (OSError, ValueError):
        return 0
    t, n, left = topic(), 0, {}
    for cle, w in wait.items():
        if now - datetime.fromisoformat(w["depuis"]) > FRESH or cle in _load(w["sport"]):
            continue
        if t and _post(t, w["titre"], w["texte"] + "\n(alerte envoyée en retard)"):
            sent = _load(w["sport"])
            sent[cle] = now.isoformat()
            _save(_ledger(w["sport"]), sent)
            n += 1
        else:
            left[cle] = w
    if left != wait:
        _save(_attente(), left)
    return n


def _h(s: str) -> str:
    """HTTP headers are latin-1: ntfy decodes RFC 2047 so emojis and accents survive."""
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        import base64

        return "=?UTF-8?B?" + base64.b64encode(s.encode()).decode() + "?="


def alerter(sport: str, cle: str, match: str, pari: str, cote, moment: str | None = None,
            heure: datetime | None = None, now: datetime | None = None, sites=None) -> bool:
    """Push one live position of Le M once. `cle` identifies it (day + match + pari). True when sent now."""
    now = now or datetime.now(timezone.utc)
    relancer(now)
    if heure is not None and heure.tzinfo is None:
        heure = heure.replace(tzinfo=timezone.utc)
    if heure is not None and now - heure > FRESH:
        return False
    t = topic()
    if not t:
        return False
    sent = _load(sport)
    if cle in sent:
        return False
    if callable(sites):  # looked up only when the push will really go (costs Odds API credits)
        try:
            sites = sites()
        except Exception as e:  # noqa: BLE001
            print("sites de l'alerte M introuvables:", e, flush=True)
            sites = None
    title, body = message(sport, match, pari, cote, moment, sites)
    if not _post(t, title, body):
        try:
            wait = json.loads(_attente().read_text())
        except (OSError, ValueError):
            wait = {}
        wait.setdefault(cle, {"sport": sport, "titre": title, "texte": body, "depuis": (heure or now).isoformat()})
        _save(_attente(), wait)
        return False
    sent[cle] = now.isoformat()
    _save(_ledger(sport), sent)
    return True


def alerter_foot(doc: dict, before: list[dict], after: list[dict], now: datetime | None = None,
                 doc_id: str | None = None) -> int:
    """New entries of a football doc's live_a_blanc list after one live cycle -> one push each."""
    seen = {n.get("pari") for n in before or []}
    n_sent = 0
    for n in after or []:
        if n.get("pari") in seen:
            continue
        try:
            heure = datetime.fromisoformat(n["heure"]) if n.get("heure") else None
        except ValueError:
            heure = None
        match = f"{doc.get('domicile', '?')} - {doc.get('exterieur', '?')}"
        sc = str(doc.get("score") or "").replace(" ", "")
        moment = f"{n.get('minute')}e minute" + (f", score {sc}" if sc else "")
        cle = f"{doc.get('jour') or (heure.date().isoformat() if heure else '')}|{doc_id or match}|{n.get('pari')}"
        n_sent += alerter("foot", cle, match, n.get("pari"), n.get("cote"), moment, heure, now,
                          sites=lambda n=n: sites_foot(doc, n.get("pari")))
    return n_sent


# --- Where to take it: French sites quoting the same bet live (owner 2026-11-03: « avec le site où la prendre »).
# The live feeds the M reads (API-Football /odds/live, api-tennis get_live_odds) do not say which bookmaker quotes,
# so the French sites are read from The Odds API, region fr, at the moment of the push only (1 credit a market a
# league: about 2 credits an alert in football, 1 a tournament in tennis). Admin push only, never on the site.
ODDS_API = "https://api.the-odds-api.com/v4/sports"


def _odds_api(path: str, **params) -> list:
    import urllib.parse

    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        return []
    q = urllib.parse.urlencode({"apiKey": key, "oddsFormat": "decimal", **params})
    with urllib.request.urlopen(f"{ODDS_API}{path}?{q}", timeout=10) as r:
        try:
            left = r.headers.get("x-requests-remaining")
            if left is not None:
                (STATE / "odds_credits.txt").write_text(f"{left} credits left (alerte M {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ})\n")
        except OSError:
            pass
        return json.loads(r.read())


def _quotes(event: dict, want) -> list[tuple[str, float]]:
    """(site, odds) of every French bookmaker of the event whose outcome passes want(market_key, outcome)."""
    out = {}
    for b in event.get("bookmakers") or []:
        for mk in b.get("markets") or []:
            for oc in mk.get("outcomes") or []:
                if want(mk.get("key"), oc) and isinstance(oc.get("price"), (int, float)):
                    name = str(b.get("title") or b.get("key")).replace(" (FR)", "")
                    out[name] = max(out.get(name, 0), float(oc["price"]))
    return sorted(out.items(), key=lambda x: -x[1])


def sites_foot(doc: dict, pari: str) -> list[tuple[str, float]]:
    from sportai.connectors.teams import same_team
    from sportai.connectors.the_odds_api import SPORT_KEYS
    from sportai.services.dashboard import COMPETITION_NAMES
    from sportai.services.revue import _keys

    home, away = doc.get("domicile") or "", doc.get("exterieur") or ""
    key = _keys(home, away).get(pari)
    code = {v: k for k, v in COMPETITION_NAMES.items()}.get(doc.get("competition"), doc.get("competition"))
    if key is None or code not in SPORT_KEYS or key.market not in ("1x2", "totals"):
        return []
    events = _odds_api(f"/{SPORT_KEYS[code]}/odds", regions="fr", markets="h2h" if key.market == "1x2" else "totals")
    ev = next((e for e in events if same_team(e.get("home_team", ""), home) and same_team(e.get("away_team", ""), away)), None)
    if ev is None:
        return []

    def want(mk, oc):
        if key.market == "1x2" and mk == "h2h":
            name = oc.get("name", "")
            return (key.selection == "draw" and name == "Draw") or \
                (key.selection == "home" and same_team(name, ev["home_team"]) and name != "Draw") or \
                (key.selection == "away" and same_team(name, ev["away_team"]) and name != "Draw")
        return mk == "totals" and str(oc.get("name", "")).lower() == key.selection and \
            oc.get("point") is not None and abs(float(oc["point"]) - float(key.line)) < 1e-6

    return _quotes(ev, want)


def _last(name: str) -> str:
    import unicodedata

    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace(".", " ")
    return n.split()[-1] if n.split() else n


def sites_tennis(j1: str, j2: str, cle: list) -> list[tuple[str, float]]:
    """French sites for a tennis winner position (the only tennis market region fr reliably quotes)."""
    if not cle or cle[0] != "vainqueur":
        return []
    who = j1 if cle[1] == "Home" else j2
    sports = [s["key"] for s in _odds_api("", all="false") if str(s.get("key", "")).startswith("tennis_")]
    for sk in sports:
        for ev in _odds_api(f"/{sk}/odds", regions="fr", markets="h2h"):
            names = {_last(ev.get("home_team", "")), _last(ev.get("away_team", ""))}
            if names == {_last(j1), _last(j2)}:
                return _quotes(ev, lambda mk, oc: mk == "h2h" and _last(oc.get("name", "")) == _last(who))
    return []


def alerter_tennis(day: str, row: dict, now: datetime | None = None) -> bool:
    """Push the M's live tennis position of a tennis/<date>.json row (matchs[].m_direct), once."""
    d = row.get("m_direct") or {}
    if not d.get("position"):
        return False
    match = f"{row.get('joueur_1', '?')} - {row.get('joueur_2', '?')}"
    moment = f"score {d['score_au_moment']}" if d.get("score_au_moment") else None
    return alerter("tennis", f"{day}|{row.get('id')}|{d['position']}", match, d["position"], d.get("cote"), moment,
                   None, now, sites=lambda: sites_tennis(row.get("joueur_1", ""), row.get("joueur_2", ""), d.get("cle")))
