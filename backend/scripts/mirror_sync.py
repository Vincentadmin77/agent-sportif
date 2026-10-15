"""Keep the live loop's local copy of the page in step after another pass wrote documents.

    python scripts/mirror_sync.py OUT_DIR doc_id=version ...

Copies OUT_DIR/matchs/*.json (and pronostic/, stats/) into /tmp/claude-0/mirror and records each document's new
version (as the ArtifactData write returned it) in /tmp/claude-0/versions.json, so the next
live_cycle.sh neither reverts what that pass published nor pins a stale version.
"""

import json
import shutil
import sys
from pathlib import Path

MIRROR, VERSIONS = Path("/tmp/claude-0/mirror/matchs"), Path("/tmp/claude-0/versions.json")


def main(out: str, pins: list[str]) -> None:
    MIRROR.mkdir(parents=True, exist_ok=True)
    for p in sorted(Path(out, "matchs").glob("*.json")):
        shutil.copy(p, MIRROR / p.name)
    for sub in ("pronostic", "stats", "joueur", "chasseur"):  # "Mon pronostic" and "Résultats et statistiques", kept for the next pass
        for p in sorted(Path(out, sub).glob("*.json")):
            (MIRROR.parent / sub).mkdir(exist_ok=True)
            shutil.copy(p, MIRROR.parent / sub / p.name)
    v = json.loads(VERSIONS.read_text()) if VERSIONS.exists() else {}
    for pin in pins:
        doc, _, ver = pin.partition("=")
        v[doc] = int(ver)
    VERSIONS.write_text(json.dumps(v))
    print(f"{len(pins)} version(s) recorded")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
