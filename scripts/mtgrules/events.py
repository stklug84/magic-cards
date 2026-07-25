"""Game events.

Events are the unit that replacement effects (rule 614) intercept and that
triggered abilities (rule 603) watch. Every game-state mutation of interest
flows through Game.emit(), which runs the replacement machinery and then
notifies trigger watchers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class EventType:
    ZONE_CHANGE = "zone_change"  # obj, from_zone, to_zone
    ENTERS_BATTLEFIELD = "etb"  # obj  (rule 603.6a)
    DIES = "dies"  # obj  (rule 700.4)
    # spec, count, controller (r111.2); event name, not a credential
    CREATE_TOKEN = "create_token"  # noqa: S105  # nosec B105
    PUT_COUNTERS = "put_counters"  # obj, kind, count  (rule 122)
    DRAW = "draw"  # player, count     (rule 121)
    DAMAGE = "damage"  # source, target, amount (rule 120)
    GAIN_LIFE = "gain_life"  # player, amount    (rule 119.3)
    LOSE_LIFE = "lose_life"  # player, amount
    CAST = "cast"  # obj (spell)       (rule 601.2i)
    ATTACKS = "attacks"  # obj, defender     (rule 508.1)
    TAP = "tap"  # obj
    UNTAP = "untap"  # obj
    SACRIFICE = "sacrifice"  # obj               (rule 701.22)
    DESTROY = "destroy"  # obj               (rule 701.7)
    EXILE_OBJ = "exile_obj"  # obj               (rule 701.9)
    BEGIN_STEP = "begin_step"  # step, player
    END_STEP_EVT = "end_step"  # step, player
    PROLIFERATE = "proliferate"  # player            (rule 702.87)
    SHUFFLE = "shuffle"  # player            (rule 701.24)
    LAND_PLAYED = "land_played"  # obj, player       (rule 305)


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)
    #: replacement effects that already applied to this event (rule 616.2:
    #: each replacement effect applies to a given event only once)
    applied: set = field(default_factory=set)
    #: set by a replacement/prevention effect that removes the event
    prevented: bool = False

    def __getattr__(self, key):
        try:
            return self.data[key]
        except KeyError:
            raise AttributeError(key) from None

    def __repr__(self):
        return f"<Event {self.type} {self.data}>"
