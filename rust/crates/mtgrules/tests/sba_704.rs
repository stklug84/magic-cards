//! State-based actions (CR 704) and commander damage (903.10a): port of
//! the Python `test_704_sba.py`.

mod common;

use std::rc::Rc;

use common::{CreatureSpec, creature, creature_with, make_game, settle};
use mtgrules::abilities::TokenSpec;
use mtgrules::objects::{PlayerId, Zone};
use mtgrules::stack::Target;

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

#[test]
fn rule_704_5a_zero_life_loses() {
    let mut game = make_game(2);
    game.player_mut(P1).life = 0;
    settle(&mut game);
    assert!(game.player(P1).lost);
    assert_eq!(game.winner, Some(P0));
}

#[test]
fn rule_704_5b_draw_from_empty_library() {
    let mut game = make_game(2);
    game.player_mut(P1).library.clear();
    game.draw(P1, 1);
    settle(&mut game);
    assert!(game.player(P1).lost);
    assert!(game.player(P1).lose_reason.contains("704.5b"));
}

#[test]
fn rule_104_4a_simultaneous_loss_is_a_draw() {
    let mut game = make_game(2);
    game.player_mut(P0).life = 0;
    game.player_mut(P1).life = -3;
    settle(&mut game);
    assert!(game.player(P0).lost && game.player(P1).lost);
    assert!(game.game_over);
    assert_eq!(game.winner, None);
}

#[test]
fn rule_104_2a_sole_survivor_of_a_double_loss_wins() {
    let mut game = make_game(3);
    game.player_mut(P0).life = 0;
    game.player_mut(PlayerId(2)).poison = 10;
    settle(&mut game);
    assert!(game.player(P0).lost && game.player(PlayerId(2)).lost);
    assert!(!game.player(P1).lost);
    assert_eq!(game.winner, Some(P1));
    assert!(game.game_over);
}

#[test]
fn rule_704_5f_zero_toughness_dies() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 2, 2);
    game.put_counters(c, "-1/-1", 2);
    settle(&mut game);
    assert_eq!(game.obj(c).zone, Zone::Graveyard);
}

#[test]
fn rule_704_5g_lethal_damage_destroys() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 2, 3);
    let src = creature(&mut game, P1, 3, 2);
    game.deal_damage(Some(src), Target::Object(c), 3, false);
    settle(&mut game);
    assert_eq!(game.obj(c).zone, Zone::Graveyard);
}

#[test]
fn rule_704_5g_nonlethal_damage_survives() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 2, 3);
    let src = creature(&mut game, P1, 2, 2);
    game.deal_damage(Some(src), Target::Object(c), 2, false);
    settle(&mut game);
    assert_eq!(game.obj(c).zone, Zone::Battlefield);
    assert_eq!(game.obj(c).damage, 2);
}

#[test]
fn rule_704_5h_deathtouch_any_damage_destroys() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 2, 9);
    let src = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            power: 1,
            toughness: 2,
            keywords: vec!["deathtouch"],
            ..CreatureSpec::default()
        },
    );
    game.deal_damage(Some(src), Target::Object(c), 1, false);
    settle(&mut game);
    assert_eq!(game.obj(c).zone, Zone::Graveyard);
}

#[test]
fn rule_702_12_indestructible_survives_lethal() {
    let mut game = make_game(2);
    let c = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            power: 2,
            toughness: 1,
            keywords: vec!["indestructible"],
            ..CreatureSpec::default()
        },
    );
    let src = creature(&mut game, P1, 5, 2);
    game.deal_damage(Some(src), Target::Object(c), 5, false);
    settle(&mut game);
    assert_eq!(game.obj(c).zone, Zone::Battlefield);
}

#[test]
fn rule_704_5q_counter_annihilation() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 2, 2);
    game.obj_mut(c).counters.insert("+1/+1".to_owned(), 3);
    game.obj_mut(c).counters.insert("-1/-1".to_owned(), 2);
    settle(&mut game);
    assert_eq!(game.obj(c).counter("+1/+1"), 1);
    assert_eq!(game.obj(c).counter("-1/-1"), 0);
    assert!(!game.obj(c).counters.contains_key("-1/-1"));
}

#[test]
fn rule_704_5j_legend_rule() {
    let mut game = make_game(2);
    let legend = || CreatureSpec {
        name: "Legend",
        supertypes: vec!["Legendary"],
        ..CreatureSpec::default()
    };
    let a = creature_with(&mut game, P0, legend());
    let b = creature_with(&mut game, P0, legend());
    settle(&mut game);
    let on_bf = [a, b]
        .iter()
        .filter(|o| game.obj(**o).zone == Zone::Battlefield)
        .count();
    assert_eq!(on_bf, 1);
    // the policy keeps the one with the most counters: a tie keeps the first
    assert_eq!(game.obj(a).zone, Zone::Battlefield);
}

#[test]
fn rule_704_5j_different_controllers_keep_both() {
    let mut game = make_game(2);
    let legend = || CreatureSpec {
        name: "Legend",
        supertypes: vec!["Legendary"],
        ..CreatureSpec::default()
    };
    let a = creature_with(&mut game, P0, legend());
    let b = creature_with(&mut game, P1, legend());
    settle(&mut game);
    assert_eq!(game.obj(a).zone, Zone::Battlefield);
    assert_eq!(game.obj(b).zone, Zone::Battlefield);
}

#[test]
fn rule_704_5d_token_ceases_outside_battlefield() {
    let mut game = make_game(2);
    let toks = game.create_tokens(
        P0,
        Rc::new(TokenSpec::creature("Soldier", 1, 1)),
        1,
        None,
        None,
    );
    assert_eq!(toks.len(), 1);
    game.destroy(toks[0]);
    settle(&mut game);
    assert_eq!(game.obj(toks[0]).zone, Zone::Ceased);
    assert!(!game.player(P0).graveyard.contains(&toks[0]));
    assert_eq!(game.player(P0).stats.get("tokens_killed"), Some(&1));
}

#[test]
fn rule_903_10a_commander_damage() {
    let mut game = make_game(2);
    let cmd = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "General",
            power: 7,
            ..CreatureSpec::default()
        },
    );
    game.obj_mut(cmd).commander = true;
    for _ in 0..3 {
        game.deal_damage(Some(cmd), Target::Player(P1), 7, true);
    }
    settle(&mut game);
    assert!(game.player(P1).lost);
    assert!(game.player(P1).lose_reason.contains("903.10a"));
    assert_eq!(game.player(P1).commander_damage.get(&cmd), Some(&21));
}

#[test]
fn rule_120_3a_noncommander_damage_reduces_life_only() {
    let mut game = make_game(2);
    let c = creature(&mut game, P0, 7, 2);
    for _ in 0..3 {
        game.deal_damage(Some(c), Target::Player(P1), 7, true);
    }
    settle(&mut game);
    assert_eq!(game.player(P1).life, 40 - 21);
    assert!(!game.player(P1).lost);
}
