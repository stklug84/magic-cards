//! Keyword abilities (CR 702) and casting timing (CR 117.1a, 307.1,
//! 702.8): port of the Python `test_702_keywords.py` - lifelink,
//! exalted, the `alone` flag of the attack event, fear / intimidate /
//! landwalk, and sorcery-speed timing. The attack declarations use a
//! scripted all-out policy where Python relies on the default one.

mod common;

use std::cell::RefCell;
use std::rc::Rc;

use common::{
    CardSpec, CreatureSpec, FakeRef, card_in_hand_with, creature, creature_with, give_mana,
    make_game, set_phase, settle,
};
use mtgrules::Game;
use mtgrules::abilities::{Ability, SpellAbility, TriggerSpec, TriggeredAbility};
use mtgrules::combat::{CombatPhase, can_block};
use mtgrules::compiler::compile_card;
use mtgrules::effects::Effect;
use mtgrules::events::{Event, EventType};
use mtgrules::objects::{Defender, ObjId, PlayerId};
use mtgrules::policy::{Policy, TrivialPolicy};
use mtgrules::protocols::CountValue;
use mtgrules::replacements::Replacement;
use mtgrules::stack::Target;
use pycompat::random::PyRandom;

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

// ---- lifelink (rule 702.15b)

#[test]
fn lifelink_combat_damage_gains_life() {
    let mut game = make_game(2);
    set_phase(&mut game, "combat");
    let a = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["lifelink"],
            ..CreatureSpec::default()
        },
    );
    game.obj_mut(a).attacking = Some(Defender::Player(P1));
    let mut phase = CombatPhase::new();
    phase.attackers = vec![a];
    phase.damage_step(&mut game, false);
    settle(&mut game);
    assert_eq!(game.player(P1).life, 38);
    assert_eq!(game.player(P0).life, 42);
}

#[test]
fn lifelink_noncombat_damage_gains_life() {
    let mut game = make_game(2);
    let src = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            power: 3,
            toughness: 3,
            keywords: vec!["lifelink"],
            ..CreatureSpec::default()
        },
    );
    game.deal_damage(Some(src), Target::Player(P1), 3, false);
    assert_eq!(game.player(P0).life, 43);
}

#[test]
fn lifelink_gain_can_be_prevented() {
    let mut game = make_game(2);
    // prevent all life gain: the gain goes through the life-gain event
    game.add_floating_replacement(Replacement::new(EventType::GainLife, |_g, _e| None));
    let src = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            power: 3,
            toughness: 3,
            keywords: vec!["lifelink"],
            ..CreatureSpec::default()
        },
    );
    game.deal_damage(Some(src), Target::Player(P1), 3, false);
    assert_eq!(game.player(P0).life, 40);
    assert_eq!(game.player(P1).life, 37);
}

// ---- the attack event (rule 508.1, 702.83b "attacks alone")

/// Attack the first opponent with every candidate.
#[derive(Debug)]
struct AllOut;

impl Policy for AllOut {
    fn declare_attackers(
        &self,
        game: &Game,
        player: PlayerId,
        candidates: &[ObjId],
    ) -> Vec<(ObjId, Defender)> {
        let target = (0..game.players.len())
            .map(PlayerId)
            .find(|p| *p != player && !game.player(*p).lost)
            .expect("an opponent is still in the game");
        candidates
            .iter()
            .map(|c| (*c, Defender::Player(target)))
            .collect()
    }
}

#[test]
fn attack_events_carry_alone() {
    let mut game = Game::new(
        &["p0", "p1"],
        PyRandom::new(7),
        &[Rc::new(AllOut), Rc::new(TrivialPolicy)],
        40,
    );
    set_phase(&mut game, "combat");
    let seen: Rc<RefCell<Vec<bool>>> = Rc::new(RefCell::new(Vec::new()));
    let probe = Rc::clone(&seen);
    let record = move |_g: &Game, _src: ObjId, ev: &Event| {
        probe.borrow_mut().push(ev.data.alone.unwrap_or(false));
        false
    };
    creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Watcher",
            entered_this_turn: true, // cannot attack this turn
            abilities: vec![Ability::from(TriggeredAbility::new(
                TriggerSpec::when(EventType::Attacks, Rc::new(record)),
                Effect::Noop {
                    note: String::new(),
                },
            ))],
            ..CreatureSpec::default()
        },
    );
    let knight = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Knight",
            ..CreatureSpec::default()
        },
    );
    CombatPhase::new().declare_attackers(&mut game);
    assert_eq!(game.obj(knight).attacking, Some(Defender::Player(P1)));
    assert_eq!(*seen.borrow(), [true]);
}

#[test]
fn two_attackers_are_not_alone() {
    let mut game = Game::new(
        &["p0", "p1"],
        PyRandom::new(7),
        &[Rc::new(AllOut), Rc::new(TrivialPolicy)],
        40,
    );
    set_phase(&mut game, "combat");
    let seen: Rc<RefCell<Vec<bool>>> = Rc::new(RefCell::new(Vec::new()));
    let probe = Rc::clone(&seen);
    let record = move |_g: &Game, _src: ObjId, ev: &Event| {
        probe.borrow_mut().push(ev.data.alone.unwrap_or(false));
        false
    };
    creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Watcher",
            entered_this_turn: true,
            abilities: vec![Ability::from(TriggeredAbility::new(
                TriggerSpec::when(EventType::Attacks, Rc::new(record)),
                Effect::Noop {
                    note: String::new(),
                },
            ))],
            ..CreatureSpec::default()
        },
    );
    creature(&mut game, P0, 2, 2);
    creature(&mut game, P0, 2, 2);
    CombatPhase::new().declare_attackers(&mut game);
    assert_eq!(*seen.borrow(), [false, false]);
}

// ---- evasion (rule 509.1b): fear, intimidate, landwalk

fn colored(game: &mut Game, player: PlayerId, colors: &[&'static str]) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            colors: colors.to_vec(),
            ..CreatureSpec::default()
        },
    )
}

fn robot(game: &mut Game, player: PlayerId) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            types: vec!["Artifact", "Creature"],
            ..CreatureSpec::default()
        },
    )
}

#[test]
fn rule_702_36b_fear() {
    let mut game = make_game(2);
    let a = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["fear"],
            colors: vec!["B"],
            ..CreatureSpec::default()
        },
    );
    let white = colored(&mut game, P1, &["W"]);
    let black = colored(&mut game, P1, &["B"]);
    let robot = robot(&mut game, P1);
    assert!(!can_block(&game, white, a));
    assert!(can_block(&game, black, a));
    assert!(can_block(&game, robot, a));
}

#[test]
fn rule_702_13b_intimidate() {
    let mut game = make_game(2);
    let a = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["intimidate"],
            colors: vec!["R", "G"],
            ..CreatureSpec::default()
        },
    );
    let blue = colored(&mut game, P1, &["U"]);
    let green = colored(&mut game, P1, &["G", "W"]);
    let robot = robot(&mut game, P1);
    assert!(!can_block(&game, blue, a));
    assert!(can_block(&game, green, a));
    assert!(can_block(&game, robot, a));
}

fn land(game: &mut Game, player: PlayerId, name: &'static str) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            name,
            types: vec!["Land"],
            subtypes: vec![name],
            ..CreatureSpec::default()
        },
    )
}

#[test]
fn rule_702_14c_landwalk() {
    let mut game = make_game(2);
    let a = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["islandwalk"],
            ..CreatureSpec::default()
        },
    );
    let blocker = creature(&mut game, P1, 2, 2);
    assert!(can_block(&game, blocker, a));
    land(&mut game, P1, "Island");
    assert!(!can_block(&game, blocker, a));
    // the attacker's own lands do not matter
    land(&mut game, P0, "Swamp");
    let b2 = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            keywords: vec!["swampwalk"],
            ..CreatureSpec::default()
        },
    );
    assert!(can_block(&game, blocker, b2));
}

// ---- timing (rules 117.1a, 307.1, 702.8)

fn spell(game: &mut Game, player: PlayerId, types: &[&'static str], keywords: &[&str]) -> ObjId {
    let card = card_in_hand_with(
        game,
        player,
        CardSpec {
            name: "Thing",
            mana_cost: "{1}",
            types: types.to_vec(),
            abilities: vec![Ability::from(SpellAbility::new(Effect::GainLife {
                amount: CountValue::Fixed(1),
            }))],
            ..CardSpec::default()
        },
    );
    for kw in keywords {
        game.obj_mut(card)
            .base_mut()
            .keywords
            .insert((*kw).to_owned());
    }
    card
}

#[test]
fn sorcery_only_in_own_main_phase_with_empty_stack() {
    let mut game = make_game(2);
    set_phase(&mut game, "main1");
    give_mana(&mut game, P0, &[("C", 1)]);
    give_mana(&mut game, P1, &[("C", 1)]);
    let mine = spell(&mut game, P0, &["Sorcery"], &[]);
    let theirs = spell(&mut game, P1, &["Sorcery"], &[]);
    assert!(!game.cast_spell(P1, theirs, 0, false));
    assert!(game.cast_spell(P0, mine, 0, false));
    // the stack is no longer empty: a second sorcery must wait
    give_mana(&mut game, P0, &[("C", 1)]);
    let second = spell(&mut game, P0, &["Sorcery"], &[]);
    assert!(!game.cast_spell(P0, second, 0, false));
}

#[test]
fn sorcery_not_in_combat() {
    let mut game = make_game(2);
    set_phase(&mut game, "combat");
    give_mana(&mut game, P0, &[("C", 1)]);
    let card = spell(&mut game, P0, &["Sorcery"], &[]);
    assert!(!game.cast_spell(P0, card, 0, false));
}

#[test]
fn instant_any_time_with_priority() {
    let mut game = make_game(2);
    set_phase(&mut game, "combat");
    give_mana(&mut game, P1, &[("C", 1)]);
    let card = spell(&mut game, P1, &["Instant"], &[]);
    assert!(game.cast_spell(P1, card, 0, false));
}

#[test]
fn flash_creature_at_instant_speed() {
    let mut game = make_game(2);
    set_phase(&mut game, "end");
    give_mana(&mut game, P1, &[("C", 1)]);
    let card = spell(&mut game, P1, &["Creature"], &["flash"]);
    assert!(game.cast_spell(P1, card, 0, false));
    give_mana(&mut game, P1, &[("C", 1)]);
    let slow = spell(&mut game, P1, &["Creature"], &[]);
    assert!(!game.cast_spell(P1, slow, 0, false));
}

// ---- exalted (rule 702.83)

fn all_out_game() -> Game {
    let mut game = Game::new(
        &["p0", "p1"],
        PyRandom::new(7),
        &[Rc::new(AllOut), Rc::new(TrivialPolicy)],
        40,
    );
    set_phase(&mut game, "combat");
    game
}

/// A compiled exalted permanent that entered this turn (cannot attack).
fn exalted(game: &mut Game, name: &'static str) {
    let ch = compile_card(&FakeRef::new(name, "Exalted").card());
    let id = game.create_object(ch, P0, false, None);
    game.obj_mut(id).controller = P0;
    game.obj_mut(id).entered_this_turn = true;
    game.place(id, P0, mtgrules::objects::Zone::Battlefield);
}

#[test]
fn exalted_compiles_to_a_trigger() {
    let ch = compile_card(&FakeRef::new("Exalted Guy", "Exalted").card());
    assert!(ch.keywords.contains("exalted"));
    assert_eq!(
        ch.abilities
            .iter()
            .filter(|a| a.as_triggered().is_some())
            .count(),
        1
    );
}

#[test]
fn exalted_attacking_alone_gets_the_bonus() {
    let mut game = all_out_game();
    let attacker = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Knight",
            ..CreatureSpec::default()
        },
    );
    exalted(&mut game, "Angel");
    exalted(&mut game, "Paladin");
    game.bump();
    CombatPhase::new().declare_attackers(&mut game); // the declaration fires exalted
    settle(&mut game);
    assert_eq!(game.obj(attacker).attacking, Some(Defender::Player(P1)));
    assert_eq!(common::pt(&game, attacker), (Some(4), Some(4)));
    game.end_of_turn_cleanup();
    assert_eq!(common::pt(&game, attacker), (Some(2), Some(2)));
}

#[test]
fn exalted_two_attackers_get_nothing() {
    let mut game = all_out_game();
    let a = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Knight",
            ..CreatureSpec::default()
        },
    );
    let b = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Squire",
            ..CreatureSpec::default()
        },
    );
    exalted(&mut game, "Angel");
    game.bump();
    CombatPhase::new().declare_attackers(&mut game);
    settle(&mut game);
    assert_eq!(game.obj(a).attacking, Some(Defender::Player(P1)));
    assert_eq!(game.obj(b).attacking, Some(Defender::Player(P1)));
    assert_eq!(game.chars(a).power, Some(2));
    assert_eq!(game.chars(b).power, Some(2));
}

#[test]
fn every_basic_landwalk_parses() {
    let ch = compile_card(
        &FakeRef::new(
            "Walker",
            "Plainswalk, islandwalk, swampwalk\nMountainwalk\nForestwalk",
        )
        .card(),
    );
    let walks: Vec<&str> = ch
        .keywords
        .iter()
        .map(String::as_str)
        .filter(|k| k.ends_with("walk"))
        .collect();
    assert_eq!(
        walks,
        [
            "forestwalk",
            "islandwalk",
            "mountainwalk",
            "plainswalk",
            "swampwalk"
        ]
    );
}
