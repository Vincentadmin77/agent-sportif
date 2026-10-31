"""Build or update every player's decayed goal history for the anytime goalscorer model (docs/23-buteurs-stars.md).

Reads the cached API-Football /fixtures?ids= files (events, line-ups, player statistics) and writes
/mnt/project-files/agent-sportif/state/buteurs_historique.json.gz (SPORTAI_BUTEURS_HISTORY to change it). No API
request: new finished league fixtures arrive through the daily goals refresh (agent_pass.py goals), which then
updates this history itself. By default only the files not read yet are added; --rebuild starts over.

Usage: python scripts/buteurs_historique.py [--rebuild] [--cache DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.services.buteurs import history_path, update_history  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="start from an empty history")
    ap.add_argument("--cache", help="folder of the fixtures_ids_*.json.gz files (default: the goals refresh cache)")
    ap.add_argument("--out", help="history file (default: %s)" % history_path())
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out) if a.out else None
    report = update_history(Path(a.cache) if a.cache else None, out, rebuild=a.rebuild)
    path = out or history_path()
    report.update(fichier=str(path), octets=path.stat().st_size if path.exists() else None,
                  secondes=round(time.time() - t0, 1))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
