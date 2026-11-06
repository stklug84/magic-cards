//! The policy presets and the knobs they turn: port of the Python
//! `test_policy_profiles.py`.

mod common;

use std::rc::Rc;

use common::{CardSpec, CreatureSpec, card_in_hand_with, creature_with, make_game};
use mtgrules::Game;
use mtgrules::objects::{Defender, ObjId, PlayerId};
use mtgrules::policy::{DefaultPolicy, Policy, PolicyProfile, get_profile};

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

#[test]
fn presets_exist() {
    for name in ["default", "aggressive", "control"] {
        assert_eq!(get_profile(name).expect("a preset").name, name);
    }
    assert_eq!(
        get_profile("default").expect("a preset"),
        PolicyProfile::default()
    );
}

#[test]
fn unknown_profile_exits() {
    let err = get_profile("yolo").expect_err("not a preset");
    assert_eq!(
        err,
        "unknown AI profile 'yolo'; choose from ['aggressive', 'control', 'default']"
    );
}

#[test]
fn default_policy_uses_default_profile() {
    assert_eq!(DefaultPolicy::new().profile.name, "default");
}

#[test]
fn mulligan_knobs() {
    let mut game = make_game(2);
    let strict = DefaultPolicy::with_profile(PolicyProfile {
        mulligan_min_lands: 3,
        mulligan_max_lands: 4,
        ..PolicyProfile::default()
    });
    let loose = DefaultPolicy::with_profile(PolicyProfile {
        mulligan_min_lands: 1,
        mulligan_max_lands: 6,
        ..PolicyProfile::default()
    });
    let mut hand: Vec<ObjId> = (0..5)
        .map(|i| {
            card_in_hand_with(
                &mut game,
                P0,
                CardSpec {
                    name: ["c0", "c1", "c2", "c3", "c4"][i],
                    types: vec!["Sorcery"],
                    ..CardSpec::default()
                },
            )
        })
        .collect();
    for name in ["l0", "l1"] {
        hand.push(card_in_hand_with(
            &mut game,
            P0,
            CardSpec {
                name,
                types: vec!["Land"],
                ..CardSpec::default()
            },
        ));
    }
    assert!(!strict.keep_hand(&game, P0, &hand, 0));
    assert!(loose.keep_hand(&game, P0, &hand, 0));
    // max_mulligans forces a keep
    let forced = DefaultPolicy::with_profile(PolicyProfile {
        mulligan_min_lands: 3,
        max_mulligans: 2,
        ..PolicyProfile::default()
    });
    assert!(forced.keep_hand(&game, P0, &hand, 2));
}

fn sized(
    game: &mut Game,
    player: PlayerId,
    name: &'static str,
    power: i64,
    toughness: i64,
) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            name,
            power,
            toughness,
            ..CreatureSpec::default()
        },
    )
}

#[test]
fn aggression_changes_attacks() {
    let mut results = Vec::new();
    for name in ["aggressive", "control"] {
        let mut game = make_game(2);
        game.set_policy(
            P0,
            Rc::new(DefaultPolicy::with_profile(
                get_profile(name).expect("a preset"),
            )),
        );
        let attacker = sized(&mut game, P0, "Raider", 3, 3);
        sized(&mut game, P1, "Wall", 3, 3);
        let picks = game.policy(P0).declare_attackers(&game, P0, &[attacker]);
        results.push(!picks.is_empty());
    }
    assert_eq!(results, [true, false]);
}

#[test]
fn lookahead_stops_commander_suicide() {
    // a commander no longer attacks into a wall that trades it away for nothing
    let mut game = make_game(2);
    let cmd = sized(&mut game, P0, "General", 3, 3);
    game.obj_mut(cmd).commander = true;
    let open = game.policy(P0).declare_attackers(&game, P0, &[cmd]);
    assert_eq!(open, vec![(cmd, Defender::Player(P1))]); // open board: attack
    sized(&mut game, P1, "Wall", 3, 3);
    let walled = game.policy(P0).declare_attackers(&game, P0, &[cmd]);
    assert_eq!(walled, vec![]); // bad trade: hold back
}

#[test]
fn lookahead_allows_lethal_alpha() {
    let mut game = make_game(2);
    game.player_mut(P1).life = 3;
    let cmd = sized(&mut game, P0, "General", 3, 3);
    game.obj_mut(cmd).commander = true;
    sized(&mut game, P1, "Wall", 3, 3);
    let picks = game.policy(P0).declare_attackers(&game, P0, &[cmd]);
    assert_eq!(picks, vec![(cmd, Defender::Player(P1))]);
}

#[test]
fn grudge_influences_target() {
    let mut game = make_game(3);
    let b = PlayerId(2);
    let flyer = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Hawk",
            keywords: vec!["flying"],
            ..CreatureSpec::default()
        },
    );
    let b_name = game.player(b).name.clone();
    game.player_mut(P0).grudges.insert(b_name, 8.0);
    let pol = DefaultPolicy::new();
    assert_eq!(pol.attack_target(&game, P0, flyer), Some(b));
}

#[test]
fn race_life_chump_blocks() {
    let mut game = make_game(2);
    let pol = DefaultPolicy::with_profile(PolicyProfile {
        race_life: 14,
        ..PolicyProfile::default()
    });
    let big = sized(&mut game, P0, "Big", 4, 6);
    let small = sized(&mut game, P1, "Chump", 1, 1);
    game.player_mut(P1).life = 20; // safe: no chump block
    assert_eq!(pol.declare_blockers(&game, P1, &[big], &[small]), vec![]);
    game.player_mut(P1).life = 10; // racing: block anything
    assert_eq!(
        pol.declare_blockers(&game, P1, &[big], &[small]),
        vec![(small, big)]
    );
}
