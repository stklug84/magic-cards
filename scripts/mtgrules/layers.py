"""Continuous effects and the layer system - full CR 613 implementation.

Characteristics are computed by starting from each object's base (printed
or token-defined) values and applying every applicable continuous effect
in layer order (rule 613.1):

  1 copy, 2 control, 3 text, 4 type, 5 color, 6 abilities, 7 power/toughness
  (7a characteristic-defining, 7b setting, 7c modifying incl. counters,
   7d switching) - rules 613.1-613.4.

Within a layer/sublayer, effects apply in timestamp order (rule 613.7)
unless one is dependent on another (rule 613.8): a dependent effect waits
for the effect it depends on; dependency loops fall back to timestamp
order (rule 613.8c). Dependency is detected by probing (613.8a): B is
applied to a scratch copy of the state and we test whether that changes
(i) whether A applies at all / what A applies to, or (ii) what A does.

Characteristic-defining abilities (rules 604.3, 613.2) are ordinary
effects of layer 7a here; ability-adding effects of layer 6 feed a
re-collection pass so statics granted in layer 6 contribute their layer-7
effects (rule 613.6).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .cr import rule
from .objects import Characteristics, next_timestamp

_eff_ids = itertools.count(1)

#: layer order including layer-7 sublayers
LAYER_ORDER = [
    (1, ""),
    (2, ""),
    (3, ""),
    (4, ""),
    (5, ""),
    (6, ""),
    (7, "a"),
    (7, "b"),
    (7, "c"),
    (7, "d"),
]


@rule("611.1", "613.1")
@dataclass
class ContinuousEffect:
    layer: int
    sublayer: str = ""
    source: object = None
    #: applies_to(game, obj, chars) -> bool
    applies_to: object = None
    #: apply(game, obj, chars) mutates the working Characteristics
    apply: object = None
    #: None = while the source's static ability is active (rule 611.3);
    #: "end_of_turn" = until cleanup (rules 611.2a-b, 514.2)
    duration: str | None = "static"
    is_cda: bool = False  # rules 604.3 / 613.2
    timestamp: int = 0
    id: int = 0

    def __post_init__(self):
        self.id = next(_eff_ids)
        if not self.timestamp:
            # rule 613.7b: an effect from a static ability shares its
            # object's timestamp; floating effects get their own
            self.timestamp = (
                self.source.timestamp
                if self.source is not None and self.duration == "static"
                else next_timestamp()
            )


class LayerSystem:
    def __init__(self, game):
        self.game = game
        self.floating: list[ContinuousEffect] = []
        self._cache: dict[int, Characteristics] = {}
        self._tick = -1
        #: stable ContinuousEffect instances per (source id, ability id)
        self._static_cache: dict = {}

    # -- public API ------------------------------------------------------
    def characteristics(self, obj) -> Characteristics:
        if obj.zone != "battlefield":
            # objects elsewhere have their base characteristics
            # (copy effects on the stack are out of scope for the pool)
            return obj.base
        if self._tick != self.game.tick:
            self._recompute()
            self._tick = self.game.tick
        return self._cache.get(obj.id, obj.base)

    def add_floating(self, effect: ContinuousEffect):
        self.floating.append(effect)
        self.game.bump()

    @rule("514.2")
    def end_of_turn_cleanup(self):
        """'Until end of turn' effects end during cleanup (rule 514.2)."""
        self.floating = [e for e in self.floating if e.duration != "end_of_turn"]
        self.game.bump()

    # -- collection ------------------------------------------------------
    @rule("611.3", "604.2")
    def _collect_static(self, use_chars: dict | None):
        """Continuous effects from static abilities of permanents.

        *use_chars* selects which ability set to read: None -> base
        abilities; otherwise the partially-computed characteristics (used
        for the post-layer-6 re-collection pass, rule 613.6).
        """
        out = []
        for obj in self.game.battlefield_objects():
            abilities = use_chars[obj.id].abilities if use_chars else obj.base.abilities
            for ab in abilities:
                if getattr(ab, "kind", "") == "static" and ab.continuous:
                    key = (obj.id, id(ab))
                    if key not in self._static_cache:
                        self._static_cache[key] = ab.continuous(self.game, obj)
                    out.extend(self._static_cache[key])
        return out

    # -- computation -----------------------------------------------------
    @rule("613.1", "613.5")
    def _recompute(self):
        game = self.game
        objs = list(game.battlefield_objects())
        chars = {o.id: o.base.copy() for o in objs}

        effects = self._collect_static(None) + list(self.floating)

        for layer, sub in LAYER_ORDER:
            if (layer, sub) == (7, "a"):
                # rule 613.6: abilities added in layer 6 generate effects
                # in later layers; re-collect statics from computed chars
                seen = {e.id for e in effects}
                for e in self._collect_static(chars):
                    if e.id not in seen:
                        effects.append(e)
            group = [
                e for e in effects if e.layer == layer and (e.sublayer or "") == sub
            ]
            self._apply_group(group, objs, chars)
            if (layer, sub) == (7, "c"):
                self._apply_pt_counters(objs, chars)

        self._cache = chars

    @rule("613.7", "613.8")
    def _apply_group(self, group, objs, chars):
        """Apply one layer's effects: timestamp order with dependency
        handling (rules 613.7-613.8).
        """
        remaining = sorted(group, key=lambda e: (e.timestamp, e.id))
        while remaining:
            pick = None
            for cand in remaining:
                if not any(
                    self._depends(cand, other, objs, chars)
                    for other in remaining
                    if other is not cand
                ):
                    pick = cand
                    break
            if pick is None:
                # rule 613.8c: dependency loop -> timestamp order
                pick = remaining[0]
            remaining.remove(pick)
            for o in objs:
                if pick.applies_to(self.game, o, chars[o.id]):
                    pick.apply(self.game, o, chars[o.id])

    @rule("613.8a")
    def _depends(self, a, b, objs, chars) -> bool:
        """Return whether applying *b* first changes what *a* does."""
        game = self.game

        def snapshot():
            return {o.id: chars[o.id].copy() for o in objs}

        base = snapshot()
        after_b = snapshot()
        for o in objs:
            if b.applies_to(game, o, after_b[o.id]):
                b.apply(game, o, after_b[o.id])

        set1 = {o.id for o in objs if a.applies_to(game, o, base[o.id])}
        set2 = {o.id for o in objs if a.applies_to(game, o, after_b[o.id])}
        if set1 != set2:
            return True
        # "what the effect does": compare a's delta with and without b,
        # relative to the respective pre-states
        for oid in set1:
            probe1, probe2 = base[oid].copy(), after_b[oid].copy()
            o = next(o for o in objs if o.id == oid)
            a.apply(game, o, probe1)
            a.apply(game, o, probe2)
            if self._delta(base[oid], probe1) != self._delta(after_b[oid], probe2):
                return True
        return False

    @staticmethod
    def _delta(before: Characteristics, after: Characteristics) -> tuple:
        return (
            after.name != before.name and after.name,
            tuple(sorted(after.colors - before.colors)),
            tuple(sorted(before.colors - after.colors)),
            tuple(sorted(after.types ^ before.types)),
            tuple(sorted(after.subtypes ^ before.subtypes)),
            tuple(sorted(after.supertypes ^ before.supertypes)),
            (after.power or 0) - (before.power or 0),
            (after.toughness or 0) - (before.toughness or 0),
            tuple(sorted(after.keywords ^ before.keywords)),
            len(after.abilities) - len(before.abilities),
        )

    @rule("613.4", "122.1a")
    def _apply_pt_counters(self, objs, chars):
        """+1/+1 and -1/-1 counters apply in layer 7c (rule 613.4c order
        does not matter: all are additive).
        """
        for o in objs:
            ch = chars[o.id]
            if ch.power is None and ch.toughness is None:
                continue
            plus = o.counters.get("+1/+1", 0)
            minus = o.counters.get("-1/-1", 0)
            if plus or minus:
                ch.power = (ch.power or 0) + plus - minus
                ch.toughness = (ch.toughness or 0) + plus - minus
