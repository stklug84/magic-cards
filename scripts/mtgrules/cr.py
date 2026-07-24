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

import re
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent

_RULE_RE = re.compile(r"^(\d{3}(?:\.\d+[a-z]?)?)\.?\s+(.*)$")
_SECTION_RE = re.compile(r"^(\d)\. (.+)$")


@dataclass
class Rule:
    number: str                 # "704.5g" (no trailing dot)
    text: str
    examples: list = field(default_factory=list)


class ComprehensiveRules:
    """Index over one CR text file."""

    def __init__(self, path: Path | str | None = None):
        if path is None:
            candidates = sorted(_REPO.glob("MagicCompRules-*.txt"))
            if not candidates:
                raise FileNotFoundError("no MagicCompRules-*.txt in repo")
            path = candidates[-1]
        self.path = Path(path)
        self.rules: dict[str, Rule] = {}
        self.glossary: dict[str, str] = {}
        self._parse()

    def _parse(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        # the rules body runs from "1. Game Concepts" up to the second
        # "Glossary" line (the first is the table of contents entry)
        glossary_hits = [i for i, l in enumerate(lines) if l.strip() == "Glossary"]
        body_end = glossary_hits[1] if len(glossary_hits) > 1 else len(lines)

        current: Rule | None = None
        seen_toc: set[str] = set()
        for line in lines[:body_end]:
            line = line.strip()
            if not line:
                continue
            if line.startswith("Example: "):
                if current is not None:
                    current.examples.append(line[len("Example: "):])
                continue
            m = _RULE_RE.match(line)
            if m:
                number, text = m.group(1), m.group(2)
                if number in self.rules and not text:
                    continue
                if number not in self.rules or len(text) > len(
                        self.rules[number].text):
                    # table-of-contents lines ("704. State-Based Actions")
                    # precede the body; the longer body text wins
                    if number in self.rules:
                        seen_toc.add(number)
                    rule = Rule(number, text)
                    if number in self.rules:
                        rule.examples = self.rules[number].examples
                    self.rules[number] = rule
                current = self.rules[number]

        # glossary: term line followed by definition lines
        term = None
        buf: list[str] = []
        for line in lines[body_end + 1:]:
            line = line.strip()
            if line in ("Credits",):
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
        return self.rules[number.rstrip(".")]

    def __contains__(self, number: str) -> bool:
        return number.rstrip(".") in self.rules

    def text(self, number: str) -> str:
        return self[number].text

    def examples_under(self, prefix: str) -> list[tuple[str, str]]:
        """All (rule number, example text) pairs under a rule prefix."""
        out = []
        for num in sorted(self.rules):
            if num == prefix or num.startswith(prefix.rstrip(".") + "."):
                out.extend((num, ex) for ex in self.rules[num].examples)
            elif re.match(re.escape(prefix.rstrip(".")) + r"[a-z]", num):
                out.extend((num, ex) for ex in self.rules[num].examples)
        return out


# ---------------------------------------------------------------- @rule

#: global registry: rule number -> list of qualified implementer names
RULE_IMPLEMENTATIONS: dict[str, list[str]] = {}

#: rules explicitly declared out of scope, with a reason
UNSUPPORTED: dict[str, str] = {}

_cr_singleton: ComprehensiveRules | None = None


def get_cr() -> ComprehensiveRules:
    global _cr_singleton
    if _cr_singleton is None:
        _cr_singleton = ComprehensiveRules()
    return _cr_singleton


def rule(*numbers: str):
    """Decorator annotating a function/class with the CR rules it implements.

    Numbers are validated against the parsed CR at import time, so a typo
    or a rule renumbered by a CR update fails loudly.
    """
    cr = get_cr()
    for n in numbers:
        if n not in cr:
            raise ValueError(f"@rule: unknown CR rule {n!r}")

    def deco(obj):
        qname = f"{obj.__module__}.{getattr(obj, '__qualname__', obj.__name__)}"
        for n in numbers:
            RULE_IMPLEMENTATIONS.setdefault(n.rstrip("."), []).append(qname)
        existing = getattr(obj, "__cr_rules__", ())
        obj.__cr_rules__ = tuple(existing) + tuple(
            n.rstrip(".") for n in numbers)
        return obj
    return deco


def unsupported(number: str, reason: str) -> None:
    """Declare a CR rule (or whole rule family) as out of scope."""
    cr = get_cr()
    if number not in cr:
        raise ValueError(f"unsupported(): unknown CR rule {number!r}")
    UNSUPPORTED[number.rstrip(".")] = reason


def coverage_report(prefixes: tuple[str, ...] = ()) -> str:
    """Human-readable implemented / unsupported / unclaimed report.

    *prefixes* restricts the report to rule families the engine claims
    (e.g. ("117", "601", "704")). For each top-level rule in a claimed
    family, sub-rules are grouped as implemented (with implementers),
    declared unsupported (with reason), or unclaimed.
    """
    cr = get_cr()
    lines = [f"CR file: {cr.path.name}  ({len(cr.rules)} rules parsed)",
             f"implemented rule annotations: {len(RULE_IMPLEMENTATIONS)}",
             f"declared unsupported: {len(UNSUPPORTED)}", ""]
    for prefix in prefixes:
        nums = [n for n in sorted(cr.rules)
                if n == prefix or n.startswith(prefix + ".")]
        impl = [n for n in nums if n in RULE_IMPLEMENTATIONS]
        unsup = [n for n in nums if n in UNSUPPORTED]
        rest = [n for n in nums
                if n not in RULE_IMPLEMENTATIONS and n not in UNSUPPORTED]
        title = cr.rules.get(prefix)
        lines.append(f"--- {prefix}. {title.text if title else ''} "
                     f"[{len(impl)} implemented / {len(unsup)} unsupported / "
                     f"{len(rest)} unclaimed of {len(nums)}]")
        for n in impl:
            lines.append(f"  OK   {n}  <- {', '.join(RULE_IMPLEMENTATIONS[n])}")
        for n in unsup:
            lines.append(f"  SKIP {n}  ({UNSUPPORTED[n]})")
        for n in rest:
            lines.append(f"  ..   {n}")
        lines.append("")
    return "\n".join(lines)


#: rule families the engine claims to cover (used by the coverage report)
CLAIMED_FAMILIES = ("117", "302", "400", "405", "500", "502", "503", "504",
                    "505", "508", "509", "510", "513", "514", "601", "602",
                    "603", "604", "605", "606", "608", "611", "613", "614",
                    "615", "616", "701", "702", "704", "903")


if __name__ == "__main__":
    import sys as _sys
    cr = get_cr()
    n_examples = sum(len(r.examples) for r in cr.rules.values())
    print(f"{cr.path.name}: {len(cr.rules)} rules, {n_examples} examples, "
          f"{len(cr.glossary)} glossary terms")
    if "coverage" in _sys.argv:
        # import the engine so @rule annotations register; running as
        # `-m mtgrules.cr` makes this file __main__, so report through the
        # package module whose registry the engine populated
        from mtgrules import (abilities, adapter, combat, compiler,  # noqa
                              effects, game, layers, manasys, objects,
                              policy, replacements, turns)
        import mtgrules.cr as _pkg_cr
        families = ("117", "601", "602", "603", "605", "606", "608",
                    "611", "613", "614", "616", "704", "903", "510",
                    "514", "502", "508", "509", "405", "116", "122")
        print(_pkg_cr.coverage_report(families))
    else:
        for probe in ("100.1", "117.1", "601.2", "613.1", "704.5g",
                      "903.10a"):
            print(f"  {probe}: {cr.text(probe)[:90]}...")
