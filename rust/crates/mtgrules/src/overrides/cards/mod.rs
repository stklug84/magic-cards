//! The per-card implementations and the name -> implementation table.

mod artifacts;
mod counters;
mod graveyard;
mod misc;
mod spells;
mod tokens;

use super::OverrideFn;
use crate::abilities::{ActivatedAbility, TargetSpec, TriggerSpec, TriggeredAbility};
use crate::effects::Effect;
use crate::protocols::CountValue;

/// A triggered ability with its oracle text.
fn trig(trigger: TriggerSpec, effect: Effect, text: &str) -> TriggeredAbility {
    let mut ability = TriggeredAbility::new(trigger, effect);
    text.clone_into(&mut ability.text);
    ability
}

/// A triggered ability with targets.
fn trig_targets(
    trigger: TriggerSpec,
    effect: Effect,
    targets: Vec<TargetSpec>,
    text: &str,
) -> TriggeredAbility {
    let mut ability = trig(trigger, effect, text);
    ability.targets = targets;
    ability
}

/// An activated ability with a mana cost (and maybe `{T}`).
fn activated(mana_cost: &str, tap_cost: bool, effect: Effect, text: &str) -> ActivatedAbility {
    ActivatedAbility {
        mana_cost: mana_cost.to_owned(),
        tap_cost,
        effect: Some(effect),
        text: text.to_owned(),
        ..ActivatedAbility::default()
    }
}

/// Put `n` counters of `kind` on `on`.
fn counters(kind: &str, n: i64, on: &str) -> Effect {
    Effect::PutCounters {
        kind: kind.to_owned(),
        count: CountValue::Fixed(n),
        on: on.to_owned(),
    }
}

/// `TargetSpec::new(what)` with `other`.
fn other_target(what: &str) -> TargetSpec {
    TargetSpec {
        other: true,
        ..TargetSpec::new(what)
    }
}

/// `TargetSpec::new(what)` with `optional`.
fn optional_target(what: &str) -> TargetSpec {
    TargetSpec {
        optional: true,
        ..TargetSpec::new(what)
    }
}

/// Card name -> hand-written implementation.
pub(super) static OVERRIDES: &[(&str, OverrideFn)] = &[
    ("Auntie Ool, Cursewretch", counters::auntie_ool),
    ("Blowfly Infestation", counters::blowfly),
    ("Necroskitter", counters::necroskitter),
    ("Hapatra, Vizier of Poisons", counters::hapatra),
    ("Nest of Scarabs", counters::nest_of_scarabs),
    ("Flourishing Defenses", counters::flourishing_defenses),
    ("Obelisk Spider", counters::obelisk_spider),
    ("Midnight Banshee", counters::midnight_banshee),
    ("Carnifex Demon", counters::carnifex_demon),
    ("Soul Snuffers", counters::soul_snuffers),
    ("Contagion Engine", counters::contagion_engine),
    ("Contagion Clasp", counters::contagion_clasp),
    ("Skinrender", counters::skinrender),
    ("Yawgmoth, Thran Physician", counters::yawgmoth),
    ("Skullclamp", graveyard::skullclamp),
    ("The Scorpion God", graveyard::scorpion_god),
    ("Dusk Urchins", graveyard::dusk_urchins),
    ("Grave Titan", graveyard::grave_titan),
    ("Puppeteer Clique", graveyard::puppeteer_clique),
    ("Reassembling Skeleton", graveyard::reassembling_skeleton),
    ("Quillspike", graveyard::quillspike),
    ("Everlasting Torment", graveyard::everlasting_torment),
    ("Kulrath Knight", graveyard::kulrath_knight),
    ("Massacre Girl, Known Killer", graveyard::massacre_girl),
    ("Glissa Sunslayer", graveyard::glissa),
    ("Fire Covenant", spells::fire_covenant),
    ("Black Sun's Zenith", spells::black_suns_zenith),
    ("Chaos Warp", spells::chaos_warp),
    ("Cultivate", spells::cultivate),
    ("Farewell", spells::farewell),
    ("Austere Command", spells::austere_command),
    ("Akroma's Will", spells::akromas_will),
    ("Spell Swindle", spells::spell_swindle),
    ("Brass's Bounty", spells::brasss_bounty),
    ("Curse of Opulence", spells::curse_of_opulence),
    ("Bootleggers' Stash", spells::bootleggers_stash),
    ("Treasure Vault", spells::treasure_vault),
    ("Retrofitter Foundry", spells::retrofitter_foundry),
    ("Academy Manufactor", artifacts::academy_manufactor),
    ("Mechanized Production", artifacts::mechanized_production),
    (
        "Infinite Guideline Station",
        artifacts::infinite_guideline_station,
    ),
    ("Anim Pakal, Thousandth Moon", artifacts::anim_pakal),
    ("Esix, Fractal Bloom", artifacts::esix),
    ("Adrix and Nev, Twincasters", artifacts::adrix_and_nev),
    ("Doubling Season", artifacts::doubling_season),
    (
        "Ojer Taq, Deepest Foundation // Temple of Civilization",
        artifacts::ojer_taq,
    ),
    ("Kaya, Geist Hunter", tokens::kaya_geist_hunter),
    ("Saheeli Rai", tokens::saheeli_rai),
    ("Saheeli, Sublime Artificer", tokens::saheeli_sublime),
    ("Sai, Master Thopterist", tokens::sai),
    ("Thopter Spy Network", tokens::thopter_spy_network),
    ("Bident of Thassa", tokens::bident),
    ("Mentor of the Meek", tokens::mentor_of_the_meek),
    ("Blood Artist", tokens::blood_artist),
    ("Zulaport Cutthroat", tokens::zulaport),
    ("Marionette Apprentice", tokens::marionette_apprentice),
    ("Shalai, Voice of Plenty", tokens::shalai),
    ("Skyclave Apparition", tokens::skyclave),
    ("Restoration Angel", tokens::restoration_angel),
    ("Conjurer's Closet", tokens::conjurers_closet),
    ("Reef Worm", tokens::reef_worm),
    ("Hangarback Walker", misc::hangarback),
    ("Triplicate Titan", misc::triplicate_titan),
    ("Myr Battlesphere", misc::myr_battlesphere),
    ("Determined Iteration", misc::determined_iteration),
    ("Growing Ranks", misc::growing_ranks),
    ("Extravagant Replication", misc::extravagant_replication),
    ("Imprisoned in the Moon", misc::imprisoned_in_the_moon),
    ("Banishing Light", misc::banishing_light),
    ("Chromatic Lantern", misc::chromatic_lantern),
    ("Coalition Relic", misc::coalition_relic),
    ("Channeler Initiate", misc::channeler_initiate),
    ("Devoted Druid", misc::devoted_druid),
    ("Evolution Sage", misc::evolution_sage),
    ("Heroic Reinforcements", misc::heroic_reinforcements),
    ("Swan Song", misc::swan_song),
    ("Arcane Denial", misc::arcane_denial),
    ("Wayfarer's Bauble", misc::wayfarers_bauble),
    ("Myriad Landscape", misc::myriad_landscape),
    ("Persist", misc::persist_sorcery),
    ("Aberrant Return", misc::aberrant_return),
    ("Grave Venerations", misc::grave_venerations),
];

#[cfg(test)]
mod tests {
    use super::OVERRIDES;

    #[test]
    fn names_are_unique() {
        let mut names: Vec<&str> = OVERRIDES.iter().map(|(n, _)| *n).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), OVERRIDES.len());
    }
}
