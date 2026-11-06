//! The `Policy` trait's default choices (the Python `DefaultPolicy`'s
//! rule-based ones, used as-is by the trivial policy): discards,
//! sacrifices, the legend rule, bounce lands, scry, proliferate,
//! populate, tutoring, mana colours, damage division.

mod common;

use std::rc::Rc;

use common::{CardSpec, CreatureSpec, card_in_hand_with, card_in_library, creature, creature_with};
use mtgcards::CardData;
use mtgrules::Game;
use mtgrules::abilities::TokenSpec;
use mtgrules::effects::Ctx;
use mtgrules::objects::{Defender, ObjId, PlayerId, Zone};
use mtgrules::policy::{Action, TrivialPolicy, target_threat, threat};
use mtgrules::stack::Target;
use pycompat::random::PyRandom;

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

fn trivial_game() -> Game {
    Game::new(
        &["p0", "p1"],
        PyRandom::new(3),
        &[Rc::new(TrivialPolicy)],
        40,
    )
}

#[test]
fn the_trivial_policy_passes_and_fights_nothing() {
    let mut game = trivial_game();
    let bear = creature(&mut game, P0, 2, 2);
    let pol = game.policy(P0);
    assert!(pol.choose_action(&game, P0).is_none());
    assert_eq!(pol.declare_attackers(&game, P0, &[bear]).len(), 0);
    assert_eq!(pol.declare_blockers(&game, P1, &[bear], &[]).len(), 0);
    assert!(pol.keep_hand(&game, P0, &[], 0));
    assert_eq!(pol.bottom_cards(&game, P0, &[bear], 1), vec![bear]);
    let trigger = mtgrules::stack::PendingTrigger {
        ability: Rc::new(mtgrules::abilities::TriggeredAbility::new(
            mtgrules::abilities::TriggerSpec::on(mtgrules::events::EventType::Dies),
            mtgrules::effects::Effect::Noop {
                note: String::new(),
            },
        )),
        source: bear,
        controller: P0,
        event: mtgrules::events::Event::new(
            mtgrules::events::EventType::Dies,
            mtgrules::events::EventData::default(),
        ),
    };
    assert!(pol.accept_optional(&game, &trigger));
    assert_eq!(
        pol.order_triggers(&game, P0, vec![trigger.clone()]).len(),
        1
    );
    assert!(pol.commander_to_command_zone(&game, bear, Zone::Graveyard));
    assert!(pol.pay_ward(&game, mtgrules::stack::StackId(1), 2));
    assert_eq!(pol.choose_mana_color(&game, &Ctx::new(P0, None), "RG"), "R");
    assert_eq!(pol.choose_mana_color(&game, &Ctx::new(P0, None), ""), "C");
    let _ = Action::Land(bear); // the action enum is public API
}

#[test]
fn discard_is_the_highest_mana_value_and_sacrifice_the_least_threat() {
    let mut game = trivial_game();
    let cheap = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Cheap",
            mana_cost: "{1}",
            ..CardSpec::default()
        },
    );
    let dear = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Dear",
            mana_cost: "{4}{B}",
            ..CardSpec::default()
        },
    );
    let pol = game.policy(P0);
    assert_eq!(pol.choose_discard(&game, P0), Some(dear));
    assert_ne!(pol.choose_discard(&game, P0), Some(cheap));
    let small = creature(&mut game, P0, 1, 1);
    let big = creature(&mut game, P0, 5, 5);
    let rock = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Rock",
            types: vec!["Artifact"],
            ..CreatureSpec::default()
        },
    );
    assert_eq!(
        pol.choose_sacrifice(&game, P0, "a creature", None),
        Some(small)
    );
    assert_eq!(
        pol.choose_sacrifice(&game, P0, "a creature", Some(small)),
        Some(big)
    );
    assert_eq!(
        pol.choose_sacrifice(&game, P0, "an artifact", None),
        Some(rock)
    );
    assert_eq!(
        pol.choose_sacrifice(&game, P0, "a planeswalker or creature", Some(big)),
        Some(small)
    );
    assert!(threat(&game, big) > threat(&game, small));
    assert_eq!(target_threat(&game, Target::Player(P1)), 0.0);
}

#[test]
fn legend_rule_bounce_land_and_populate_choices() {
    let mut game = trivial_game();
    let a = creature(&mut game, P0, 2, 2);
    let b = creature(&mut game, P0, 2, 2);
    game.obj_mut(b).counters.insert("+1/+1".to_owned(), 2);
    let pol = game.policy(P0);
    assert_eq!(pol.choose_legend(&game, &[a, b]), b);
    // the least colour-rich land goes back to hand
    let mono = CardData {
        behavior: {
            let mut beh = mtgcards::behavior::Behavior::new();
            beh.insert("land_colors", mtgcards::behavior::set_value(["G"]));
            beh
        },
        ..CardData::new("Forest", "test")
    };
    let dual = CardData {
        behavior: {
            let mut beh = mtgcards::behavior::Behavior::new();
            beh.insert("land_colors", mtgcards::behavior::set_value(["B", "G"]));
            beh
        },
        ..CardData::new("Swamp Forest", "test")
    };
    let forest = game.create_object(
        mtgrules::objects::Characteristics::default(),
        P0,
        false,
        Some(Rc::new(mono)),
    );
    let golgari = game.create_object(
        mtgrules::objects::Characteristics::default(),
        P0,
        false,
        Some(Rc::new(dual)),
    );
    game.place(forest, P0, Zone::Battlefield);
    game.place(golgari, P0, Zone::Battlefield);
    assert_eq!(
        pol.choose_bounce_land(&game, &[golgari, forest]),
        Some(golgari)
    );
    assert_eq!(pol.choose_bounce_land(&game, &[]), None);
    assert_eq!(pol.best_land(&game, P0, &[forest, golgari]), Some(forest));
    // populate copies the biggest creature token
    let tokens = game.create_tokens(
        P0,
        Rc::new(TokenSpec::creature("Snake", 1, 1)),
        1,
        None,
        None,
    );
    let big = game.create_tokens(
        P0,
        Rc::new(TokenSpec::creature("Wurm", 6, 6)),
        1,
        None,
        None,
    );
    assert_eq!(
        pol.choose_populate(&game, &[tokens[0], big[0]]),
        Some(big[0])
    );
    assert_eq!(pol.choose_populate(&game, &[]), None);
}

#[test]
fn scry_proliferate_tutor_and_division() {
    let mut game = trivial_game();
    let pol = game.policy(P0);
    // few lands: lands stay on top, spells go to the bottom
    let land = card_in_library(&mut game, P0, "Swamp");
    game.obj_mut(land).base_mut().types = ["Land".to_owned()].into_iter().collect();
    let spell = card_in_library(&mut game, P0, "Bolt");
    let (keep, bottom) = pol.scry(&game, P0, &[land, spell]);
    assert_eq!((keep, bottom), (vec![land], vec![spell]));
    // flooded: the land goes to the bottom
    for _ in 0..6 {
        creature_with(
            &mut game,
            P0,
            CreatureSpec {
                name: "Swamp",
                types: vec!["Land"],
                ..CreatureSpec::default()
            },
        );
    }
    let (keep, bottom) = pol.scry(&game, P0, &[land, spell]);
    assert_eq!((keep, bottom), (vec![spell], vec![land]));
    // proliferate: our good counters, their bad ones
    let mine = creature(&mut game, P0, 2, 2);
    game.obj_mut(mine).counters.insert("+1/+1".to_owned(), 1);
    let theirs = creature(&mut game, P1, 2, 2);
    game.obj_mut(theirs).counters.insert("-1/-1".to_owned(), 1);
    let their_good = creature(&mut game, P1, 2, 2);
    game.obj_mut(their_good)
        .counters
        .insert("+1/+1".to_owned(), 1);
    let picks = pol.choose_proliferate(&game, P0);
    assert!(picks.contains(&mine) && picks.contains(&theirs) && !picks.contains(&their_good));
    // tutor: the highest key weight in the library
    let keyed = CardData {
        behavior: {
            let mut beh = mtgcards::behavior::Behavior::new();
            beh.insert("key", pycompat::json::Value::from(3_i64));
            beh
        },
        ..CardData::new("Key Piece", "test")
    };
    let key = game.create_object(
        mtgrules::objects::Characteristics::default(),
        P0,
        false,
        Some(Rc::new(keyed)),
    );
    game.place(key, P0, Zone::Library);
    assert_eq!(pol.choose_tutor_card(&game, P0), Some(key));
    // divided damage: lethal to the biggest threat first, the rest to the last
    let small = creature(&mut game, P1, 1, 1);
    let big = creature(&mut game, P1, 4, 4);
    let shares = pol.divide_damage(
        &game,
        &Ctx::new(P0, None),
        7,
        &[
            Target::Object(small),
            Target::Object(big),
            Target::Player(P1),
        ],
    );
    assert_eq!(shares.iter().sum::<i64>(), 7);
    assert_eq!(shares, vec![1, 4, 2]);
    assert_eq!(
        pol.divide_damage(&game, &Ctx::new(P0, None), 3, &[]),
        Vec::<i64>::new()
    );
    // the trivial attack declaration shape
    let _ = (Defender::Player(P1), ObjId(1));
}
