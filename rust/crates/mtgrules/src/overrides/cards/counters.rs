//! The -1/-1 counter theme: Hapatra and friends.

use std::rc::Rc;

use mtgcards::CardData;

use super::{activated, counters, optional_target, other_target, trig, trig_targets};
use crate::Game;
use crate::abilities::{ActivatedAbility, TargetSpec, TriggerSpec};
use crate::effects::{Ctx, Effect};
use crate::events::{Event, EventType};
use crate::objects::{Characteristics, ObjId};
use crate::overrides::{ELF, INSECT, SNAKE, counters_put_cond, note, reanimate};
use crate::protocols::CountValue;
use crate::stack::Target;

/// Ward-Blight 2 (SIMPLIFIED to Ward {2}); draw / drain on -1/-1.
pub(super) fn auntie_ool(ch: &mut Characteristics, _card: &CardData) {
    note(&ch.name, "Ward-Blight 2 simplified to Ward {2}");
    ch.keywords.insert("ward:2".to_owned());
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(obj) = ctx.event_obj else {
            return;
        };
        let controller = game.obj(obj).controller;
        if controller == ctx.controller {
            game.draw(ctx.controller, 1);
        } else {
            game.lose_life(controller, 1);
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::PutCounters, counters_put_cond("-1/-1", false)),
            effect,
            "Whenever one or more -1/-1 counters are put on a creature...",
        )
        .into(),
    );
}

/// Blowfly Infestation: countered creature dies -> spread a counter.
pub(super) fn blowfly(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, _source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            let o = game.obj(obj);
            o.base.types.contains("Creature")
                && o.lki_counters.get("-1/-1").copied().unwrap_or(0) > 0
        })
    });
    ch.abilities.push(
        trig_targets(
            TriggerSpec::when(EventType::Dies, cond),
            counters("-1/-1", 1, "target"),
            vec![TargetSpec::new("creature")],
            "Whenever a creature with a -1/-1 counter on it dies, put a -1/-1 counter on target creature.",
        )
        .into(),
    );
}

/// Necroskitter: steal opposing creatures that die with -1/-1.
pub(super) fn necroskitter(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            let o = game.obj(obj);
            o.base.types.contains("Creature")
                && o.controller != game.obj(source).controller
                && o.lki_counters.get("-1/-1").copied().unwrap_or(0) > 0
        })
    });
    let mut ability = trig(
        TriggerSpec::when(EventType::Dies, cond),
        reanimate(),
        "Whenever a creature an opponent controls with a -1/-1 counter on it dies, you may return that card to the battlefield under your control.",
    );
    ability.optional = true;
    ch.abilities.push(ability.into());
}

/// Hapatra: combat damage -> counter; counter placed -> Snake.
pub(super) fn hapatra(ch: &mut Characteristics, _card: &CardData) {
    let dmg_cond = Rc::new(|_game: &Game, source: ObjId, event: &Event| {
        event.data.resolved
            && event.data.combat == Some(true)
            && event.data.source == Some(source)
            && matches!(event.data.target, Some(Target::Player(_)))
    });
    ch.abilities.push(
        trig_targets(
            TriggerSpec::when(EventType::Damage, dmg_cond),
            counters("-1/-1", 1, "target"),
            vec![TargetSpec::new("creature")],
            "combat damage to a player -> -1/-1 counter",
        )
        .into(),
    );
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::PutCounters, counters_put_cond("-1/-1", false)),
            Effect::create_tokens(CountValue::Fixed(1), SNAKE.spec()),
            "-1/-1 placed -> snake",
        )
        .into(),
    );
}

/// Nest of Scarabs: -1/-1 counters placed -> Insects.
pub(super) fn nest_of_scarabs(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|_game: &Game, _source: ObjId, event: &Event| {
        event.data.resolved
            && event.data.kind.as_deref() == Some("-1/-1")
            && event.data.obj.is_some()
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::PutCounters, cond),
            Effect::create_tokens(CountValue::Dynamic(Rc::new(|_g, _c| 1)), INSECT.spec()),
            "-1/-1 placed -> insect",
        )
        .into(),
    );
    note(&ch.name, "one Insect per counter event (not per counter)");
}

/// Flourishing Defenses: -1/-1 counter placed -> Elf.
pub(super) fn flourishing_defenses(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::PutCounters, counters_put_cond("-1/-1", false)),
            Effect::create_tokens(CountValue::Fixed(1), ELF.spec()),
            "-1/-1 placed -> elf",
        )
        .into(),
    );
}

/// Obelisk Spider: reach; -1/-1 placed -> drain 1.
pub(super) fn obelisk_spider(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("reach".to_owned());
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::PutCounters, counters_put_cond("-1/-1", false)),
            Effect::Sequence(vec![
                Effect::LoseLife {
                    amount: CountValue::Fixed(1),
                    who: "each_opponent".to_owned(),
                },
                Effect::GainLife {
                    amount: CountValue::Fixed(1),
                },
            ]),
            "-1/-1 placed -> drain 1",
        )
        .into(),
    );
    note(&ch.name, "drain simplified to any -1/-1 event");
}

/// Midnight Banshee: wither; upkeep -1/-1 on nonblack creatures.
pub(super) fn midnight_banshee(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("wither".to_owned());
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        for obj in game.battlefield_objects() {
            let c = game.chars(obj);
            if c.types.contains("Creature") && !c.colors.contains("B") && Some(obj) != ctx.source {
                game.put_counters(obj, "-1/-1", 1);
            }
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(
                EventType::BeginStep,
                Rc::new(|_g, _s, e: &Event| e.data.step.as_deref() == Some("upkeep")),
            ),
            effect,
            "each upkeep: -1/-1 on each nonblack creature",
        )
        .into(),
    );
}

/// Carnifex Demon: enters with counters; spread them for {B}.
pub(super) fn carnifex_demon(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("flying".to_owned());
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            counters("-1/-1", 2, "self"),
            "enters with 2 -1/-1",
        )
        .into(),
    );
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(src) = ctx.source else {
            return;
        };
        if game.obj(src).counter("-1/-1") < 1 {
            return;
        }
        game.remove_counters(src, "-1/-1", 1);
        for obj in game.battlefield_objects() {
            if obj == src {
                continue;
            }
            if game.chars(obj).types.contains("Creature") {
                game.put_counters(obj, "-1/-1", 1);
            }
        }
    });
    ch.abilities.push(
        activated(
            "{B}",
            false,
            effect,
            "{B}, remove -1/-1: -1/-1 on each other creature",
        )
        .into(),
    );
}

/// Soul Snuffers: wither; ETB -1/-1 on every creature.
pub(super) fn soul_snuffers(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("wither".to_owned());
    let effect = Effect::custom("", |game: &mut Game, _ctx: &Ctx| {
        for obj in game.battlefield_objects() {
            if game.chars(obj).types.contains("Creature") {
                game.put_counters(obj, "-1/-1", 1);
            }
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            effect,
            "ETB: -1/-1 on each creature",
        )
        .into(),
    );
}

/// Contagion Engine: ETB player wipe; proliferate twice.
pub(super) fn contagion_engine(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Player(p)) = ctx.target(0) {
            for obj in game.player(p).battlefield.clone() {
                if game.chars(obj).types.contains("Creature") {
                    game.put_counters(obj, "-1/-1", 1);
                }
            }
        }
    });
    ch.abilities.push(
        trig_targets(
            TriggerSpec::on(EventType::EntersBattlefield),
            effect,
            vec![TargetSpec::new("player")],
            "ETB: -1/-1 on each creature target player controls",
        )
        .into(),
    );
    ch.abilities.push(
        activated(
            "{4}",
            true,
            Effect::Proliferate { times: 2 },
            "{4},{T}: proliferate twice",
        )
        .into(),
    );
}

/// Contagion Clasp: ETB -1/-1; tap to proliferate.
pub(super) fn contagion_clasp(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig_targets(
            TriggerSpec::on(EventType::EntersBattlefield),
            counters("-1/-1", 1, "target"),
            vec![TargetSpec::new("creature")],
            "ETB: -1/-1 on target creature",
        )
        .into(),
    );
    ch.abilities.push(
        activated(
            "{4}",
            true,
            Effect::Proliferate { times: 1 },
            "{4},{T}: proliferate",
        )
        .into(),
    );
}

/// Skinrender: ETB three -1/-1 counters.
pub(super) fn skinrender(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig_targets(
            TriggerSpec::on(EventType::EntersBattlefield),
            counters("-1/-1", 3, "target"),
            vec![other_target("creature")],
            "ETB: three -1/-1 on target creature",
        )
        .into(),
    );
}

/// Yawgmoth: pay life + sac for counters and cards.
pub(super) fn yawgmoth(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("hexproof".to_owned()); // SIMPLIFIED: protection from Humans
    ch.abilities.push(
        ActivatedAbility {
            life_cost: 1,
            sac_cost: "another creature".to_owned(),
            effect: Some(Effect::Sequence(vec![
                counters("-1/-1", 1, "target"),
                Effect::draw(CountValue::Fixed(1)),
            ])),
            targets: vec![optional_target("creature")],
            text: "Pay 1 life, sacrifice another creature: -1/-1 + draw".to_owned(),
            ..ActivatedAbility::default()
        }
        .into(),
    );
    note(
        &ch.name,
        "protection from Humans simplified to hexproof; {B}{B} discard mode omitted",
    );
}
