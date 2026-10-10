"""Team name normalisation so fixtures, odds and history from different
providers can be joined. Unknown spellings go in ALIASES, never guessed."""

from __future__ import annotations

import re
import unicodedata

# Club-type prefixes and founding years ("Bologna FC 1909", "SV 07 Elversberg") are not part of the name.
_STOPWORDS = {"fc", "ac", "as", "sc", "cf", "afc", "ssc", "ogc", "osc", "rc", "stade", "olympique", "de", "the", "club", "calcio",
              "and", "sco", "aj", "es", "us", "ud", "cfc", "bc", "acf", "ss", "rcd", "tsg", "sv", "balompie", "futbol",
              "alsace"}

# normalised provider spelling -> canonical key
ALIASES: dict[str, str] = {
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "marseille": "marseille",
    "lyon": "lyonnais",
    "man city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "inter": "internazionale",
    "inter milan": "internazionale",
    "bayern munich": "bayern munchen",
    "barcelona": "barcelona",
    # Spellings seen in The Odds API and openfootball, 2026-10 (after stopwords and numbers are removed).
    "rennes": "rennais",
    "brest": "brestois",
    "racing lens": "lens",
    "internazionale milano": "internazionale",
    "athletic bilbao": "athletic",
    "rayo vallecano madrid": "rayo vallecano",
    "espanyol barcelona": "espanyol",
    "deportivo alaves": "alaves",
}


def normalise(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = " ".join(s.split())
    if s in ALIASES:
        return ALIASES[s]
    words = [w for w in s.split() if w not in _STOPWORDS and not w.isdigit()]
    s = " ".join(words) or s
    return ALIASES.get(s, s)


def same_team(a: str, b: str) -> bool:
    return normalise(a) == normalise(b)
