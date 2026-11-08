"""The owner's model freeze (2026-11-08 14:21Z, foot): "il faut qu'il arrête de modifier quoi que ce soit".

When MODELE_GELE (default /mnt/project-files/agent-sportif/state/modele_gele.json) exists, the football model stops
learning: the daily review's family corrections stay those saved in the file, goals-refresh no longer refits, and the
passes keep reading the goals model saved in the file whatever the day. Rules, weights and thresholds are untouched.
Remove the file to unfreeze.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT = "/mnt/project-files/agent-sportif/state/modele_gele.json"


def _read() -> dict | None:
    try:
        return json.loads(Path(os.getenv("MODELE_GELE", DEFAULT)).read_text())
    except (OSError, ValueError):
        return None


def is_frozen() -> bool:
    return _read() is not None


def frozen_corrections() -> dict | None:
    d = _read()
    return None if d is None else dict(d.get("corrections") or {})


def frozen_goals_model() -> Path | None:
    d = _read()
    if not d or not d.get("goals_model"):
        return None
    p = Path(d["goals_model"])
    return p if p.exists() else None
