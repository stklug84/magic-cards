//! The artifact-token engines: Academy Manufactor, Mechanized
//! Production, Infinite Guideline Station, Anim Pakal, Esix and the
//! token doublers.

use std::rc::Rc;

use mtgcards::CardData;

use super::trig;
use crate::Game;
use crate::abilities::{
    ActivatedAbility, SpellAbility, StaticAbility, TargetSpec, TokenSpec, TriggerSpec,
};
use crate::effects::{Ctx, Effect};
use crate::events::{Event, EventType};
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, CustomValue, ObjId, Zone};
use crate::overrides::{GNOME, ROBOT, counter_doubler, note, token_doubler};
use crate::protocols::CountValue;
use crate::replacements::Replacement;

/// Mechanized Production wins at eight same-name artifacts.
const MECHANIZED_WIN_COPIES: i64 = 8;
/// Permanents with two or more colors count as multicolored (CR 105.4).
const MULTICOLORED_MIN: usize = 2;
/// Infinite Guideline Station animates at twelve charge counters.
const STATION_FLIGHT_CHARGES: i64 = 12;
/// Esix only swaps in a copy at least twice the token's P+T.
const ESIX_UPGRADE_FACTOR: i64 = 2;

/// Academy Manufactor: clue/food/treasure -> all three.
pub(super) fn academy_manufactor(ch: &mut Characteristics, _card: &CardData) {
    let factory = |_game: &Game, source: ObjId| {
        let matches = move |g: &Game, event: &Event| {
            if event.data.controller != Some(g.obj(source).controller) {
                return false;
            }
            event
                .data
                .spec
                .as_ref()
                .is_some_and(|s| ["treasure", "clue", "food"].contains(&s.predefined.as_str()))
        };
        vec![
            Replacement::new(EventType::CreateToken, |_g, mut event| {
                let have = event
                    .data
                    .spec
                    .as_ref()
                    .map(|s| s.predefined.clone())
                    .unwrap_or_default();
                event.data.extra_specs =
                    [TokenSpec::treasure(), TokenSpec::clue(), TokenSpec::food()]
                        .into_iter()
                        .filter(|spec| spec.predefined != have)
                        .map(Rc::new)
                        .collect();
                Some(event)
            })
            .when(matches)
            .with_source(Some(source)),
        ]
    };
    ch.abilities.push(
        StaticAbility::replacement(Rc::new(factory), "clue/food/treasure -> all three").into(),
    );
}

/// Mechanized Production: copy the artifact; win at eight.
pub(super) fn mechanized_production(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(target) = ctx.source.and_then(|src| game.obj(src).attached_to) else {
            return;
        };
        if game.obj(target).zone != Zone::Battlefield {
            return;
        }
        let tch = game.chars(target);
        let spec = TokenSpec {
            name: tch.name.clone(),
            power: game.obj(target).base.power,
            toughness: game.obj(target).base.toughness,
            colors: tch.colors.clone(),
            types: tch.types.clone(),
            subtypes: tch.subtypes.clone(),
            predefined: if tch.subtypes.contains("Treasure") {
                "treasure".to_owned()
            } else {
                String::new()
            },
            ..TokenSpec::default()
        };
        game.create_tokens(ctx.controller, Rc::new(spec), 1, None, None);
        let mut names: indexmap::IndexMap<String, i64> = indexmap::IndexMap::new();
        for o in &game.player(ctx.controller).battlefield {
            let c = game.chars(*o);
            if c.types.contains("Artifact") && !c.name.is_empty() {
                *names.entry(c.name.clone()).or_insert(0) += 1;
            }
        }
        if names.values().copied().max().unwrap_or(0) >= MECHANIZED_WIN_COPIES {
            game.winner = Some(ctx.controller);
            game.game_over = true; // alternate win condition
            game.player_mut(ctx.controller).stat("mechanized_wins", 1);
        }
    });
    let cond = Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.step.as_deref() == Some("upkeep")
            && event.data.player == Some(game.obj(source).controller)
    });
    ch.abilities.push(
        trig(
            TriggerSpec::when(EventType::BeginStep, cond),
            effect,
            "upkeep: copy enchanted artifact; win at 8 same-name artifacts",
        )
        .into(),
    );
    let mut aura = SpellAbility::new(Effect::Noop {
        note: "enchant artifact".to_owned(),
    });
    aura.targets = vec![TargetSpec {
        controller: "you".to_owned(),
        ..TargetSpec::new("artifact")
    }];
    "enchant artifact you control".clone_into(&mut aura.text);
    ch.abilities.push(aura.into());
}

/// Count the controller's multicolored permanents.
fn igs_count_multicolored(game: &Game, ctx: &Ctx) -> i64 {
    let n = game
        .player(ctx.controller)
        .battlefield
        .iter()
        .filter(|o| game.chars(**o).colors.len() >= MULTICOLORED_MIN)
        .count();
    i64::try_from(n).unwrap_or(i64::MAX)
}

/// Station (rule 702.184): tap the best creature to charge up.
fn igs_station(game: &mut Game, ctx: &Ctx) {
    let Some(src) = ctx.source else {
        return;
    };
    let mut best: Option<(ObjId, i64)> = None;
    for o in &game.player(ctx.controller).battlefield {
        let c = game.chars(*o);
        let obj = game.obj(*o);
        let can_tap = !obj.entered_this_turn || c.keywords.contains("haste"); // rule 302.6
        if c.types.contains("Creature")
            && !obj.tapped
            && *o != src
            && can_tap
            && best.is_none_or(|(_, bp)| c.power.unwrap_or(0) > bp)
        {
            best = Some((*o, c.power.unwrap_or(0)));
        }
    }
    if let Some((best, power)) = best {
        game.tap(best);
        game.put_counters(src, "charge", power.max(0));
    }
}

/// At twelve charge counters: an artifact creature with flying.
fn igs_continuous(_game: &Game, source: ObjId) -> Vec<ContinuousEffect> {
    let applies = move |g: &Game, obj: ObjId, _c: &Characteristics| {
        obj == source && g.obj(obj).counter("charge") >= STATION_FLIGHT_CHARGES
    };
    let add_type = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
        c.types.insert("Creature".to_owned());
    };
    let add_kw = |_g: &Game, _o: ObjId, c: &mut Characteristics| {
        c.keywords.insert("flying".to_owned());
    };
    vec![
        ContinuousEffect::new(4, "", applies, add_type).with_source(Some(source)),
        ContinuousEffect::new(6, "", applies, add_kw).with_source(Some(source)),
    ]
}

/// Infinite Guideline Station: Robots, draws, and station charges.
pub(super) fn infinite_guideline_station(ch: &mut Characteristics, _card: &CardData) {
    let per_multicolored = || CountValue::Dynamic(Rc::new(igs_count_multicolored));
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::EntersBattlefield),
            Effect::create_tokens(per_multicolored(), ROBOT.spec()),
            "ETB: a Robot per multicolored permanent",
        )
        .into(),
    );
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Attacks),
            Effect::draw(per_multicolored()),
            "attacks: draw per multicolored permanent",
        )
        .into(),
    );
    ch.abilities.push(
        ActivatedAbility {
            sorcery_only: true,
            effect: Some(Effect::custom("", igs_station)),
            text: "Station (rule 702.184)".to_owned(),
            ..ActivatedAbility::default()
        }
        .into(),
    );
    ch.abilities.push(
        StaticAbility::continuous(
            Rc::new(igs_continuous),
            "12+ charge: artifact creature with flying",
        )
        .into(),
    );
}

/// Anim Pakal: grow on attack, then Gnomes per counter.
pub(super) fn anim_pakal(ch: &mut Characteristics, _card: &CardData) {
    let effect = Effect::custom("", |game: &mut Game, ctx: &Ctx| {
        let Some(src) = ctx.source else {
            return;
        };
        game.put_counters(src, "+1/+1", 1);
        let n = game.obj(src).counter("+1/+1");
        game.create_tokens(ctx.controller, Rc::new(GNOME.spec()), n, None, None);
    });
    ch.abilities.push(
        trig(
            TriggerSpec::on(EventType::Attacks),
            effect,
            "attacks: +1/+1 counter, then Gnomes per counter",
        )
        .into(),
    );
    note(&ch.name, "Gnomes enter untapped, not attacking");
}

/// Esix, Fractal Bloom: first tokens each turn may copy a creature.
pub(super) fn esix(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("ward:2".to_owned());
    let factory = |_game: &Game, source: ObjId| {
        let matches = move |g: &Game, event: &Event| {
            if event.data.controller != Some(g.obj(source).controller) {
                return false;
            }
            if g.obj(source).custom.get("esix_turn") == Some(&CustomValue::Int(i64::from(g.turn))) {
                return false;
            }
            event
                .data
                .spec
                .as_ref()
                .is_some_and(|s| s.types.contains("Creature"))
        };
        let replace = move |g: &mut Game, mut event: Event| {
            let turn = i64::from(g.turn);
            g.obj_mut(source)
                .custom
                .insert("esix_turn".to_owned(), CustomValue::Int(turn));
            let me = g.obj(source).controller;
            let mut best: Option<(ObjId, i64)> = None;
            for o in &g.player(me).battlefield {
                let c = g.chars(*o);
                if c.types.contains("Creature") {
                    let v = c.power.unwrap_or(0) + c.toughness.unwrap_or(0);
                    if best.is_none_or(|(_, bv)| v > bv) {
                        best = Some((*o, v));
                    }
                }
            }
            let token_size = event
                .data
                .spec
                .as_ref()
                .map_or(2, |s| s.power.unwrap_or(1) + s.toughness.unwrap_or(1));
            if let Some((best, bv)) = best
                && bv > ESIX_UPGRADE_FACTOR * token_size
            {
                let c = g.chars(best);
                let base = &g.obj(best).base;
                event.data.spec = Some(Rc::new(TokenSpec {
                    name: c.name.clone(),
                    power: base.power,
                    toughness: base.toughness,
                    colors: c.colors.clone(),
                    types: c.types.clone(),
                    subtypes: c.subtypes.clone(),
                    keywords: base.keywords.clone(),
                    ..TokenSpec::default()
                }));
            }
            Some(event)
        };
        vec![
            Replacement::new(EventType::CreateToken, replace)
                .when(matches)
                .with_source(Some(source)),
        ]
    };
    ch.abilities.push(
        StaticAbility::replacement(
            Rc::new(factory),
            "first tokens each turn may copy a creature",
        )
        .into(),
    );
}

/// Adrix and Nev: ward and a token doubler.
pub(super) fn adrix_and_nev(ch: &mut Characteristics, _card: &CardData) {
    ch.keywords.insert("ward:2".to_owned());
    ch.abilities.push(token_doubler(false, 2).into());
}

/// Doubling Season: token and counter doubling.
pub(super) fn doubling_season(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(token_doubler(false, 2).into());
    ch.abilities.push(counter_doubler().into());
}

/// Ojer Taq: triple creature tokens.
pub(super) fn ojer_taq(ch: &mut Characteristics, _card: &CardData) {
    ch.abilities.push(token_doubler(true, 3).into());
    note(&ch.name, "back face (Temple of Civilization) not modeled");
}
