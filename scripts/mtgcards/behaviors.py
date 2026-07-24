"""Behavior hooks, loaded from the knowledge graph.

Oracle text cannot be executed, so cards whose effect matters to the
simulation get explicit behavior hooks. These are stored as simulation
annotations in MagicSimulationAnnotations.ttl (:hasBehaviorHook /
:threatWeight, see MagicCardsOntology.ttl) and MERGE OVER anything the
oracle parser derived (hooks win). Cards without hooks run on
parser-derived behavior alone; cards with neither are inert bodies/spells.

Behavior key reference (BEHAVIOR_KEYS) - also the whitelist for
:behaviorKey values and --custom-cards JSON:

  mana/ramp     rock_mana, rock_colors, ramp_lands, fetch_land, enters_tapped,
                land_colors, treasures_per_turn, burst_treasures
  draw          draw_cards, draw_per_turn, draw_on_attack, draw_on_tokens
  interaction   removal, removal_scope(creature|art_ench|cre_ench|any),
                removal_exile, removal_lock, removal_targets, counterspell,
                protect, wipe{style: damage|destroy|counters|select, dmg, x}
  tokens        tokens_per_turn, etb_tokens(n,p,t,art), death_tokens,
                burst_tokens, populate_per_turn, replicate, doubler,
                creature_token_mult, esix, anim, reef, mechanized_wincon
  counters      mass_counters, single_counters, etb_counter_wipe,
                etb_target_counters, proliferate, token_per_counter,
                drain_on_counters, chain(blowfly), kulrath_lock, steal,
                grave_rob, yawgmoth, clamp
  drains        drain_own, drain_any, burst_drain
  misc          hexproof_grant, etb_removal, blink_on_etb, closet, station,
                rebuild, tutor, recursion, energy_gain, energy_thopter,
                manufactor, key(threat priority weight, from :threatWeight)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BEHAVIOR_KEYS = {
    "rock_mana", "rock_colors", "ramp_lands", "fetch_land", "enters_tapped",
    "land_colors", "treasures_per_turn", "burst_treasures", "draw_cards",
    "draw_per_turn", "draw_on_attack", "draw_on_tokens", "removal",
    "removal_scope",     "removal_exile", "removal_lock", "removal_targets", "mass_self",
    "counter_wipe_self", "igs",
    "counterspell", "protect", "wipe", "tokens_per_turn", "etb_tokens",
    "death_tokens", "burst_tokens", "populate_per_turn", "replicate",
    "doubler", "creature_token_mult", "esix", "anim", "reef",
    "mechanized_wincon", "mass_counters", "single_counters",
    "etb_counter_wipe", "etb_target_counters", "proliferate",
    "token_per_counter", "drain_on_counters", "chain", "kulrath_lock",
    "steal", "grave_rob", "yawgmoth", "clamp", "drain_own", "drain_any",
    "burst_drain", "hexproof_grant", "etb_removal", "blink_on_etb", "closet",
    "station", "rebuild", "tutor", "recursion", "energy_gain",
    "energy_thopter", "manufactor", "key", "anthem",
}

# hook values that the engine indexes positionally
_TUPLE_KEYS = {"etb_tokens", "death_tokens", "burst_tokens"}

ANNOTATIONS_FILE = "MagicSimulationAnnotations.ttl"

_SUBJECT_RE = re.compile(r"^:(\w+)\s*$|^:(\w+)\s", re.M)
_THREAT_RE = re.compile(r':threatWeight "(\d+)"')
_HOOK_RE = re.compile(
    r':behaviorKey "([^"]+)" ; :behaviorValue "((?:[^"\\]|\\.)*)"')


def _decode(key: str, raw: str):
    value = json.loads(raw.replace('\\"', '"').replace("\\\\", "\\"))
    if key in _TUPLE_KEYS and isinstance(value, list):
        return tuple(value)
    return value


def load_annotations(repo_root: Path, ind2name: dict[str, str]) -> dict:
    """Parse MagicSimulationAnnotations.ttl -> {card name: behavior dict}.

    *ind2name* maps card individual local names (as produced by
    ttl_loader.load_graph_cards) to card names; annotation subjects that do
    not resolve to a known card individual raise, keeping the annotation
    graph and the card graphs consistent.
    """
    path = Path(repo_root) / ANNOTATIONS_FILE
    hooks_by_name: dict[str, dict] = {}
    if not path.exists():
        return hooks_by_name
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block.startswith(":") or "owl:Ontology" in block:
            continue
        m = re.match(r":(\w+)", block)
        if not m:
            continue
        ind = m.group(1)
        name = ind2name.get(ind)
        if name is None:
            raise ValueError(
                f"{ANNOTATIONS_FILE}: subject :{ind} is not a card "
                f"individual known to the card graphs")
        hooks = hooks_by_name.setdefault(name, {})
        tw = _THREAT_RE.search(block)
        if tw:
            hooks["key"] = int(tw.group(1))
        for key, raw in _HOOK_RE.findall(block):
            if key not in BEHAVIOR_KEYS:
                raise ValueError(
                    f"{ANNOTATIONS_FILE}: :{ind} uses unknown behavior "
                    f"key {key!r} (see BEHAVIOR_KEYS)")
            hooks[key] = _decode(key, raw)
    return hooks_by_name


def apply_behaviors(card, hooks_by_name: dict) -> None:
    """Merge graph-authored hooks over parser-derived behavior."""
    hooks = hooks_by_name.get(card.name)
    if not hooks and " // " in card.name:
        hooks = hooks_by_name.get(card.name.split(" // ")[0])
    if hooks:
        card.behavior.update(hooks)
