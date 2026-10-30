"""The "agent mathématicien": positions registry and internal report (docs/22-agent-mathematicien.md).

    python scripts/mathematicien.py registre --page PAGE --out OUT [--jour 2026-10-29]
    python scripts/mathematicien.py importer --page PAGE --out OUT     # rebuilt history, flagged "recalcule"
    python scripts/mathematicien.py rapport --page PAGE [--out FILE]

PAGE holds the page's collections as ArtifactData `list` saves them (matchs/, pronostic/, registre/, and
basket_pronostic/ and basket_matchs/ for the basketball opinions and the M's choice, tennis/ for the tennis ones). `registre` and
`importer` write OUT/registre/<jour>.json, to write in the page's `registre` collection (doc_id = the day). No API call.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportai.services import registre as R  # noqa: E402


def _load(folder: str, name: str) -> dict[str, dict]:
    d = Path(folder) / name
    if not d.exists():
        return {}
    docs = {p.stem: json.loads(p.read_text()) for p in d.glob("*.json")}
    return {k: v.get("data", v) if isinstance(v.get("data"), dict) else v for k, v in docs.items()}


def _code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "inconnue"


def _write(out: str, docs: dict[str, dict]) -> None:
    d = Path(out) / "registre"
    d.mkdir(parents=True, exist_ok=True)
    for day, doc in docs.items():
        (d / f"{day}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print(json.dumps({"registre": sorted(docs)}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("registre", "importer", "rapport", "realigner", "selection-passee"))
    ap.add_argument("--page", required=True)
    ap.add_argument("--out")
    ap.add_argument("--jour")
    ap.add_argument("--motif", help="realigner: why the owner had the day's official list rewritten")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    prons, regs = _load(a.page, "pronostic"), _load(a.page, "registre")
    if a.cmd == "registre":
        matchs = _load(a.page, "matchs")
        days = [a.jour] if a.jour else [d for d, p in prons.items() if not p.get("recalcule")]
        bprons, bmatchs, version = _load(a.page, "basket_pronostic"), _load(a.page, "basket_matchs"), _code_version()
        tdocs = _load(a.page, "tennis")
        _write(a.out, {d: R.selection_m(R.tennis_avis(R.basket_avis(R.update(regs.get(d) or {}, prons[d], matchs, now, version),
                                                                    bprons.get(d) or {}, now, bmatchs, version),
                                                      tdocs.get(d), now, version),
                                        prons[d], now, bprons.get(d), tdocs.get(d))
                       for d in days if d in prons and not prons[d].get("recalcule")})
    elif a.cmd == "realigner":  # a day rewritten on the owner's request: replaced records kept, flagged
        matchs, bprons = _load(a.page, "matchs"), _load(a.page, "basket_pronostic")
        _write(a.out, {a.jour: R.realign(regs.get(a.jour) or {}, prons[a.jour], matchs, now, _code_version(), a.motif,
                                         bprons.get(a.jour))})
    elif a.cmd == "selection-passee":  # the selection the past days would have had, flagged recalcule
        bprons = _load(a.page, "basket_pronostic")
        days = [a.jour] if a.jour else [d for d in prons if d < now.date().isoformat()]
        _write(a.out, {d: {**(regs.get(d) or {"jour": d, "fiches": {}}),
                           "selection_m": R.rebuilt_selection(prons[d], bprons.get(d), regs.get(d)),
                           "selection_m_recalculee": True} for d in days})
    elif a.cmd == "importer":
        _write(a.out, {d: R.import_rebuilt(p) for d, p in prons.items()
                       if p.get("recalcule") and not (regs.get(d) or {}).get("fiches")})
    else:
        text = json.dumps(R.report(list(regs.values())), ensure_ascii=False, indent=1)
        Path(a.out).write_text(text) if a.out else print(text)


if __name__ == "__main__":
    main()
