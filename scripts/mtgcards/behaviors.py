"""The behavior-key whitelist of the simulation annotations.

Oracle text cannot be executed, so cards whose effect matters to the
simulation get explicit behavior hooks: ``:hasBehaviorHook`` /
``:threatWeight`` assertions in MagicSimulationAnnotations.ttl (see
MagicCardsOntology.ttl). The simulator (the Rust workspace under
``rust/``) reads them; this module only names the keys it understands,
so that the graph validators (``mtgvalidate.consistency``) can reject an
annotation the simulator would not load.

BEHAVIOR_KEYS mirrors ``BEHAVIOR_KEYS`` in
``rust/crates/mtgcards/src/behavior.rs``; the Rust test
``rust/crates/mtgcards/tests/behavior_keys.rs`` reads this file and
fails when the two sets differ, so a key is added in both places.

Behavior key reference:

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

BEHAVIOR_KEYS = {
    "rock_mana",
    "rock_colors",
    "ramp_lands",
    "fetch_land",
    "enters_tapped",
    "land_colors",
    "treasures_per_turn",
    "burst_treasures",
    "draw_cards",
    "draw_per_turn",
    "draw_on_attack",
    "draw_on_tokens",
    "removal",
    "removal_scope",
    "removal_exile",
    "removal_lock",
    "removal_targets",
    "mass_self",
    "counter_wipe_self",
    "igs",
    "counterspell",
    "protect",
    "wipe",
    "tokens_per_turn",
    "etb_tokens",
    "death_tokens",
    "burst_tokens",
    "populate_per_turn",
    "replicate",
    "doubler",
    "creature_token_mult",
    "esix",
    "anim",
    "reef",
    "mechanized_wincon",
    "mass_counters",
    "single_counters",
    "etb_counter_wipe",
    "etb_target_counters",
    "proliferate",
    "token_per_counter",
    "drain_on_counters",
    "chain",
    "kulrath_lock",
    "steal",
    "grave_rob",
    "yawgmoth",
    "clamp",
    "drain_own",
    "drain_any",
    "burst_drain",
    "hexproof_grant",
    "etb_removal",
    "blink_on_etb",
    "closet",
    "station",
    "rebuild",
    "tutor",
    "recursion",
    "energy_gain",
    "energy_thopter",
    "manufactor",
    "key",
    "anthem",
}

# hook values that the simulator indexes positionally (mirrors TUPLE_KEYS
# in rust/crates/mtgcards/src/behavior.rs; checked by the same Rust test)
_TUPLE_KEYS = {"etb_tokens", "death_tokens", "burst_tokens"}
