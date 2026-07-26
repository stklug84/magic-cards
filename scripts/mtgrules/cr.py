"""Comprehensive Rules toolchain.

Parses the Magic: The Gathering Comprehensive Rules text file
(MagicCompRules-*.txt) into a machine-usable index:

  * numbered rules   ("100.", "100.1.", "100.1a", "704.5g", ...)
  * worked examples  ("Example: ..." passages, attached to their rule)
  * the glossary     (term -> definition)

and provides the @rule decorator used throughout the rules engine to
annotate every function/class with the CR rules it implements. The
annotations feed the conformance coverage report (`coverage_report`),
which lists implemented vs merely cited rules, so no rule is ever
silently skipped: what the engine does is auditable against the exact
text the annotation points to.

The CR is normative prose for humans; it cannot be compiled. This module
therefore never *interprets* rule text - it only indexes it for
traceability, tests, and error messages.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable

_REPO = Path(__file__).resolve().parent.parent.parent

_RULE_RE = re.compile(r"^(\d{3}(?:\.\d+[a-z]?)?)\.?\s+(.*)$")
_SECTION_RE = re.compile(r"^(\d)\. (.+)$")

_T = TypeVar("_T")


@dataclass
class Rule:
    """One numbered CR rule with its attached worked examples."""

    number: str  # "704.5g" (no trailing dot)
    text: str
    examples: list[str] = field(default_factory=list)


class ComprehensiveRules:
    """Index over one CR text file."""

    def __init__(self, path: Path | str | None = None) -> None:
        """Parse *path* (default: the newest CR file in the repo root)."""
        if path is None:
            candidates = sorted(_REPO.glob("MagicCompRules-*.txt"))
            if not candidates:
                msg = "no MagicCompRules-*.txt in repo"
                raise FileNotFoundError(msg)
            path = candidates[-1]
        self.path = Path(path)
        self.rules: dict[str, Rule] = {}
        self.glossary: dict[str, str] = {}
        self._parse()

    def _parse(self) -> None:
        """Split the file into rules body and glossary and parse both."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        # the rules body runs from "1. Game Concepts" up to the second
        # "Glossary" line (the first is the table of contents entry)
        glossary_hits = [i for i, ln in enumerate(lines) if ln.strip() == "Glossary"]
        body_end = glossary_hits[1] if len(glossary_hits) > 1 else len(lines)
        self._parse_rules(lines[:body_end])
        self._parse_glossary(lines[body_end + 1 :])

    def _parse_rules(self, lines: list[str]) -> None:
        """Index the numbered rules and attach their examples."""
        current: Rule | None = None
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Example: "):
                if current is not None:
                    current.examples.append(line[len("Example: ") :])
                continue
            m = _RULE_RE.match(line)
            if not m:
                continue
            number, text = m.group(1), m.group(2)
            if number in self.rules and not text:
                continue
            if number not in self.rules or len(text) > len(self.rules[number].text):
                # table-of-contents lines ("704. State-Based Actions")
                # precede the body; the longer body text wins
                parsed = Rule(number, text)
                if number in self.rules:
                    parsed.examples = self.rules[number].examples
                self.rules[number] = parsed
            current = self.rules[number]

    def _parse_glossary(self, lines: list[str]) -> None:
        """Index the glossary: a term line followed by definition lines."""
        term: str | None = None
        buf: list[str] = []
        for raw in lines:
            line = raw.strip()
            if line == "Credits":
                break
            if not line:
                if term and buf:
                    self.glossary[term] = " ".join(buf)
                term, buf = None, []
                continue
            if term is None:
                term = line
            else:
                buf.append(line)
        if term and buf:
            self.glossary[term] = " ".join(buf)

    # -- lookups ---------------------------------------------------------
    def __getitem__(self, number: str) -> Rule:
        """Look up a rule by number ("704.5g", with or without a dot)."""
        return self.rules[number.rstrip(".")]

    def __contains__(self, number: object) -> bool:
        """Whether the CR defines the given rule number."""
        return isinstance(number, str) and number.rstrip(".") in self.rules

    def text(self, number: str) -> str:
        """Return the normative text of the given rule."""
        return self[number].text

    def examples_under(self, prefix: str) -> list[tuple[str, str]]:
        """All (rule number, example text) pairs under a rule prefix."""
        out: list[tuple[str, str]] = []
        for num in sorted(self.rules):
            if (
                num == prefix
                or num.startswith(prefix.rstrip(".") + ".")
                or re.match(re.escape(prefix.rstrip(".")) + r"[a-z]", num)
            ):
                out.extend((num, ex) for ex in self.rules[num].examples)
        return out


# ---------------------------------------------------------------- @rule

#: global registry: rule number -> list of qualified implementer names
RULE_IMPLEMENTATIONS: dict[str, list[str]] = {}

#: rules explicitly declared out of scope, with a reason
UNSUPPORTED: dict[str, str] = {}


@functools.cache
def get_cr() -> ComprehensiveRules:
    """Return the process-wide CR index (parsed once, cached)."""
    return ComprehensiveRules()


def rule(*numbers: str) -> Callable[[_T], _T]:
    """Annotate a function/class with the CR rules it implements.

    Numbers are validated against the parsed CR at import time, so a typo
    or a rule renumbered by a CR update fails loudly.
    """
    cr = get_cr()
    for n in numbers:
        if n not in cr:
            msg = f"@rule: unknown CR rule {n!r}"
            raise ValueError(msg)

    def deco(obj: _T) -> _T:
        # the decorator serves both classes and functions; the registry
        # bookkeeping below is uniform attribute plumbing over either
        target = cast("Any", obj)
        qname = f"{target.__module__}.{getattr(target, '__qualname__', '?')}"
        for n in numbers:
            RULE_IMPLEMENTATIONS.setdefault(n.rstrip("."), []).append(qname)
        existing = getattr(target, "__cr_rules__", ())
        target.__cr_rules__ = tuple(existing) + tuple(n.rstrip(".") for n in numbers)
        return obj

    return deco


def unsupported(number: str, reason: str) -> None:
    """Declare a CR rule (or whole rule family) as out of scope."""
    cr = get_cr()
    if number not in cr:
        msg = f"unsupported(): unknown CR rule {number!r}"
        raise ValueError(msg)
    UNSUPPORTED[number.rstrip(".")] = reason


def coverage_report(prefixes: tuple[str, ...] = ()) -> str:
    """Human-readable implemented / unsupported / unclaimed report.

    *prefixes* restricts the report to rule families the engine claims
    (e.g. ("117", "601", "704")). For each top-level rule in a claimed
    family, sub-rules are grouped as implemented (with implementers),
    declared unsupported (with reason), or unclaimed.
    """
    cr = get_cr()
    lines = [
        f"CR file: {cr.path.name}  ({len(cr.rules)} rules parsed)",
        f"implemented rule annotations: {len(RULE_IMPLEMENTATIONS)}",
        f"declared unsupported: {len(UNSUPPORTED)}",
        "",
    ]
    for prefix in prefixes:
        nums = [
            n for n in sorted(cr.rules) if n == prefix or n.startswith(prefix + ".")
        ]
        impl = [n for n in nums if n in RULE_IMPLEMENTATIONS]
        unsup = [n for n in nums if n in UNSUPPORTED]
        rest = [
            n for n in nums if n not in RULE_IMPLEMENTATIONS and n not in UNSUPPORTED
        ]
        title = cr.rules.get(prefix)
        lines.append(
            f"--- {prefix}. {title.text if title else ''} "
            f"[{len(impl)} implemented / {len(unsup)} unsupported / "
            f"{len(rest)} unclaimed of {len(nums)}]",
        )
        lines.extend(
            f"  OK   {n}  <- {', '.join(RULE_IMPLEMENTATIONS[n])}" for n in impl
        )
        lines.extend(f"  SKIP {n}  ({UNSUPPORTED[n]})" for n in unsup)
        lines.extend(f"  ..   {n}" for n in rest)
        lines.append("")
    return "\n".join(lines)


#: rule families the engine claims to cover (used by the coverage report)
CLAIMED_FAMILIES = (
    "117",
    "302",
    "400",
    "405",
    "500",
    "502",
    "503",
    "504",
    "505",
    "508",
    "509",
    "510",
    "513",
    "514",
    "601",
    "602",
    "603",
    "604",
    "605",
    "606",
    "608",
    "611",
    "613",
    "614",
    "615",
    "616",
    "701",
    "702",
    "704",
    "903",
)


if __name__ == "__main__":
    import sys as _sys

    cr = get_cr()
    n_examples = sum(len(r.examples) for r in cr.rules.values())
    print(  # noqa: T201 - CLI diagnostic output of `-m mtgrules.cr`
        f"{cr.path.name}: {len(cr.rules)} rules, {n_examples} examples, "
        f"{len(cr.glossary)} glossary terms",
    )
    if "coverage" in _sys.argv:
        # import the engine so @rule annotations register; running as
        # `-m mtgrules.cr` makes this file __main__, so report through the
        # package module whose registry the engine populated
        # Deliberate self-import: running as __main__ makes this file a
        # separate module object; the registry lives on the package module.
        import mtgrules.cr as _pkg_cr  # noqa: PLW0406
        from mtgrules import (
            compiler,  # noqa: F401
        )

        families = (
            "117",
            "601",
            "602",
            "603",
            "605",
            "606",
            "608",
            "611",
            "613",
            "614",
            "616",
            "704",
            "903",
            "510",
            "514",
            "502",
            "508",
            "509",
            "405",
            "116",
            "122",
        )
        print(_pkg_cr.coverage_report(families))  # noqa: T201 - CLI output
    else:
        for probe in ("100.1", "117.1", "601.2", "613.1", "704.5g", "903.10a"):
            print(f"  {probe}: {cr.text(probe)[:90]}...")  # noqa: T201
