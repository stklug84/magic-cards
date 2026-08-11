"""SPARQL query checks: syntax, canonical prefix, and term existence.

Checks:
  1. syntax   - every .rq file parses as a valid SPARQL 1.1 query (rdflib).
  2. prefixes - every query that uses mc: terms binds mc: to the ontology
                namespace.
  3. terms    - every mc: term referenced exists in the combined graph
                (ontology + individuals), catching typos in class,
                property, and individual names.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rdflib import Graph
from rdflib.plugins.sparql import prepareQuery

if TYPE_CHECKING:
    from pathlib import Path

    from mtgvalidate.context import ValidationContext

PREFIX_RE = re.compile(r"^\s*PREFIX\s+(\w*):\s*<([^>]*)>", re.IGNORECASE | re.MULTILINE)
MC_TERM_RE = re.compile(r"\bmc:([A-Za-z_][\w-]*)")


def load_known_terms(ctx: ValidationContext) -> set[str]:
    """Collect every local name in the ontology namespace across all TTLs."""
    graph = Graph()
    for path in ctx.ttl_files():
        graph.parse(path, format="turtle")
    ns = ctx.ontology_iri
    return {
        str(node)[len(ns) :]
        for triple in graph
        for node in triple
        if str(node).startswith(ns)
    }


def check_query(
    ctx: ValidationContext,
    path: Path,
    known_terms: set[str],
) -> list[str]:
    """Run all three checks (syntax, prefixes, terms) on one query file.

    The query must parse as SPARQL 1.1, bind mc: to the ontology
    namespace whenever it uses mc: terms, and only reference mc: local
    names that exist in the combined graph.
    """
    rel = ctx.display(path)
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
    if uses_mc and prefixes.get("mc") != ctx.ontology_iri:
        errors.append(
            f"{rel}: uses mc: terms but PREFIX mc: is "
            f"{prefixes.get('mc')!r}, expected {ctx.ontology_iri!r}",
        )

    # --- 3. terms exist ------------------------------------------------
    unknown = sorted({t for t in MC_TERM_RE.findall(code) if t not in known_terms})
    if unknown:
        errors.append(f"{rel}: unknown mc: term(s): {', '.join(unknown)}")
    return errors


def run(ctx: ValidationContext) -> list[str]:
    """Validate every SPARQL query file and return the collected errors."""
    files = ctx.rq_files()
    if not files:
        return ["sparql: no .rq files found under the graph roots"]
    known_terms = load_known_terms(ctx)
    print(f"Loaded {len(known_terms)} known mc: terms from TTL files.\n")  # noqa: T201 - progress line

    errors: list[str] = []
    for path in files:
        file_errors = check_query(ctx, path, known_terms)
        errors.extend(file_errors)
        if not file_errors:
            print(f"OK   {ctx.display(path)}")  # noqa: T201 - validator OK line
    print(f"\nChecked {len(files)} SPARQL files.")  # noqa: T201 - validator summary line
    return errors
