#!/usr/bin/env python3
"""Validate all SPARQL query files (.rq) in the repository.

Checks:
  1. syntax   - every .rq file parses as a valid SPARQL 1.1 query (rdflib).
  2. prefixes - every query declares the mc: prefix with the canonical
                ontology namespace when it uses mc: terms.
  3. terms    - every mc: term referenced in a query exists in the combined
                repository graph (ontology + individuals), catching typos in
                class/property/individual names.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/validate_sparql.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rdflib import Graph
from rdflib.plugins.sparql import prepareQuery

ROOT = Path(__file__).resolve().parent.parent

MC_NS = "urn:stklug84:MagicCardsOntology:2026-02-27#"

PREFIX_RE = re.compile(r"^\s*PREFIX\s+(\w*):\s*<([^>]*)>", re.IGNORECASE | re.MULTILINE)
MC_TERM_RE = re.compile(r"\bmc:([A-Za-z_][\w-]*)")


def rq_files() -> list[Path]:
    """Return every tracked .rq file; exit 1 if the repo has none."""
    files = sorted(p for p in ROOT.rglob("*.rq") if ".git" not in p.parts)
    if not files:
        print("ERROR: no .rq files found", file=sys.stderr)  # noqa: T201 - validator FAIL output on stderr
        sys.exit(1)
    return files


def load_known_terms() -> set[str]:
    """Collect every local name in the mc: namespace used across all TTL files."""
    graph = Graph()
    for path in ROOT.rglob("*.ttl"):
        if ".git" in path.parts:
            continue
        graph.parse(path, format="turtle")
    terms: set[str] = set()
    for triple in graph:
        for node in triple:
            text = str(node)
            if text.startswith(MC_NS):
                terms.add(text[len(MC_NS) :])
    return terms


def check_query(path: Path, known_terms: set[str]) -> list[str]:
    """Run all three checks (syntax, prefixes, terms) on one query file.

    The query must parse as SPARQL 1.1, bind mc: to the canonical
    ontology namespace whenever it uses mc: terms, and only reference
    mc: local names that exist in the combined repository graph.
    """
    rel = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    # comment lines document retargeting examples (e.g. a commented-out
    # 'VALUES ?deck { mc:SomeDeck }' placeholder for deck graphs that
    # live outside this repository) - exclude them from the prefix and
    # term checks, but keep them for the syntax parse
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    errors: list[str] = []

    # --- 1. syntax ----------------------------------------------------
    try:
        prepareQuery(text)
    except Exception as exc:  # noqa: BLE001 - report any parse failure
        return [f"{rel}: syntax error: {exc}"]

    # --- 2. prefixes -------------------------------------------------
    prefixes = {m.group(1): m.group(2) for m in PREFIX_RE.finditer(code)}
    uses_mc = bool(MC_TERM_RE.search(code))
    if uses_mc and prefixes.get("mc") != MC_NS:
        errors.append(
            f"{rel}: uses mc: terms but PREFIX mc: is "
            f"{prefixes.get('mc')!r}, expected {MC_NS!r}",
        )

    # --- 3. terms exist ------------------------------------------------
    unknown = sorted({t for t in MC_TERM_RE.findall(code) if t not in known_terms})
    if unknown:
        errors.append(f"{rel}: unknown mc: term(s): {', '.join(unknown)}")
    return errors


def report(n_files: int, errors: list[str]) -> int:
    """Print the summary and FAIL lines; return the process exit code."""
    # T201+RUF100 (below): this validator's program output, consumed by
    print()  # noqa: T201
    print(f"Checked {n_files} SPARQL files.")  # noqa: T201
    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)  # noqa: T201
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)  # noqa: T201
        return 1
    print("All SPARQL checks passed.")  # noqa: T201
    return 0


def main() -> int:
    """Validate every SPARQL query file and report."""
    errors: list[str] = []
    files = rq_files()
    known_terms = load_known_terms()
    print(f"Loaded {len(known_terms)} known mc: terms from TTL files.\n")  # noqa: T201 - validator progress line

    for path in files:
        file_errors = check_query(path, known_terms)
        errors.extend(file_errors)
        if not file_errors:
            print(f"OK   {path.relative_to(ROOT)}")  # noqa: T201 - validator OK line, grepped by CI

    return report(len(files), errors)


if __name__ == "__main__":
    sys.exit(main())
