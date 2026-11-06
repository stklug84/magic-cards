"""Canonical dump of the compiled abilities (parity level L3).

Loads a graph root through :class:`mtgcards.database.CardDatabase`, runs
every distinct card record through :func:`mtgrules.compiler.compile_card`
(overrides included) and writes one JSON line per record, in index
order, describing the resulting ``Characteristics`` structurally: the
kind, costs, targets and effect tree of every ability, the keywords and
cost modifiers, the clauses the compiler could not read and the
override's simplification note. Closures are reduced to their presence
(``true``) or, for dynamic counts, to the tag ``"dynamic"``. The Rust
``mtgrules`` crate has the same dump
(``cargo run -p mtgrules --example dump_abilities``); ``cargo xtask
parity`` runs both and diffs them line by line.

Usage (from ``scripts/``)::

    python3 -m parity.dump_abilities --root .. [--custom-cards F] [--extra-graph G ...]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgcards.database import CardDatabase
from mtgrules import compiler, overrides
from mtgrules.abilities import (
    ActivatedAbility,
    SpellAbility,
    StaticAbility,
    TargetSpec,
    TokenSpec,
    TriggeredAbility,
)
from mtgrules.effects import Effect

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from mtgrules.abilities import Ability
    from mtgrules.objects import Characteristics


def _sorted(values: Iterable[object]) -> list[str]:
    return sorted(str(v) for v in values)


def _count(value: object) -> object:
    """Return a CountValue as an int, the literal ``x``, or ``dynamic``."""
    if callable(value):
        return "dynamic"
    return value


def _token(spec: TokenSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "power": spec.power,
        "toughness": spec.toughness,
        "colors": _sorted(spec.colors),
        "types": _sorted(spec.types),
        "subtypes": _sorted(spec.subtypes),
        "keywords": _sorted(spec.keywords),
        "tapped": spec.tapped,
        "predefined": spec.predefined,
        "abilities": len(spec.abilities),
    }


def _value(value: object) -> object:
    if isinstance(value, Effect):
        return effect_tree(value)
    if isinstance(value, TokenSpec):
        return _token(value)
    if isinstance(value, (set, frozenset)):
        return _sorted(value)
    if isinstance(value, (list, tuple)):
        return [_value(v) for v in value]
    if callable(value):
        return True
    return value


def effect_tree(effect: Effect) -> dict[str, Any]:
    """Return the effect node: its class name, then its fields in order."""
    node: dict[str, Any] = {"node": type(effect).__name__}
    # every concrete Effect is a dataclass; the base class is not typed as one
    for f in dataclasses.fields(effect):  # type: ignore[arg-type]
        raw = getattr(effect, f.name)
        if f.name in ("count", "amount") and type(effect).__name__ != "AddMana":
            node[f.name] = _count(raw)
        else:
            node[f.name] = _value(raw)
    return node


def _target(spec: TargetSpec) -> dict[str, Any]:
    return {
        "what": spec.what,
        "controller": spec.controller,
        "count": spec.count,
        "optional": spec.optional,
        "other": spec.other,
        "min_count": spec.min_count,
    }


def ability_record(ability: Ability) -> dict[str, Any]:
    """One ability as a flat record with a ``kind`` tag."""
    if isinstance(ability, ActivatedAbility):
        return {
            "kind": "activated",
            "mana_cost": ability.mana_cost,
            "tap_cost": ability.tap_cost,
            "sac_cost": ability.sac_cost,
            "life_cost": ability.life_cost,
            "loyalty_cost": ability.loyalty_cost,
            "effect": effect_tree(ability.effect) if ability.effect else None,
            "targets": [_target(t) for t in ability.targets],
            "is_mana_ability": ability.is_mana_ability,
            "sorcery_only": ability.sorcery_only,
            "once_per_turn": ability.once_per_turn,
            "from_hand": ability.from_hand,
            "from_graveyard": ability.from_graveyard,
            "text": ability.text,
        }
    if isinstance(ability, TriggeredAbility):
        return {
            "kind": "triggered",
            "event": ability.trigger.event,
            "condition": ability.trigger.condition is not None,
            "step": ability.trigger.step,
            "effect": effect_tree(ability.effect),
            "targets": [_target(t) for t in ability.targets],
            "intervening_if": ability.intervening_if is not None,
            "text": ability.text,
            "optional": ability.optional,
            "once_each_turn": ability.once_each_turn,
        }
    if isinstance(ability, SpellAbility):
        return {
            "kind": "spell",
            "effect": effect_tree(ability.effect),
            "targets": [_target(t) for t in ability.targets],
            "text": ability.text,
        }
    if isinstance(ability, StaticAbility):
        return {
            "kind": "static",
            "continuous": ability.continuous is not None,
            "replacement": ability.replacement is not None,
            "text": ability.text,
            "uncounterable_spells": ability.uncounterable_spells,
            "enters_tapped": ability.enters_tapped,
        }
    msg = f"unknown ability kind: {type(ability).__name__}"
    raise TypeError(msg)


def card_record(ch: Characteristics) -> dict[str, Any]:
    """Return the compiled characteristics of one card as a record."""
    return {
        "name": ch.name,
        "mana_cost": ch.mana_cost,
        "colors": _sorted(ch.colors),
        "supertypes": _sorted(ch.supertypes),
        "types": _sorted(ch.types),
        "subtypes": _sorted(ch.subtypes),
        "power": ch.power,
        "toughness": ch.toughness,
        "loyalty": ch.loyalty,
        "keywords": _sorted(ch.keywords),
        "cost_less_per_creature": ch.cost_less_per_creature,
        "additional_cost": ch.additional_cost,
        "etb_x_counters": ch.etb_x_counters,
        "abilities": [ability_record(a) for a in ch.abilities],
        "unknown": sorted(compiler.UNKNOWN_CLAUSES.get(ch.name, ())),
        "note": overrides.NOTES.get(ch.name),
    }


def dump_lines(db: CardDatabase) -> Iterator[str]:
    """Yield one line per distinct card record, in index order."""
    seen: set[int] = set()
    for card in db.index.values():
        if id(card) in seen:
            continue
        seen.add(id(card))
        compiler.UNKNOWN_CLAUSES.pop(card.name, None)
        ch = compiler.compile_card(card)
        yield json.dumps(card_record(ch))


def main(argv: list[str] | None = None) -> int:
    """Write the dump to stdout (or ``--out``); 0 on success."""
    parser = argparse.ArgumentParser(
        description="Canonical dump of the compiled abilities (parity level L3).",
    )
    parser.add_argument(
        "--root", default="..", help="graph root (default: the checkout)"
    )
    parser.add_argument(
        "--custom-cards", help="a --custom-cards JSON file to layer on top"
    )
    parser.add_argument(
        "--extra-graph",
        action="append",
        default=[],
        help="an extra graph directory (repeatable)",
    )
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)
    db = CardDatabase(
        Path(args.root), args.custom_cards, [Path(g) for g in args.extra_graph]
    )
    text = "".join(f"{line}\n" for line in dump_lines(db))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
