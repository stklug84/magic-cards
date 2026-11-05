//! Skullclamp, The Scorpion God, the recursion and the global statics
//! of the counters deck.

use std::rc::Rc;

use mtgcards::CardData;

use super::{activated, counters, trig};
use crate::Game;
use crate::abilities::{ActivatedAbility, StaticAbility, TargetSpec, TriggerSpec};
use crate::effects::{Ctx, Effect};
use crate::events::{Event, EventType};
use crate::game::MoveOptions;
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, CustomValue, ObjId, Zone};
use crate::overrides::{ZOMBIE, grant_all_creatures, note};
use crate::protocols::CountValue;
use crate::replacements::Replacement;
use crate::stack::Target;

/// Skullclamp: +1/-1 equipment that draws when the bearer dies.
pub(super) fn skullclamp(ch: &mut Characteristics, _card: &CardData) {
    let factory = |game: &Game, source: ObjId| {
        let bearer = game.obj(source).attached_to;
        let applies = move |_g: &Game, obj: ObjId, _c: &Characteristics| Some(obj) == bearer;
        let clamp = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.power = Some(c.power.unwrap_or(0) + 1);
            c.toughness = Some(c.toughness.unwrap_or(0) - 1);
        };
        vec![ContinuousEffect::new(7, "c", applies, clamp).with_source(Some(source))]
    };
    ch.abilities
        .push(StaticAbility::continuous(Rc::new(factory), "equipped gets +1/-1").into());

    // note: attachment is cleared on zone change, so capture via the
    // object's override scratch state:
    let died_cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            game.obj(obj).custom.get("clamped_by") == Some(&CustomValue::Object(source))
        })
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::Dies, died_cond),
            Effect::draw(CountValue::Fixed(2)),
            "equipped dies: draw 2",
        )
        .into(),
    );

    let equip = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Object(t)) = ctx.target(0)
            && game.obj(t).zone == Zone::Battlefield
            && let Some(src) = ctx.source
        {
            game.attach(src, t);
            game.obj_mut(t)
                .custom
                .insert("clamped_by".to_owned(), CustomValue::Object(src));
        }
    });
    ch.abilities.push(
        ActivatedAbility {
            sorcery_only: true,
            targets: vec![TargetSpec {
                controller: "you".to_owned(),
                ..TargetSpec::new("creature")
            }],
            ..activated("{1}", false, equip, "Equip {1}")
        }
        .into(),
    );
}

/// The Scorpion God: countered deaths draw cards.
pub(super) fn scorpion_god(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, _source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            let o = game.obj(obj);
            o.base.types.contains("Creature")
                && o.lki_counters.get("-1/-1").copied().unwrap_or(0) > 0
        })
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::Dies, cond),
            Effect::draw(CountValue::Fixed(1)),
            "creature with -1/-1 dies: draw",
        )
        .into(),
    );
    ch.abilities.push(
        ActivatedAbility {
            targets: vec![TargetSpec::new("creature")],
            ..activated(
                "{1}{B}{R}",
                false,
                counters("-1/-1", 1, "target"),
                "{1}{B}{R}: -1/-1 on target creature",
            )
        }
        .into(),
    );
    note(
        &ch.name,
        "return-to-hand-from-graveyard upkeep trigger omitted",
    );
}

/// Dusk Urchins: shrink on attack, draw per counter on death.
pub(super) fn dusk_urchins(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Attacks),
            counters("-1/-1", 1, "self"),
            "attacks: -1/-1 on itself",
        )
        .into(),
    );
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(src) = ctx.source {
            let n = game
                .obj(src)
                .lki_counters
                .get("-1/-1")
                .copied()
                .unwrap_or(0);
            game.draw(ctx.controller, n);
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Dies),
            effect,
            "dies: draw per -1/-1 counter",
        )
        .into(),
    );
    note(&ch.name, "blocks trigger folded into attacks only");
}

/// Grave Titan: deathtouch; Zombies on entry and attack.
pub(super) fn grave_titan(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("deathtouch".to_owned());
    let two_zombies = || Effect::create_tokens(CountValue::Fixed(2), ZOMBIE.spec());
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            two_zombies(),
            "ETB: two Zombies",
        )
        .into(),
    );
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Attacks),
            two_zombies(),
            "attacks: two Zombies",
        )
        .into(),
    );
}

/// Puppeteer Clique: ETB raid the best opposing graveyard creature.
pub(super) fn puppeteer_clique(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords
        .extend(["flying".to_owned(), "persist".to_owned()]);
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let mut best: Option<ObjId> = None;
        let mut best_power = -1;
        for opp in game.opponents(ctx.controller) {
            for card in &game.player(opp).graveyard {
                let base = &game.obj(*card).base;
                if base.types.contains("Creature") && base.power.unwrap_or(0) > best_power {
                    best = Some(*card);
                    best_power = base.power.unwrap_or(0);
                }
            }
        }
        if let Some(best) = best {
            game.obj_mut(best).controller = ctx.controller;
            game.move_zone(best, Zone::Battlefield);
            game.player_mut(ctx.controller).stat("grave_robs", 1);
        }
    });
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            effect,
            "ETB: raid an opposing graveyard",
        )
        .into(),
    );
    note(
        &ch.name,
        "stolen creature stays (haste/exile-at-end omitted)",
    );
}

/// Reassembling Skeleton: return itself from the graveyard.
pub(super) fn reassembling_skeleton(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(src) = ctx.source
            && game.obj(src).zone == Zone::Graveyard
        {
            game.obj_mut(src).controller = ctx.controller;
            let opts = MoveOptions {
                tapped: true,
                ..MoveOptions::default()
            };
            game.move_zone_with(src, Zone::Battlefield, &opts);
        }
    });
    ch.abilities.push(
        ActivatedAbility {
            from_graveyard: true,
            ..activated(
                "{1}{B}",
                false,
                effect,
                "{1}{B}: return from graveyard tapped",
            )
        }
        .into(),
    );
}

/// Quillspike: eat a -1/-1 counter for +3/+3.
pub(super) fn quillspike(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let fed = game
            .player(ctx.controller)
            .battlefield
            .iter()
            .copied()
            .find(|obj| game.obj(*obj).counter("-1/-1") > 0);
        let Some(fed) = fed else {
            return;
        };
        game.remove_counters(fed, "-1/-1", 1);
        let src = ctx.source;
        let pump = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.power = Some(c.power.unwrap_or(0) + 3);
            c.toughness = Some(c.toughness.unwrap_or(0) + 3);
        };
        game.add_floating_effect(
            ContinuousEffect::new(7, "c", move |_g, o, _c| Some(o) == src, pump)
                .with_source(src)
                .until_end_of_turn(),
        );
    });
    ch.abilities
        .push(activated("{B}", false, effect, "{B/G}, remove a -1/-1 counter: +3/+3").into());
    note(
        &ch.name,
        "{B/G} simplified to {B}; counter removed from any own creature",
    );
}

/// Everlasting Torment: no life gain; everything has wither.
pub(super) fn everlasting_torment(ch: &mut Characteristics, _card: &CardData) {
    let factory = |_game: &Game, source: ObjId| {
        vec![
            // no life gain (rule 615)
            Replacement::new(EventType::GainLife, |_g, _event| None).with_source(Some(source)),
        ]
    };
    ch.abilities
        .push(StaticAbility::replacement(Rc::new(factory), "players can't gain life").into());
    ch.abilities
        .push(grant_all_creatures("wither", false, false).into());
    note(&ch.name, "'damage can't be prevented' omitted");
}

/// Kulrath Knight: countered enemy creatures can't attack/block.
pub(super) fn kulrath_knight(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords
        .extend(["flying".to_owned(), "wither".to_owned()]);
    let factory = |_game: &Game, source: ObjId| {
        let applies = move |g: &Game, obj: ObjId, c: &Characteristics| {
            let o = g.obj(obj);
            o.controller != g.obj(source).controller
                && c.types.contains("Creature")
                && !o.counters.is_empty()
        };
        let shackle = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
            c.keywords.insert("shackled".to_owned());
        };
        vec![ContinuousEffect::new(6, "", applies, shackle).with_source(Some(source))]
    };
    ch.abilities.push(
        StaticAbility::continuous(
            Rc::new(factory),
            "opposing creatures with counters can't attack or block",
        )
        .into(),
    );
}

/// Massacre Girl: menace; your creatures have wither.
pub(super) fn massacre_girl(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("menace".to_owned());
    ch.abilities
        .push(grant_all_creatures("wither", true, false).into());
    note(&ch.name, "card-draw clause omitted");
}

/// Glissa Sunslayer: first strike + deathtouch; hits draw.
pub(super) fn glissa(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords
        .extend(["first strike".to_owned(), "deathtouch".to_owned()]);
    let cond = Rc::new(|_game: &Game, source: ObjId, event: &Event| {
        event.data.resolved
            && event.data.combat == Some(true)
            && event.data.source == Some(source)
            && matches!(event.data.target, Some(Target::Player(_)))
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::Damage, cond),
            Effect::draw(CountValue::Fixed(1)),
            "combat damage to player: draw",
        )
        .into(),
    );
    note(&ch.name, "modal (counters/stun) simplified to draw");
}
