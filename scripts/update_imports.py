#!/usr/bin/env python3
"""Rewrite the owl:imports block of MagicCardIndividuals.ttl.

Consumes /tmp/imports.json as written by scripts/generate_individuals.py
generate (one entry per emitted sets/*.ttl file with its ontology URN and
individual count) and replaces every per-set import line in the aggregator
ontology:

    owl:imports <urn:...> ;  # sets/<File>.ttl (<n> individuals)

The MagicCardsOntology import and all surrounding content are preserved.
SubTypeSupplement stays first (mirroring the generator's ordering); the
remaining imports are sorted by file name.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/generate_individuals.py generate
  python3 scripts/update_imports.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "MagicCardIndividuals.ttl"
IMPORTS_JSON = Path("/tmp/imports.json")

IMPORT_LINE_RE = re.compile(
    r"^    owl:imports <[^>]+> ;  # sets/\S+\.ttl \(\d+ individuals\)$",
)
ONTOLOGY_IMPORT = "    owl:imports <urn:stklug84:MagicCardsOntology:2026-02-27#> ;"
SUPPLEMENT = "SubTypeSupplement.ttl"


def import_lines(entries: list[dict]) -> list[str]:
    """Render one aggregator import line per generated set file."""
    supplement = [e for e in entries if e["file"] == SUPPLEMENT]
    sets = sorted(
        (e for e in entries if e["file"] != SUPPLEMENT),
        key=lambda e: e["file"],
    )
    return [
        f"    owl:imports <{e['urn']}> ;  # sets/{e['file']} ({e['cards']} individuals)"
        for e in supplement + sets
    ]


def main() -> int:
    """Rewrite the import block; return a process exit code."""
    if not IMPORTS_JSON.exists():
        print(
            f"ERROR: {IMPORTS_JSON} not found - run "
            "'generate_individuals.py generate' first",
            file=sys.stderr,
        )
        return 1
    entries = json.loads(IMPORTS_JSON.read_text())

    lines = MASTER.read_text().splitlines()
    kept: list[str] = []
    inserted = False
    for line in lines:
        if IMPORT_LINE_RE.match(line):
            if not inserted:
                kept.extend(import_lines(entries))
                inserted = True
            continue
        kept.append(line)

    if not inserted:
        try:
            anchor = kept.index(ONTOLOGY_IMPORT)
        except ValueError:
            print(
                f"ERROR: no import block found in {MASTER.name}",
                file=sys.stderr,
            )
            return 1
        kept[anchor + 1 : anchor + 1] = import_lines(entries)

    MASTER.write_text("\n".join(kept) + "\n")
    print(f"{MASTER.name}: {len(entries)} imports written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
