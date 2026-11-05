//! Kaya, the Saheelis, the Thopter and aristocrat engines, the
//! blinkers and the Reef Worm.

use std::rc::Rc;

use mtgcards::CardData;

use super::{trig, trig_targets};
use crate::Game;
use crate::abilities::{ActivatedAbility, StaticAbility, TargetSpec, TriggerSpec};
use crate::effects::{Ctx, Effect};
use crate::events::{Event, EventType};
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, ObjId, Zone};
use crate::overrides::{REEF_FISH, SERVO, THOPTER, note, reef_spec};
use crate::protocols::CountValue;
use crate::replacements::Replacement;
use crate::stack::Target;

/// Mentor of the Meek triggers for creatures with power 2 or less.
const MEEK_MAX_POWER: i64 = 2;

fn drain(n: i64) -> Effect {
    Effect::Drain {
        amount: CountValue::Fixed(n),
    }
}

fn draw(n: i64) -> Effect {
    Effect::draw(CountValue::Fixed(n))
}

fn loyalty(cost: i64, effect: Effect, text: &str) -> ActivatedAbility {
    ActivatedAbility {
        loyalty_cost: Some(cost),
        effect: Some(effect),
        text: text.to_owned(),
        ..ActivatedAbility::default()
    }
}

/// Whether the damage event is resolved combat damage to a player from
/// one of the source's controller's permanents of `kind`.
fn own_combat_hit(game: &Game, source: ObjId, event: &Event, kind: &str) -> bool {
    event.data.resolved
        && event.data.combat == Some(true)
        && matches!(event.data.target, Some(Target::Player(_)))
        && event.data.source.is_some_and(|src| {
            game.obj(src).controller == game.obj(source).controller
                && game.chars(src).types.contains(kind)
        })
}

/// Kaya, Geist Hunter: team deathtouch; token doubling burst.
pub(super) fn kaya_geist_hunter(ch: &mut Characteristics, _card: &CardData) {
    let plus1 = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let me = ctx.controller;
        game.add_floating_effect(
            ContinuousEffect::new(
                6,
                "",
                move |g: &Game, o: ObjId, c: &Characteristics| {
                    g.obj(o).controller == me && c.types.contains("Creature")
                },
                |_g, _o, c| {
                    c.keywords.insert("deathtouch".to_owned());
                },
            )
            .with_source(ctx.source)
            .until_end_of_turn(),
        );
    });
    ch.abilities
        .push(loyalty(1, plus1, "+1: your creatures gain deathtouch").into());

    let minus2 = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let me = ctx.controller;
        game.add_floating_effect(
            ContinuousEffect::new(6, "", |_g, _o, _c| false, |_g, _o, _c| {})
                .with_source(ctx.source)
                .until_end_of_turn(),
        );
        game.add_floating_replacement(
            Replacement::new(EventType::CreateToken, double_count)
                .when(move |_g, e: &Event| e.data.controller == Some(me))
                .with_source(ctx.source)
                .floating(),
        );
    });
    ch.abilities
        .push(loyalty(-2, minus2, "-2: token doubling until end of turn").into());
    note(
        &ch.name,
        "-2 doubling persists to end of game (cleanup of floating replacement simplified)",
    );
}

/// Double the created-token count (Kaya's -2).
fn double_count(_game: &mut Game, mut event: Event) -> Option<Event> {
    event.data.count = event.data.count.map(|n| n * 2);
    Some(event)
}

/// Saheeli Rai: +1 scry and ping each opponent.
pub(super) fn saheeli_rai(ch: &mut Characteristics, _card: &CardData) {
    let ping = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        for p in game.opponents(ctx.controller) {
            game.deal_damage(ctx.source, Target::Player(p), 1, false);
        }
    });
    ch.abilities.push(
        loyalty(
            1,
            Effect::Sequence(vec![
                Effect::Scry {
                    count: CountValue::Fixed(1),
                },
                ping,
            ]),
            "+1: scry 1, 1 damage to each opponent",
        )
        .into(),
    );
    note(&ch.name, "-2 copy-artifact and -7 omitted");
}

/// A cast trigger for the controller's spells whose card passes `kind`.
fn own_cast(kind: fn(&Characteristics) -> bool) -> TriggerSpec {
    TriggerSpec::when(
        EventType::Cast,
        Rc::new(move |game: &Game, source: ObjId, event: &Event| {
            event.data.player == Some(game.obj(source).controller)
                && event.data.obj.is_some_and(|obj| kind(&game.obj(obj).base))
        }),
    )
}

/// Saheeli, Sublime Artificer: noncreature casts make Servos.
pub(super) fn saheeli_sublime(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            own_cast(|base| !base.types.contains("Creature")),
            Effect::create_tokens(CountValue::Fixed(1), SERVO.spec()),
            "you cast a noncreature spell: Servo",
        )
        .into(),
    );
}

/// Sai, Master Thopterist: artifact casts make Thopters.
pub(super) fn sai(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            own_cast(|base| base.types.contains("Artifact")),
            Effect::create_tokens(CountValue::Fixed(1), THOPTER.spec()),
            "you cast an artifact spell: Thopter",
        )
        .into(),
    );
    note(&ch.name, "sacrifice-to-draw ability omitted");
}

/// Thopter Spy Network: upkeep Thopters; artifact hits draw.
pub(super) fn thopter_spy_network(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        let me = game.obj(source).controller;
        if event.data.step.as_deref() != Some("upkeep") || event.data.player != Some(me) {
            return false;
        }
        game.player(me)
            .battlefield
            .iter()
            .any(|o| game.chars(*o).types.contains("Artifact"))
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::BeginStep, cond),
            Effect::create_tokens(CountValue::Fixed(1), THOPTER.spec()),
            "upkeep (if you control an artifact): Thopter",
        )
        .into(),
    );
    ch.abilities.push(
        trig(
            TriggerSpec::when(
                EventType::Damage,
                Rc::new(|g, s, e| own_combat_hit(g, s, e, "Artifact")),
            ),
            draw(1),
            "artifact combat damage to a player: draw",
        )
        .into(),
    );
}

/// Bident of Thassa: creature hits draw cards.
pub(super) fn bident(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::when(
                EventType::Damage,
                Rc::new(|g, s, e| own_combat_hit(g, s, e, "Creature")),
            ),
            draw(1),
            "creature combat damage to a player: draw",
        )
        .into(),
    );
    note(&ch.name, "activated force-attack mode omitted");
}

/// Mentor of the Meek: small creatures draw cards.
pub(super) fn mentor_of_the_meek(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            let o = game.obj(obj);
            obj != source
                && o.controller == game.obj(source).controller
                && o.base.types.contains("Creature")
                && o.base.power.unwrap_or(0) <= MEEK_MAX_POWER
        })
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::EntersBattlefield, cond),
            draw(1),
            "small creature enters: draw",
        )
        .into(),
    );
    note(&ch.name, "the {1} payment is waived (SIMPLIFIED)");
}

/// A dies trigger for creatures passing `check(game, source, dead)`.
fn dies_when(check: fn(&Game, ObjId, ObjId) -> bool) -> TriggerSpec {
    TriggerSpec::when(
        EventType::Dies,
        Rc::new(move |game: &Game, source: ObjId, event: &Event| {
            event.data.obj.is_some_and(|obj| check(game, source, obj))
        }),
    )
}

/// Blood Artist: any creature death drains 1.
pub(super) fn blood_artist(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            dies_when(|game, _source, obj| game.obj(obj).base.types.contains("Creature")),
            drain(1),
            "any creature dies: drain 1",
        )
        .into(),
    );
    note(&ch.name, "drains each opponent instead of target player");
}

/// Zulaport Cutthroat: own creature death drains 1.
pub(super) fn zulaport(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            dies_when(|game, source, obj| {
                let o = game.obj(obj);
                o.base.types.contains("Creature") && o.controller == game.obj(source).controller
            }),
            drain(1),
            "own creature dies: drain 1",
        )
        .into(),
    );
}

/// Marionette Apprentice: own artifact/token death drains 1.
pub(super) fn marionette_apprentice(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            dies_when(|game, source, obj| {
                let o = game.obj(obj);
                o.controller == game.obj(source).controller
                    && (o.base.types.contains("Artifact") || o.is_token)
            }),
            drain(1),
            "own artifact/token dies: drain 1",
        )
        .into(),
    );
}

/// Shalai: your other permanents have hexproof.
pub(super) fn shalai(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("flying".to_owned());
    let factory = |_game: &Game, source: ObjId| {
        let applies = move |g: &Game, obj: ObjId, _c: &Characteristics| {
            g.obj(obj).controller == g.obj(source).controller && obj != source
        };
        vec![
            ContinuousEffect::new(6, "", applies, |_g, _o, c| {
                c.keywords.insert("hexproof".to_owned());
            })
            .with_source(Some(source)),
        ]
    };
    ch.abilities
        .push(StaticAbility::continuous(Rc::new(factory), "your other stuff has hexproof").into());
    note(&ch.name, "player hexproof + {4}{G}{G} pump omitted");
}

/// Skyclave Apparition: ETB exile a small nonland permanent.
pub(super) fn skyclave(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        if let Some(Target::Object(t)) = ctx.target(0)
            && game.obj(t).zone == Zone::Battlefield
        {
            game.exile(t);
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
            "ETB: exile target small nonland permanent",
        )
        .into(),
    );
    note(&ch.name, "mv<=4 restriction and Illusion give-back omitted");
}

/// Restoration Angel: flash flyer; ETB blink.
pub(super) fn restoration_angel(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords
        .extend(["flash".to_owned(), "flying".to_owned()]);
    let mut ability = trig_targets(
        TriggerSpec::on(EventType::EntersBattlefield),
        Effect::Blink { index: 0 },
        vec![TargetSpec {
            controller: "you".to_owned(),
            other: true,
            optional: true,
            ..TargetSpec::new("creature")
        }],
        "ETB: you may blink another creature you control",
    );
    ability.optional = true;
    ch.abilities.push(ability.into());
}

/// Conjurer's Closet: end-step blink.
pub(super) fn conjurers_closet(ch: &mut Characteristics, _card: &CardData) {
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.step.as_deref() == Some("end")
            && event.data.player == Some(game.obj(source).controller)
    });
    let mut ability = trig_targets(
        TriggerSpec::when(EventType::BeginStep, cond),
        Effect::Blink { index: 0 },
        vec![TargetSpec {
            controller: "you".to_owned(),
            optional: true,
            ..TargetSpec::new("creature")
        }],
        "end step: blink a creature you control",
    );
    ability.optional = true;
    ch.abilities.push(ability.into());
}

/// Reef Worm: dies into Fish -> Whale -> Kraken.
pub(super) fn reef_worm(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Dies),
            Effect::create_tokens(CountValue::Fixed(1), reef_spec(&REEF_FISH)),
            "dies: Fish -> Whale -> Kraken",
        )
        .into(),
    );
}
