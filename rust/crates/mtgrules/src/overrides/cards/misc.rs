//! The rest: Hangarback and the Golem/Myr makers, populate, the copy
//! enchantments, the exile auras, mana rocks, counterspells, land fetch
//! and graveyard recursion.

use std::rc::Rc;

use mtgcards::CardData;

use super::{activated, counters, trig, trig_targets};
use crate::Game;
use crate::abilities::{
    ActivatedAbility, SpellAbility, StaticAbility, TargetSpec, TokenSpec, TriggerSpec,
};
use crate::effects::{AddMana, Ctx, Effect};
use crate::events::{Event, EventType};
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, CustomValue, ObjId, Zone};
use crate::overrides::{GOLEM, MYR, THOPTER, TokenDef, note};
use crate::protocols::CountValue;
use crate::stack::Target;

/// A spell ability with its text.
fn spell(effect: Effect, text: &str) -> SpellAbility {
    let mut ability = SpellAbility::new(effect);
    text.clone_into(&mut ability.text);
    ability
}

/// A trigger at the beginning of the controller's `step`.
fn own_step(step: &'static str) -> TriggerSpec {
    TriggerSpec::when(
        EventType::BeginStep,
        Rc::new(move |game: &Game, source: ObjId, event: &Event| {
            event.data.step.as_deref() == Some(step)
                && event.data.player == Some(game.obj(source).controller)
        }),
    )
}

/// A `{T}` mana ability.
fn tap_for(add: AddMana, text: &str) -> ActivatedAbility {
    ActivatedAbility {
        is_mana_ability: true,
        ..activated("", true, Effect::AddMana(add), text)
    }
}

/// A token copy of `obj`'s current characteristics (printed P/T and
/// keywords).
fn token_copy(game: &Game, obj: ObjId) -> TokenSpec {
    let c = game.chars(obj);
    let base = &game.obj(obj).base;
    TokenSpec {
        name: c.name.clone(),
        power: base.power,
        toughness: base.toughness,
        colors: c.colors.clone(),
        types: c.types.clone(),
        subtypes: c.subtypes.clone(),
        keywords: base.keywords.clone(),
        ..TokenSpec::default()
    }
}

/// Hangarback Walker: X counters in; Thopters out.
pub(super) fn hangarback(ch: &mut Characteristics, _card: &CardData) {
    "+1/+1".clone_into(&mut ch.etb_x_counters);
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(src) = ctx.source else {
            return;
        };
        let n = game
            .obj(src)
            .lki_counters
            .get("+1/+1")
            .copied()
            .unwrap_or(0);
        game.create_tokens(ctx.controller, Rc::new(THOPTER.spec()), n, None, None);
    });
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Dies),
            effect,
            "dies: a Thopter per +1/+1 counter",
        )
        .into(),
    );
}

/// Triplicate Titan: dies into three Golems.
pub(super) fn triplicate_titan(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.extend([
        "flying".to_owned(),
        "vigilance".to_owned(),
        "trample".to_owned(),
    ]);
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Dies),
            Effect::create_tokens(CountValue::Fixed(3), GOLEM.spec()),
            "dies: three 4/4 Golems",
        )
        .into(),
    );
    note(&ch.name, "Golem keyword split simplified to flying on all");
}

/// Myr Battlesphere: ETB four Myr.
pub(super) fn myr_battlesphere(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            Effect::create_tokens(CountValue::Fixed(4), MYR.spec()),
            "ETB: four Myr",
        )
        .into(),
    );
    note(&ch.name, "attack pump/damage trigger omitted");
}

/// Determined Iteration: populate at combat start.
pub(super) fn determined_iteration(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            own_step("combat_begin"),
            Effect::Populate { times: 1 },
            "begin combat: populate",
        )
        .into(),
    );
    note(&ch.name, "haste grant omitted");
}

/// Growing Ranks: populate each upkeep.
pub(super) fn growing_ranks(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            own_step("upkeep"),
            Effect::Populate { times: 1 },
            "upkeep: populate",
        )
        .into(),
    );
}

/// Extravagant Replication: upkeep copy of your best creature.
pub(super) fn extravagant_replication(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let me = ctx.controller;
        let mut best: Option<(ObjId, i64)> = None;
        for o in &game.player(me).battlefield {
            let c = game.chars(*o);
            if c.types.contains("Creature") && Some(*o) != ctx.source {
                let v = c.power.unwrap_or(0) + c.toughness.unwrap_or(0);
                if best.is_none_or(|(_, bv)| v > bv) {
                    best = Some((*o, v));
                }
            }
        }
        if let Some((best, _)) = best {
            let spec = token_copy(game, best);
            game.create_tokens(me, Rc::new(spec), 1, None, None);
        }
    });
    ch.abilities.push(
        trig(
            own_step("upkeep"),
            effect,
            "upkeep: token copy of your best creature",
        )
        .into(),
    );
}

/// Imprisoned in the Moon: enchanted permanent is a colorless land.
pub(super) fn imprisoned_in_the_moon(ch: &mut Characteristics, _card: &CardData) {
    let factory = |game: &Game, source: ObjId| {
        let held = game.obj(source).attached_to;
        let applies = move |_g: &Game, obj: ObjId, _c: &Characteristics| Some(obj) == held;
        let to_land = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.types = ["Land".to_owned()].into_iter().collect();
            c.subtypes.clear();
            c.supertypes.remove("Legendary");
        };
        let strip_abilities = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.abilities = vec![tap_for(AddMana::fixed(&["C"]), "{T}: Add {C}").into()];
            c.keywords.clear();
        };
        let zero_pt = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.power = None;
            c.toughness = None;
        };
        vec![
            ContinuousEffect::new(4, "", applies, to_land).with_source(Some(source)),
            ContinuousEffect::new(6, "", applies, strip_abilities).with_source(Some(source)),
            ContinuousEffect::new(7, "b", applies, zero_pt).with_source(Some(source)),
        ]
    };
    ch.abilities.push(
        StaticAbility::continuous(Rc::new(factory), "enchanted permanent is a colorless land")
            .into(),
    );
    let mut aura = spell(
        Effect::Noop {
            note: "enchant".to_owned(),
        },
        "enchant creature or planeswalker",
    );
    aura.targets = vec![TargetSpec::new("creature_or_planeswalker")];
    ch.abilities.push(aura.into());
}

/// Banishing Light: exile until it leaves the battlefield.
pub(super) fn banishing_light(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(Target::Object(t)) = ctx.target(0) else {
            return;
        };
        if game.obj(t).zone != Zone::Battlefield {
            return;
        }
        game.exile(t);
        if game.obj(t).zone == Zone::Exile
            && let Some(src) = ctx.source
        {
            game.obj_mut(src)
                .custom
                .insert("imprisoned".to_owned(), CustomValue::Object(t));
        }
    });
    ch.abilities.push(
        trig_targets(
            TriggerSpec::on(EventType::EntersBattlefield),
            effect,
            vec![TargetSpec {
                controller: "opponent".to_owned(),
                ..TargetSpec::new("nonland")
            }],
            "ETB: exile target nonland permanent",
        )
        .into(),
    );
    let release_cond =
        Rc::new(|_game: &Game, source: ObjId, event: &Event| event.data.obj == Some(source));
    let release = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let held = ctx
            .source
            .and_then(|src| game.obj(src).custom.get("imprisoned").copied());
        if let Some(CustomValue::Object(held)) = held
            && game.obj(held).zone == Zone::Exile
        {
            let owner = game.obj(held).owner;
            game.obj_mut(held).controller = owner;
            game.move_zone(held, Zone::Battlefield);
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::Dies, release_cond),
            release,
            "leaves: return the exiled card",
        )
        .into(),
    );
}

/// Chromatic Lantern: tap for any color.
pub(super) fn chromatic_lantern(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(tap_for(AddMana::any(), "{T}: Add one mana of any color").into());
    note(&ch.name, "lands-have-any-color static omitted");
}

/// Coalition Relic: tap for any color.
pub(super) fn coalition_relic(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(tap_for(AddMana::any(), "{T}: Add one mana of any color").into());
    note(&ch.name, "charge counter mode omitted");
}

/// Channeler Initiate: counters in, any color out.
pub(super) fn channeler_initiate(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            counters("-1/-1", 3, "self"),
            "enters with three -1/-1 counters",
        )
        .into(),
    );
    ch.abilities.push(
        tap_for(
            AddMana::any(),
            "{T}, remove a -1/-1 counter: any color (SIMPLIFIED: counter not removed)",
        )
        .into(),
    );
    note(&ch.name, "mana ability does not remove the -1/-1 counter");
}

/// Devoted Druid: tap for green.
pub(super) fn devoted_druid(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(tap_for(AddMana::fixed(&["G"]), "{T}: Add {G}").into());
    note(&ch.name, "untap-with--1/-1 mode omitted");
}

/// Evolution Sage: landfall proliferate.
pub(super) fn evolution_sage(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::when(
                EventType::LandPlayed,
                Rc::new(|game: &Game, source: ObjId, event: &Event| {
                    event.data.player == Some(game.obj(source).controller)
                }),
            ),
            Effect::Proliferate { times: 1 },
            "landfall: proliferate",
        )
        .into(),
    );
}

/// Heroic Reinforcements: two hasty Soldiers.
pub(super) fn heroic_reinforcements(ch: &mut Characteristics, _card: &CardData) {
    const HASTY_SOLDIER: TokenDef = TokenDef {
        subtypes: &["Soldier"],
        keywords: &["haste"],
        ..TokenDef::creature("Soldier", 1, 1, "RW")
    };
    ch.abilities.push(
        spell(
            Effect::create_tokens(CountValue::Fixed(2), HASTY_SOLDIER.spec()),
            "two hasty Soldiers",
        )
        .into(),
    );
    note(&ch.name, "+1/+1 until end of turn pump omitted");
}

/// Swan Song: counter; its controller gets a Bird.
pub(super) fn swan_song(ch: &mut Characteristics, _card: &CardData) {
    const BIRD: TokenDef = TokenDef {
        subtypes: &["Bird"],
        keywords: &["flying"],
        ..TokenDef::creature("Bird", 2, 2, "U")
    };
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Spell(id)) = ctx.target(0)
            && let Some(item) = game.stack_item(id)
        {
            let controller = item.controller;
            game.counter_spell(id);
            if controller != ctx.controller {
                game.create_tokens(controller, Rc::new(BIRD.spec()), 1, None, None);
            }
        }
    });
    let mut ability = spell(effect, "counter; its controller gets a Bird");
    ability.targets = vec![TargetSpec::new("spell")];
    ch.abilities.push(ability.into());
}

/// Arcane Denial: counter; they draw 2, you draw 1.
pub(super) fn arcane_denial(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Spell(id)) = ctx.target(0)
            && let Some(item) = game.stack_item(id)
        {
            let other = item.controller;
            game.counter_spell(id);
            game.draw(other, 2);
        }
        game.draw(ctx.controller, 1);
    });
    let mut ability = spell(
        effect,
        "counter; they draw 2, you draw 1 (draw timing simplified)",
    );
    ability.targets = vec![TargetSpec::new("spell")];
    ch.abilities.push(ability.into());
}

/// `{2},{T}, sacrifice: fetch `n` basic lands tapped`.
fn fetch_basics(n: i64, text: &str) -> ActivatedAbility {
    ActivatedAbility {
        sac_cost: "self".to_owned(),
        ..activated(
            "{2}",
            true,
            Effect::SearchLands {
                count: CountValue::Fixed(n),
                tapped: true,
                to_hand: false,
                basic_only: true,
            },
            text,
        )
    }
}

/// Wayfarer's Bauble: sac to fetch a basic tapped.
pub(super) fn wayfarers_bauble(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(fetch_basics(1, "{2},{T}, sac: basic land tapped").into());
}

/// Myriad Landscape: sac to fetch two basics tapped.
pub(super) fn myriad_landscape(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities
        .push(fetch_basics(2, "{2},{T}, sac: two basic lands tapped").into());
    note(&ch.name, "'share a land type' restriction omitted");
}

/// Persist (the sorcery): return your best dead creature.
pub(super) fn persist_sorcery(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let me = ctx.controller;
        let mut best: Option<(ObjId, i64)> = None;
        for card in &game.player(me).graveyard {
            let base = &game.obj(*card).base;
            if base.types.contains("Creature") {
                let v = base.power.unwrap_or(0) + base.toughness.unwrap_or(0);
                if best.is_none_or(|(_, bv)| v > bv) {
                    best = Some((*card, v));
                }
            }
        }
        if let Some((best, _)) = best {
            game.obj_mut(best).controller = me;
            game.move_zone(best, Zone::Battlefield);
        }
    });
    ch.abilities.push(
        spell(
            effect,
            "return a creature from your graveyard (loses legendary rider omitted)",
        )
        .into(),
    );
    note(
        &ch.name,
        "returns your best creature; legendary clause omitted",
    );
}

/// Aberrant Return: as Persist.
pub(super) fn aberrant_return(ch: &mut Characteristics, card: &CardData) {
    persist_sorcery(ch, card);
}

/// Grave Venerations: approximated as graveyard recursion.
pub(super) fn grave_venerations(ch: &mut Characteristics, card: &CardData) {
    persist_sorcery(ch, card);
    note(&ch.name, "approximated as graveyard recursion");
}
