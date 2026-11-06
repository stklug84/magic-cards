//! Combat damage and combat keywords (CR 508-511, 702): port of the
//! Python `test_510_combat.py`.

mod common;

use common::{CreatureSpec, creature, creature_with, make_game, set_phase, settle};
use mtgrules::Game;
use mtgrules::combat::{CombatPhase, can_attack, can_block};
use mtgrules::objects::{Defender, ObjId, PlayerId, Zone};

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

fn game_in_combat() -> Game {
    let mut game = make_game(2);
    set_phase(&mut game, "combat");
    game
}

fn attacker(game: &mut Game, power: i64, toughness: i64, keywords: &[&'static str]) -> ObjId {
    let a = creature_with(
        game,
        P0,
        CreatureSpec {
            power,
            toughness,
            keywords: keywords.to_vec(),
            ..CreatureSpec::default()
        },
    );
    game.obj_mut(a).attacking = Some(Defender::Player(P1));
    a
}

fn block(game: &mut Game, attacker: ObjId, blocker: ObjId) {
    game.obj_mut(attacker).blocked_by.push(blocker);
    game.obj_mut(blocker).blocking.push(attacker);
}

/// Run the damage step(s) for the declared attack.
fn fight(game: &mut Game) {
    let mut phase = CombatPhase::new();
    phase.attackers = game
        .player(P0)
        .battlefield
        .iter()
        .copied()
        .filter(|o| game.obj(*o).attacking.is_some())
        .collect();
    let first_strikers = phase.combatants(game).into_iter().any(|a| {
        let kw = &game.chars(a).keywords;
        kw.contains("first strike") || kw.contains("double strike")
    });
    if first_strikers {
        phase.damage_step(game, true);
        settle(game);
    }
    phase.damage_step(game, false);
    settle(game);
}

#[test]
fn rule_510_1c_unblocked_hits_player() {
    let mut game = game_in_combat();
    attacker(&mut game, 3, 2, &[]);
    fight(&mut game);
    assert_eq!(game.player(P1).life, 37);
    assert_eq!(game.player(P0).stats.get("combat_damage"), Some(&3));
}

#[test]
fn rule_510_1c_blocked_trades() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 2, &[]);
    let b = creature(&mut game, P1, 2, 2);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(a).zone, Zone::Graveyard);
    assert_eq!(game.obj(b).zone, Zone::Graveyard);
    assert_eq!(game.player(P1).life, 40); // no damage through
}

#[test]
fn rule_702_19_trample_excess_to_player() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 6, 6, &["trample"]);
    let b = creature(&mut game, P1, 1, 2);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(b).zone, Zone::Graveyard);
    assert_eq!(game.player(P1).life, 36); // 6 - 2 lethal = 4 through
}

#[test]
fn rule_702_2b_deathtouch_trample_assigns_one() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 6, 6, &["trample", "deathtouch"]);
    let b = creature(&mut game, P1, 1, 4);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(b).zone, Zone::Graveyard); // 1 deathtouch = lethal
    assert_eq!(game.player(P1).life, 35); // 5 trample through
}

#[test]
fn rule_510_5_first_strike_kills_before_normal_damage() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 1, &["first strike"]);
    let b = creature(&mut game, P1, 5, 2);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(b).zone, Zone::Graveyard);
    assert_eq!(game.obj(a).zone, Zone::Battlefield); // never took damage
}

#[test]
fn rule_702_4_double_strike_hits_twice() {
    let mut game = game_in_combat();
    attacker(&mut game, 3, 2, &["double strike"]);
    fight(&mut game);
    assert_eq!(game.player(P1).life, 34);
}

#[test]
fn rule_509_1b_flying_blocked_only_by_reach_or_flying() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 2, &["flying"]);
    let ground = creature(&mut game, P1, 2, 2);
    let reach = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            keywords: vec!["reach"],
            ..CreatureSpec::default()
        },
    );
    let flier = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            keywords: vec!["flying"],
            ..CreatureSpec::default()
        },
    );
    assert!(!can_block(&game, ground, a));
    assert!(can_block(&game, reach, a));
    assert!(can_block(&game, flier, a));
}

#[test]
fn rule_302_6_summoning_sickness() {
    let mut game = game_in_combat();
    let fresh = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            entered_this_turn: true,
            ..CreatureSpec::default()
        },
    );
    assert!(!can_attack(&game, fresh));
    let hasty = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            entered_this_turn: true,
            keywords: vec!["haste"],
            ..CreatureSpec::default()
        },
    );
    assert!(can_attack(&game, hasty));
    let tapped = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            tapped: true,
            ..CreatureSpec::default()
        },
    );
    assert!(!can_attack(&game, tapped));
    let wall = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["defender"],
            ..CreatureSpec::default()
        },
    );
    assert!(!can_attack(&game, wall));
}

#[test]
fn rule_702_80_wither_damage_as_counters() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 2, &["wither"]);
    let b = creature(&mut game, P1, 1, 4);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(b).counter("-1/-1"), 2);
    assert_eq!(game.obj(b).damage, 0);
    assert_eq!(common::pt(&game, b), (Some(-1), Some(2)));
}

// -- fixes F2 / F3: both sides of combat deal their damage ----------

#[test]
fn rule_510_4_first_striker_takes_regular_step_damage() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 2, &["first strike"]);
    let b = creature(&mut game, P1, 3, 3);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(b).zone, Zone::Battlefield);
    assert_eq!(game.obj(b).damage, 2); // first-strike damage marked
    assert_eq!(game.obj(a).zone, Zone::Graveyard); // regular-step damage back
}

#[test]
fn rule_702_7b_first_strike_blocker_deals_damage() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 2, 2, &[]);
    let b = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            power: 2,
            toughness: 1,
            keywords: vec!["first strike"],
            ..CreatureSpec::default()
        },
    );
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(a).zone, Zone::Graveyard);
    assert_eq!(game.obj(b).zone, Zone::Battlefield); // the attacker never struck
    assert_eq!(game.obj(b).damage, 0);
}

#[test]
fn rule_510_1_zero_power_attacker_is_damaged_by_its_blocker() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 0, 4, &[]);
    let b = creature(&mut game, P1, 2, 2);
    block(&mut game, a, b);
    fight(&mut game);
    assert_eq!(game.obj(a).damage, 2);
    assert_eq!(game.obj(b).damage, 0);
    assert_eq!(game.player(P1).life, 40);
}

#[test]
fn rule_702_19d_trample_with_removed_blocker_hits_player() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 3, 3, &["trample"]);
    let b = creature(&mut game, P1, 1, 1);
    block(&mut game, a, b);
    game.move_zone(b, Zone::Graveyard); // removed before damage
    fight(&mut game);
    assert_eq!(game.player(P1).life, 37);
}

#[test]
fn rule_510_1c_blocked_without_trample_deals_nothing_if_blocker_left() {
    let mut game = game_in_combat();
    let a = attacker(&mut game, 3, 3, &[]);
    let b = creature(&mut game, P1, 1, 1);
    block(&mut game, a, b);
    game.move_zone(b, Zone::Graveyard);
    fight(&mut game);
    assert_eq!(game.player(P1).life, 40);
}
