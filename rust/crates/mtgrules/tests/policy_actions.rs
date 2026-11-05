//! The default policy only proposes engine-legal actions (fix P1),
//! keeps planeswalkers alive as a preference on top of loyalty legality
//! (P2) and plays flash creatures / non-counter instants at the end of
//! an opponent's turn (P4): port of the Python `test_policy_actions.py`.

mod common;

use common::{
    CardSpec, CreatureSpec, card_in_hand_with, creature_with, give_mana, make_game, set_phase,
};
use mtgrules::Game;
use mtgrules::abilities::{Ability, ActivatedAbility, SpellAbility};
use mtgrules::effects::Effect;
use mtgrules::objects::{ObjId, PlayerId};
use mtgrules::policy::Action;
use mtgrules::protocols::CountValue;

const P0: PlayerId = PlayerId(0);

fn draw_ability(tap_cost: bool, sorcery_only: bool) -> Ability {
    Ability::from(ActivatedAbility {
        effect: Some(Effect::draw(CountValue::Fixed(1))),
        text: "draw".to_owned(),
        tap_cost,
        sorcery_only,
        ..ActivatedAbility::default()
    })
}

fn choose(game: &Game) -> Option<Action> {
    game.policy(P0).choose_action(game, P0)
}

fn gain_life(n: i64) -> Ability {
    Ability::from(SpellAbility::new(Effect::GainLife {
        amount: CountValue::Fixed(n),
    }))
}

fn named(
    game: &mut Game,
    name: &'static str,
    abilities: Vec<Ability>,
    entered_this_turn: bool,
) -> ObjId {
    creature_with(
        game,
        P0,
        CreatureSpec {
            name,
            abilities,
            entered_this_turn,
            ..CreatureSpec::default()
        },
    )
}

// ---- P1: the legality precheck

#[test]
fn summoning_sick_tap_ability_is_not_proposed() {
    let mut game = make_game(2);
    set_phase(&mut game, "main2");
    let sick = named(&mut game, "Fresh", vec![draw_ability(true, false)], true);
    assert!(choose(&game).is_none());
    game.obj_mut(sick).entered_this_turn = false;
    assert!(matches!(choose(&game), Some(Action::Activate { .. })));
}

#[test]
fn sorcery_speed_ability_outside_the_window_is_not_proposed() {
    let mut game = make_game(2);
    set_phase(&mut game, "main2");
    named(&mut game, "Slow", vec![draw_ability(true, true)], false);
    assert!(matches!(choose(&game), Some(Action::Activate { .. })));
    set_phase(&mut game, "combat");
    assert!(choose(&game).is_none());
}

#[test]
fn additional_cost_that_cannot_be_paid_is_not_proposed() {
    let mut game = make_game(2);
    set_phase(&mut game, "main1");
    give_mana(&mut game, P0, &[("C", 2)]);
    let spell = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Altar",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: vec![gain_life(5)],
            ..CardSpec::default()
        },
    );
    "sacrifice_creature".clone_into(&mut game.obj_mut(spell).base_mut().additional_cost);
    assert!(choose(&game).is_none());
    creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Fodder",
            power: 1,
            toughness: 1,
            ..CreatureSpec::default()
        },
    );
    assert!(matches!(choose(&game), Some(Action::Cast { .. })));
}

#[test]
fn proposed_actions_are_accepted_by_the_engine() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    named(&mut game, "Ready", vec![draw_ability(true, false)], false);
    card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Heal",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: vec![gain_life(1)],
            ..CardSpec::default()
        },
    );
    set_phase(&mut game, "main1");
    let mut performed = 0;
    for _ in 0..4 {
        let Some(action) = choose(&game) else {
            break;
        };
        // the engine's own executor is the oracle here
        assert!(game.perform(P0, action.clone()), "{action:?}");
        performed += 1;
        while !game.stack.is_empty() {
            game.resolve_top();
        }
    }
    assert!(performed >= 1);
}

// ---- P2: loyalty

fn walker(game: &mut Game, loyalty: i64) -> ObjId {
    let ability = Ability::from(ActivatedAbility {
        loyalty_cost: Some(-3),
        effect: Some(Effect::draw(CountValue::Fixed(1))),
        text: "-3: draw".to_owned(),
        ..ActivatedAbility::default()
    });
    let w = creature_with(
        game,
        P0,
        CreatureSpec {
            name: "Walker",
            power: 0,
            toughness: 0,
            types: vec!["Planeswalker"],
            abilities: vec![ability],
            ..CreatureSpec::default()
        },
    );
    game.obj_mut(w)
        .counters
        .insert("loyalty".to_owned(), loyalty);
    w
}

#[test]
fn lethal_minus_ability_is_legal_but_declined() {
    let mut game = make_game(2);
    set_phase(&mut game, "main1");
    let w = walker(&mut game, 3);
    let ab = game.obj(w).base.abilities[0]
        .as_activated()
        .cloned()
        .expect("an activated ability");
    assert!(game.activation_legal(P0, w, &ab));
    assert!(choose(&game).is_none());
    game.obj_mut(w).counters.insert("loyalty".to_owned(), 4);
    assert!(matches!(choose(&game), Some(Action::Activate { .. })));
}

#[test]
fn loyalty_ability_only_in_the_sorcery_window() {
    let mut game = make_game(2);
    walker(&mut game, 5);
    set_phase(&mut game, "end");
    assert!(choose(&game).is_none());
}

// ---- P4: tactical flash play at the end of an opponent's turn

fn opponents_end_step() -> Game {
    let mut game = make_game(2);
    game.active_idx = 1;
    set_phase(&mut game, "end");
    give_mana(&mut game, P0, &[("C", 3)]);
    game
}

fn in_hand(
    game: &mut Game,
    name: &'static str,
    types: &[&'static str],
    effect: Effect,
    keywords: &[&str],
) -> ObjId {
    let card = card_in_hand_with(
        game,
        P0,
        CardSpec {
            name,
            mana_cost: "{1}",
            types: types.to_vec(),
            abilities: vec![Ability::from(SpellAbility::new(effect))],
            power: Some(2),
            toughness: Some(2),
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

fn gain(n: i64) -> Effect {
    Effect::GainLife {
        amount: CountValue::Fixed(n),
    }
}

fn cast_of(action: Option<&Action>) -> Option<ObjId> {
    match action {
        Some(Action::Cast { card, .. }) => Some(*card),
        _ => None,
    }
}

#[test]
fn flash_creature_is_cast_at_the_opponents_end_step() {
    let mut game = opponents_end_step();
    let ambusher = in_hand(&mut game, "Ambusher", &["Creature"], gain(0), &["flash"]);
    let action = choose(&game).expect("a cast");
    assert_eq!(cast_of(Some(&action)), Some(ambusher));
    assert!(game.perform(P0, action));
}

#[test]
fn non_flash_creature_waits() {
    let mut game = opponents_end_step();
    in_hand(&mut game, "Bear", &["Creature"], gain(0), &[]);
    assert!(choose(&game).is_none());
}

#[test]
fn instant_is_cast_but_counterspell_is_held() {
    let mut game = opponents_end_step();
    in_hand(
        &mut game,
        "Counter",
        &["Instant"],
        Effect::CounterSpell,
        &[],
    );
    assert!(choose(&game).is_none());
    let heal = in_hand(&mut game, "Heal", &["Instant"], gain(3), &[]);
    assert_eq!(cast_of(choose(&game).as_ref()), Some(heal));
}

#[test]
fn not_in_the_ai_s_own_end_step_or_in_combat() {
    let mut game = opponents_end_step();
    in_hand(&mut game, "Ambusher", &["Creature"], gain(0), &["flash"]);
    set_phase(&mut game, "combat");
    assert!(choose(&game).is_none());
    set_phase(&mut game, "end");
    game.active_idx = 0;
    assert!(choose(&game).is_none());
    assert_eq!(game.player(P0).hand.len(), 1);
}
