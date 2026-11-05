//! Spells with fixed modes and the Treasure makers.

use std::rc::Rc;

use mtgcards::CardData;

use super::{activated, trig};
use crate::Game;
use crate::abilities::{ActivatedAbility, SpellAbility, TargetSpec, TokenSpec, TriggerSpec};
use crate::effects::{AddMana, Ctx, Effect};
use crate::events::{Event, EventType};
use crate::objects::{Characteristics, ObjId, Zone};
use crate::overrides::{THOPTER, note};
use crate::protocols::CountValue;
use crate::stack::Target;

/// Austere Command mode: destroy creatures with power 3 or greater.
const AUSTERE_POWER_MIN: i64 = 3;

/// A spell ability with its text.
fn spell(effect: Effect, text: &str) -> SpellAbility {
    let mut ability = SpellAbility::new(effect);
    text.clone_into(&mut ability.text);
    ability
}

fn treasures(count: impl Into<CountValue>) -> Effect {
    Effect::create_tokens(count, TokenSpec::treasure())
}

/// Fire Covenant: pay life, divide damage.
pub(super) fn fire_covenant(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        spell(
            Effect::Sequence(vec![
                Effect::LoseLife {
                    amount: CountValue::Fixed(2),
                    who: "you".to_owned(),
                },
                Effect::DealDamage {
                    amount: CountValue::Fixed(4),
                    to: "divided".to_owned(),
                    index: 0,
                },
            ]),
            "pay life: 4 damage divided",
        )
        .into(),
    );
    note(&ch.name, "X life / X damage fixed at 2 life for 4 damage");
}

/// Black Sun's Zenith: X -1/-1 counters on each creature.
pub(super) fn black_suns_zenith(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        spell(
            Effect::PutCounters {
                kind: "-1/-1".to_owned(),
                count: CountValue::X,
                on: "each_creature".to_owned(),
            },
            "X -1/-1 counters on each creature",
        )
        .into(),
    );
    note(&ch.name, "shuffle-back clause omitted");
}

/// Chaos Warp: shuffle target permanent into its owner's library.
pub(super) fn chaos_warp(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Object(t)) = ctx.target(0)
            && game.obj(t).zone == Zone::Battlefield
        {
            let owner = game.obj(t).owner;
            game.move_zone(t, Zone::Library);
            game.shuffle(owner);
        }
    });
    let mut ability = spell(effect, "shuffle target permanent into library");
    ability.targets = vec![TargetSpec::new("permanent")];
    ch.abilities.push(ability.into());
    note(&ch.name, "reveal/may-cast rider omitted");
}

/// Cultivate: one basic tapped, one to hand.
pub(super) fn cultivate(ch: &mut Characteristics, _card: &CardData) {
    let search = |tapped: bool, to_hand: bool| Effect::SearchLands {
        count: CountValue::Fixed(1),
        tapped,
        to_hand,
        basic_only: true,
    };
    ch.abilities.push(
        spell(
            Effect::Sequence(vec![search(true, false), search(true, true)]),
            "one basic tapped + one to hand",
        )
        .into(),
    );
}

/// Farewell: exile all creatures and artifacts.
pub(super) fn farewell(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, _ctx: &Ctx| {
        for obj in game.battlefield_objects() {
            let c = game.chars(obj);
            if c.types.contains("Creature") || c.types.contains("Artifact") {
                game.exile(obj);
            }
        }
    });
    ch.abilities
        .push(spell(effect, "exile all creatures and artifacts").into());
    note(&ch.name, "modes fixed: creatures + artifacts, exiled");
}

/// Austere Command: destroy big creatures + opposing artifacts.
pub(super) fn austere_command(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        for obj in game.battlefield_objects() {
            let c = game.chars(obj);
            let big_creature =
                c.types.contains("Creature") && c.power.unwrap_or(0) >= AUSTERE_POWER_MIN;
            let their_artifact =
                c.types.contains("Artifact") && game.obj(obj).controller != ctx.controller;
            if big_creature || their_artifact {
                game.destroy(obj);
            }
        }
    });
    ch.abilities
        .push(spell(effect, "destroy big creatures + opposing artifacts").into());
    note(&ch.name, "modes fixed");
}

/// Akroma's Will: team protection plus a small pump.
pub(super) fn akromas_will(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        spell(
            Effect::Sequence(vec![
                Effect::ProtectAll,
                Effect::PumpAll {
                    power: 1,
                    toughness: 1,
                    tokens_only: false,
                },
            ]),
            "protection + small pump",
        )
        .into(),
    );
    note(&ch.name, "both modes approximated");
}

/// Spell Swindle: counter a spell, mint Treasures.
pub(super) fn spell_swindle(ch: &mut Characteristics, _card: &CardData) {
    let mut ability = spell(
        Effect::Sequence(vec![Effect::CounterSpell, treasures(CountValue::Fixed(3))]),
        "counter + treasures",
    );
    ability.targets = vec![TargetSpec::new("spell")];
    ch.abilities.push(ability.into());
    note(
        &ch.name,
        "treasures fixed at 3 (mana value of countered spell)",
    );
}

/// Brass's Bounty: a Treasure per land.
pub(super) fn brasss_bounty(ch: &mut Characteristics, _card: &CardData) {
    let count = CountValue::Dynamic(Rc::new(|game: &Game, ctx: &Ctx| {
        let lands = game
            .player(ctx.controller)
            .battlefield
            .iter()
            .filter(|o| game.chars(**o).types.contains("Land"))
            .count();
        i64::try_from(lands).unwrap_or(i64::MAX)
    }));
    ch.abilities
        .push(spell(treasures(count), "a treasure per land").into());
}

/// Curse of Opulence: gold each upkeep (SIMPLIFIED).
pub(super) fn curse_of_opulence(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        game.create_tokens(
            ctx.controller,
            Rc::new(TokenSpec::treasure()),
            1,
            None,
            None,
        );
    });
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.step.as_deref() == Some("upkeep")
            && event.data.player == Some(game.obj(source).controller)
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::BeginStep, cond),
            effect,
            "upkeep: gold",
        )
        .into(),
    );
    note(
        &ch.name,
        "attack-the-cursed-player trigger simplified to one Treasure per own upkeep",
    );
}

/// Bootleggers' Stash: tap for a Treasure.
pub(super) fn bootleggers_stash(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(activated("", true, treasures(CountValue::Fixed(1)), "{T}: Treasure").into());
    note(&ch.name, "grants-lands-the-ability simplified to itself");
}

/// Treasure Vault: colorless mana; burst into Treasures.
pub(super) fn treasure_vault(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        ActivatedAbility {
            is_mana_ability: true,
            ..activated(
                "",
                true,
                Effect::AddMana(AddMana::fixed(&["C"])),
                "{T}: Add {C}",
            )
        }
        .into(),
    );
    ch.abilities.push(
        ActivatedAbility {
            sac_cost: "self".to_owned(),
            ..activated(
                "{4}",
                true,
                treasures(CountValue::Fixed(4)),
                "{X}{X},{T}, sac: X Treasures (X=4)",
            )
        }
        .into(),
    );
    note(&ch.name, "X fixed at 4");
}

/// Retrofitter Foundry: pay and tap for a Thopter.
pub(super) fn retrofitter_foundry(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        activated(
            "{2}",
            true,
            Effect::create_tokens(CountValue::Fixed(1), THOPTER.spec()),
            "{2},{T}: Thopter",
        )
        .into(),
    );
    note(&ch.name, "untap/upgrade chain simplified");
}
