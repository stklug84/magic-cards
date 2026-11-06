//! Continuous effects and the layer system (CR 611-613): port of the
//! Python `test_613_layers.py` (the two cases that need zone changes -
//! a static effect ending with its source, the new timestamp after a
//! blink - follow with `move_zone` in M6).

mod common;

use std::rc::Rc;

use common::{CreatureSpec, creature, creature_named, creature_with, make_game, pt, set};
use mtgrules::abilities::{Ability, StaticAbility};
use mtgrules::layers::ContinuousEffect;
use mtgrules::objects::{ObjId, PlayerId};

/// Layer 7c: creatures of `controller` get +dp/+dt.
fn anthem(dp: i64, dt: i64, controller: PlayerId) -> Ability {
    let factory = move |_game: &mtgrules::Game, src: ObjId| {
        vec![
            ContinuousEffect::new(
                7,
                "c",
                move |game, obj, ch| {
                    game.obj(obj).controller == controller && ch.types.contains("Creature")
                },
                move |_game, _obj, ch| {
                    ch.power = Some(ch.power.unwrap_or(0) + dp);
                    ch.toughness = Some(ch.toughness.unwrap_or(0) + dt);
                },
            )
            .with_source(Some(src)),
        ]
    };
    Ability::Static(StaticAbility::continuous(Rc::new(factory), "anthem"))
}

#[test]
fn rule_613_3_layer7c_effects_and_counters() {
    let mut game = make_game(2);
    let p0 = PlayerId(0);
    let c = creature(&mut game, p0, 2, 2);
    let src = creature_named(&mut game, p0, "Anthem Source", 1, 1, vec![]);
    game.obj_mut(src)
        .base_mut()
        .abilities
        .push(Rc::new(anthem(1, 1, p0)));
    game.bump();
    game.obj_mut(c).counters.insert("+1/+1".to_owned(), 2);
    game.bump();
    assert_eq!(pt(&game, c), (Some(5), Some(5)));
}

#[test]
fn rule_613_4_sublayer_7b_before_7c() {
    let mut game = make_game(2);
    let c = creature(&mut game, PlayerId(0), 5, 5);
    game.obj_mut(c).counters.insert("+1/+1".to_owned(), 1);
    game.add_floating_effect(
        ContinuousEffect::new(
            7,
            "b",
            move |_g, o, _ch| o == c,
            |_g, _o, ch| {
                ch.power = Some(0);
                ch.toughness = Some(1);
            },
        )
        .until_end_of_turn(),
    );
    // 7b sets to 0/1, then the counter applies in 7c -> 1/2
    assert_eq!(pt(&game, c), (Some(1), Some(2)));
}

#[test]
fn rule_613_1_layer4_type_change_before_layer6() {
    let mut game = make_game(2);
    let c = creature(&mut game, PlayerId(0), 3, 3);
    // timestamp 1: all creatures gain flying (layer 6)
    game.add_floating_effect(
        ContinuousEffect::new(
            6,
            "",
            |_g, _o, ch| ch.types.contains("Creature"),
            |_g, _o, ch| {
                ch.keywords.insert("flying".to_owned());
            },
        )
        .until_end_of_turn(),
    );
    // timestamp 2 (later!): c becomes a land, loses all card types
    game.add_floating_effect(
        ContinuousEffect::new(
            4,
            "",
            move |_g, o, _ch| o == c,
            |_g, _o, ch| {
                ch.types.clear();
                ch.types.insert("Land".to_owned());
            },
        )
        .until_end_of_turn(),
    );
    let ch = game.chars(c);
    assert_eq!(ch.types, set(&["Land"]));
    // layer 4 applies before layer 6 regardless of timestamps (613.1), so
    // the flying grant no longer matches
    assert!(!ch.keywords.contains("flying"));
}

#[test]
fn rule_613_8_dependency_within_a_layer() {
    let mut game = make_game(2);
    let c = creature_with(
        &mut game,
        PlayerId(0),
        CreatureSpec {
            name: "Bear",
            subtypes: vec!["Bear"],
            ..CreatureSpec::default()
        },
    );
    let eff_b = ContinuousEffect::new(
        4,
        "",
        |_g, _o, ch| ch.subtypes.contains("Wolf"),
        |_g, _o, ch| {
            ch.subtypes.remove("Wolf");
            ch.subtypes.insert("Bird".to_owned());
        },
    )
    .until_end_of_turn();
    let eff_a = ContinuousEffect::new(
        4,
        "",
        |_g, _o, ch| ch.subtypes.contains("Bear"),
        |_g, _o, ch| {
            ch.subtypes.insert("Wolf".to_owned());
        },
    )
    .until_end_of_turn();
    // B has the earlier timestamp, but B depends on A (applying A changes
    // what B applies to) -> A must be applied first (613.8b)
    game.add_floating_effect(eff_b);
    game.add_floating_effect(eff_a);
    assert!(game.chars(c).subtypes.contains("Bird"));
}

#[test]
fn rule_613_7_timestamp_order_without_dependency() {
    let mut game = make_game(2);
    let c = creature(&mut game, PlayerId(0), 1, 1);
    let setter = |p: i64, t: i64| {
        ContinuousEffect::new(
            7,
            "b",
            move |_g, o, _ch| o == c,
            move |_g, _o, ch| {
                ch.power = Some(p);
                ch.toughness = Some(t);
            },
        )
        .until_end_of_turn()
    };
    game.add_floating_effect(setter(3, 3));
    game.add_floating_effect(setter(5, 5));
    assert_eq!(pt(&game, c), (Some(5), Some(5)));
}

#[test]
fn rule_514_2_until_end_of_turn_expires() {
    let mut game = make_game(2);
    let c = creature(&mut game, PlayerId(0), 1, 1);
    game.add_floating_effect(
        ContinuousEffect::new(
            7,
            "c",
            move |_g, o, _ch| o == c,
            |_g, _o, ch| {
                ch.power = Some(ch.power.unwrap_or(0) + 3);
            },
        )
        .until_end_of_turn(),
    );
    assert_eq!(game.chars(c).power, Some(4));
    game.end_of_turn_cleanup();
    assert_eq!(game.chars(c).power, Some(1));
}

// ---- Rust-side checks of the evaluator's bookkeeping

#[test]
fn static_effects_share_their_sources_timestamp_and_are_cached() {
    let mut game = make_game(2);
    let p0 = PlayerId(0);
    let c = creature(&mut game, p0, 2, 2);
    // a later floating setter would win over an earlier static one by
    // timestamp: the static effect takes its source's (earlier) timestamp
    let src = creature_named(&mut game, p0, "Setter", 0, 4, vec![setter_ability(c, 1, 1)]);
    game.add_floating_effect(
        ContinuousEffect::new(
            7,
            "b",
            move |_g, o, _ch| o == c,
            |_g, _o, ch| {
                ch.power = Some(9);
                ch.toughness = Some(9);
            },
        )
        .until_end_of_turn(),
    );
    assert_eq!(pt(&game, c), (Some(9), Some(9)));
    // the cache is keyed by the game tick: no change, no recomputation
    let before = Rc::as_ptr(&game.chars(c));
    assert_eq!(Rc::as_ptr(&game.chars(c)), before);
    game.bump();
    assert_ne!(Rc::as_ptr(&game.chars(c)), before);
    // objects off the battlefield answer with their base characteristics
    game.obj_mut(src).zone = mtgrules::objects::Zone::Graveyard;
    assert_eq!(game.chars(src).power, Some(0));
}

fn setter_ability(target: ObjId, p: i64, t: i64) -> Ability {
    let factory = move |_game: &mtgrules::Game, src: ObjId| {
        vec![
            ContinuousEffect::new(
                7,
                "b",
                move |_g, o, _ch| o == target,
                move |_g, _o, ch| {
                    ch.power = Some(p);
                    ch.toughness = Some(t);
                },
            )
            .with_source(Some(src)),
        ]
    };
    Ability::Static(StaticAbility::continuous(Rc::new(factory), "setter"))
}

#[test]
fn rule_613_6_abilities_granted_in_layer_6_contribute_later_layers() {
    let mut game = make_game(2);
    let p0 = PlayerId(0);
    let c = creature(&mut game, p0, 2, 2);
    let grantee = creature_named(&mut game, p0, "Grantee", 1, 1, vec![]);
    // layer 6: the grantee gains a static anthem ability
    let bonus = Rc::new(anthem(3, 3, p0));
    game.add_floating_effect(
        ContinuousEffect::new(
            6,
            "",
            move |_g, o, _ch| o == grantee,
            move |_g, _o, ch| ch.abilities.push(Rc::clone(&bonus)),
        )
        .until_end_of_turn(),
    );
    // the re-collection pass before layer 7a picks the granted static up
    assert_eq!(pt(&game, c), (Some(5), Some(5)));
    assert_eq!(game.chars(grantee).abilities.len(), 1);
    game.end_of_turn_cleanup();
    assert_eq!(pt(&game, c), (Some(2), Some(2)));
}
