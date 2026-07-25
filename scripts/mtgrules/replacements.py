"""Replacement and prevention effects (CR 614, 615, 616).

A replacement effect watches for a particular event and completely or
partially replaces it (rule 614.1). Replacement effects apply before the
event occurs, never to an event that has already happened (614.5), and
each effect applies to a given event only once (616.2). If several would
apply, the affected object's controller (or the affected player) chooses
the order (616.1); self-replacement effects apply first (614.16a).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .cr import rule

_rep_ids = itertools.count(1)


@rule("614.1")
@dataclass
class Replacement:
    event_type: str
    #: matches(game, event) -> bool
    matches: object = None
    #: replace(game, event) -> event | None (None = event prevented, 615)
    replace: object = None
    source: object = None
    #: rule 614.16a: self-replacement effects apply before others
    self_replacement: bool = False
    #: "static" (while source on battlefield) or "floating"
    duration: str = "static"
    id: int = 0

    def __post_init__(self):
        self.id = next(_rep_ids)


class ReplacementEngine:
    def __init__(self, game):
        self.game = game
        self.floating: list[Replacement] = []
        #: stable Replacement instances per (source id, ability id) so that
        #: rule 616.2 "applies only once per event" bookkeeping works
        self._cache: dict = {}

    @rule("614.7a", "616.1")
    def _active(self):
        """All currently active replacement effects, from static abilities
        of battlefield permanents plus floating ones.
        """
        out = list(self.floating)
        for obj in self.game.battlefield_objects():
            for ab in obj.chars(self.game).abilities:
                if getattr(ab, "kind", "") == "static" and ab.replacement:
                    key = (obj.id, id(ab))
                    if key not in self._cache:
                        self._cache[key] = ab.replacement(self.game, obj)
                    out.extend(self._cache[key])
        return out

    @rule("614.5", "616.1", "616.2")
    def process(self, event):
        """Run *event* through the replacement machinery.

        Returns the (possibly modified) event, or None if a prevention
        effect removed it entirely.
        """
        while True:
            candidates = [
                r
                for r in self._active()
                if r.event_type == event.type
                and r.id not in event.applied
                and (r.matches is None or r.matches(self.game, event))
            ]
            if not candidates:
                return event
            # rule 614.16a: self-replacements first; then rule 616.1 - the
            # affected player/controller chooses; default policy picks by
            # timestamp which is deterministic and unbiased for the pool
            # (all stacked doublers are multiplicative and commute)
            selfs = [r for r in candidates if r.self_replacement]
            pool = selfs or candidates
            chooser = self._affected_player(event)
            if chooser is not None and len(pool) > 1:
                r = self.game.policy(chooser).choose_replacement(self.game, event, pool)
            else:
                r = pool[0]
            event.applied.add(r.id)
            event = r.replace(self.game, event)
            if event is None or event.prevented:
                return None

    def _affected_player(self, event):
        from .objects import Player

        who = (
            event.data.get("controller")
            or event.data.get("player")
            or event.data.get("target")
        )
        if isinstance(who, Player):
            return who
        obj = event.data.get("obj") or event.data.get("target")
        return (
            obj.controller if obj is not None and hasattr(obj, "controller") else None
        )
