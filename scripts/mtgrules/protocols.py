"""Shared engine type vocabulary: callback signatures and structural types.

The engine wires card behavior through small callables (layer-system
predicates, trigger conditions, replacement matchers, ability factories)
whose signatures were previously enforced only by convention. The aliases
and Protocols here pin those signatures down so both mypy and the ARG*
lint rules check every callback site against the real contract
so the signatures are enforced by the type checker.

Everything here is import-light: the aliases are PEP 695 ``type``
statements (lazily evaluated), so this module never participates in a
runtime import cycle with the engine modules it describes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from mtgrules.effects import Ctx
    from mtgrules.events import Event
    from mtgrules.game import Game
    from mtgrules.layers import ContinuousEffect
    from mtgrules.objects import Characteristics, GameObject
    from mtgrules.replacements import Replacement

#: ``applies_to(game, obj, chars)`` predicate of a continuous effect
#: (rule 611.1: which objects the effect applies to).
type CharPredicate = Callable[[Game, GameObject, Characteristics], object]

#: ``apply(game, obj, chars)`` mutator of a continuous effect: modifies the
#: working Characteristics in place (rule 613.1).
type CharMutator = Callable[[Game, GameObject, Characteristics], object]

#: ``condition(game, source, event)`` of a trigger spec (rule 603.1);
#: truthy return means the ability triggers off *event*.
type TriggerCondition = Callable[[Game, GameObject, Event], object]

#: rule 603.4 intervening-"if" clause: ``pred(game, source)``.
type InterveningIf = Callable[[Game, GameObject], object]

#: ``matches(game, event)`` predicate of a replacement effect (rule 614.1).
type EventMatcher = Callable[[Game, Event], object]

#: ``replace(game, event)`` of a replacement effect: returns the modified
#: event, or None when a prevention effect removes it (rules 614/615).
type EventReplacer = Callable[[Game, Event], Event | None]

#: static-ability factory producing continuous effects for its source
#: permanent (rule 604.2): ``factory(game, source)``.
type ContinuousFactory = Callable[[Game, GameObject], list[ContinuousEffect]]

#: static-ability factory producing replacement effects for its source
#: permanent (rule 614.7a): ``factory(game, source)``.
type ReplacementFactory = Callable[[Game, GameObject], list[Replacement]]

#: a count expression in an effect node: a plain int, the literal "x"
#: (resolved against the chosen X, rule 107.3), or ``f(game, ctx)``.
type CountValue = int | str | Callable[[Game, Ctx], int]

#: a hand-written one-shot effect body: ``fn(game, ctx)`` (rule 610.1).
type OneShot = Callable[[Game, Ctx], object]

#: engine log sink: ``log(event, **fields)`` (adapter fan-out, recorders).
type LogFn = Callable[..., None]


class CardRef(Protocol):
    """The slice of a mtgcards CardData the engine reads.

    Keeping this structural (instead of importing mtgcards) lets the test
    suites substitute minimal fakes and keeps the engine's card-data
    dependency at the adapter boundary.
    """

    name: str
    mana_cost: str
    oracle: str
    types: set[str]
    subtypes: set[str]
    supertypes: set[str]
    power: int | None
    toughness: int | None
    loyalty: int | None
    color_identity: set[str]
    #: derived/authored behavior hooks; values are heterogeneous by design
    #: (bools, counts, color sets - see mtgcards.behaviors.BEHAVIOR_KEYS)
    behavior: dict[str, Any]


class RecorderLike(Protocol):
    """The slice of a mtgviz Recorder that adapter.run_game drives.

    The game/winner parameters are deliberately ``Any``: mtgviz types its
    Recorder against its own GameLike/PlayerLike protocols rather than the
    concrete engine classes, and tying the two protocol families together
    here would couple the packages for no runtime benefit.
    """

    def attach(self, game: Any, /) -> None:  # noqa: ANN401
        """Bind the game before the first turn."""

    def on_event(self, kind: str, **kw: object) -> None:
        """Record one engine log event."""

    def finish(self, game: Any, winner: Any, reason: str, /) -> None:  # noqa: ANN401
        """Emit the end-of-game records."""
