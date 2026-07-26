"""Stack records: spells and abilities awaiting resolution (CR 405).

Split out of game.py so that lower-level modules (effects, abilities,
policy) can name and isinstance-check stack entries without importing the
Game machinery (restructure
instead of deferred imports).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from mtgrules.cr import rule

if TYPE_CHECKING:
    from mtgrules.abilities import (
        ActivatedAbility,
        SpellAbility,
        TriggeredAbility,
    )
    from mtgrules.events import Event
    from mtgrules.objects import GameObject, Player

#: anything a spell or ability can target (rule 115.1): a permanent, a
#: player, or a spell on the stack
type Target = GameObject | Player | StackItem


@dataclass
class PendingTrigger:
    """A triggered ability waiting to be put on the stack (rule 603.3)."""

    ability: TriggeredAbility
    source: GameObject
    controller: Player
    #: the triggering event (rule 603.10a look-back reads its object)
    event: Event


@rule("405.1", "405.2")
@dataclass
class StackItem:
    """A spell or ability on the stack (rules 405.1-405.2)."""

    #: the spell card (is_spell), the PendingTrigger (triggered ability),
    #: or the ActivatedAbility itself (activated ability)
    obj: GameObject | PendingTrigger | ActivatedAbility
    #: for abilities: their source object; for spells: the card itself
    source: GameObject
    controller: Player
    ability: SpellAbility | ActivatedAbility | TriggeredAbility | None = None
    targets: list[Target] = field(default_factory=list)
    x: int = 0
    is_spell: bool = False

    def __repr__(self) -> str:
        """Show the spell name or the ability's source name."""
        if self.is_spell:
            name = cast("GameObject", self.obj).base.name
        else:
            name = f"ability of {self.source.base.name}"
        return f"<Stack:{name}>"
