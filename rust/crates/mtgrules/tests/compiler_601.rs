//! The oracle-text compiler: cost parsing (fix F5) and target counts
//! (fix F11) - the compiler half of the Python `test_601_targets.py` -
//! plus the engine cases that need no AI choice (608.2b rechecks). The
//! cases that depend on the default policy's target choice land with it.

mod common;

use std::cell::RefCell;
use std::rc::Rc;

use common::{
    CardSpec, CreatureSpec, FakeRef, card_in_hand_with, creature_with, give_mana, make_game, settle,
};
use mtgrules::abilities::{Ability, ActivatedAbility, SpellAbility, TargetSpec};
use mtgrules::compiler::{self, compile_card, parse_effect_text};
use mtgrules::effects::Effect;
use mtgrules::objects::{ObjId, PlayerId, Zone};

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

fn activated(card: &FakeRef) -> Vec<Rc<ActivatedAbility>> {
    compile_card(&card.card())
        .abilities
        .iter()
        .filter_map(|a| a.as_activated().cloned())
        .collect()
}

// ---- F5: only real, engine-charged costs make an activated ability

#[test]
fn modal_trigger_is_not_a_free_activated_ability() {
    let ch = compile_card(
        &FakeRef::new(
            "Modal",
            "When this creature enters, choose one: Draw a card.",
        )
        .card(),
    );
    assert!(ch.abilities.iter().all(|a| a.as_activated().is_none()));
    assert!(ch.abilities.iter().any(|a| a.as_triggered().is_some()));
}

#[test]
fn unmodelled_cost_component_is_reported() {
    compiler::clear_unknown_clauses();
    assert!(
        activated(&FakeRef::new(
            "Looter",
            "{1}, {T}, Discard a card: Draw a card."
        ))
        .is_empty()
    );
    let unknown = compiler::unknown_clauses();
    assert!(unknown["Looter"].contains("{1}, {T}, Discard a card: Draw a card."));
}

#[test]
fn untap_symbol_is_not_charged() {
    assert!(activated(&FakeRef::new("Untapper", "{Q}: Draw a card.")).is_empty());
}

#[test]
fn charged_costs_still_compile() {
    compiler::clear_unknown_clauses();
    let [a] = &activated(&FakeRef::new("Drawer", "{2}, {T}: Draw a card."))[..] else {
        panic!("one activated ability");
    };
    assert_eq!((a.cost().generic, a.tap_cost), (2, true));
    let [b] = &activated(&FakeRef {
        types: vec!["Artifact"],
        ..FakeRef::new("Sacker", "{T}, Sacrifice this artifact: Draw a card.")
    })[..] else {
        panic!("one activated ability");
    };
    assert_eq!(b.sac_cost, "self");
    let [c] = &activated(&FakeRef::new("Lifer", "Pay 2 life, {T}: Draw a card."))[..] else {
        panic!("one activated ability");
    };
    assert_eq!((c.life_cost, c.tap_cost), (2, true));
    assert!(!compiler::unknown_clauses().contains_key("Drawer"));
}

// ---- F11: the compiler reads how many targets a "target" word allows

const FORKED_TEXT: &str =
    "This spell deals 4 damage divided as you choose among one, two, or three targets.";

fn spec(text: &str) -> TargetSpec {
    let (_effect, targets) = parse_effect_text(text, "Spell");
    assert_eq!(targets.len(), 1, "{text}");
    targets.into_iter().next().expect("one target spec")
}

#[test]
fn single_target() {
    let s = spec("Destroy target creature.");
    assert_eq!((s.what.as_str(), s.count, s.minimum()), ("creature", 1, 1));
}

#[test]
fn up_to_n() {
    let s = spec("Destroy up to two target creatures.");
    assert_eq!((s.count, s.minimum(), s.optional), (2, 0, true));
}

#[test]
fn exactly_n() {
    let s = spec("Destroy two target creatures.");
    assert_eq!((s.count, s.minimum()), (2, 2));
}

#[test]
fn any_number() {
    let s = spec("Exile any number of target creatures you control.");
    assert!(s.count >= 99);
    assert_eq!((s.minimum(), s.controller.as_str()), (0, "you"));
}

#[test]
fn one_or_two() {
    let s = spec("Tap one or two target creatures.");
    assert_eq!((s.count, s.minimum()), (2, 1));
}

#[test]
fn any_target() {
    let (effect, targets) = parse_effect_text("This spell deals 3 damage to any target.", "Bolt");
    assert!(matches!(effect, Effect::DealDamage { .. }));
    assert_eq!((targets[0].what.as_str(), targets[0].count), ("any", 1));
}

#[test]
fn divided_damage_spec() {
    let (effect, targets) = parse_effect_text(FORKED_TEXT, "Forked");
    let Effect::DealDamage { to, .. } = &effect else {
        panic!("not a damage effect: {effect:?}");
    };
    assert_eq!(to, "divided");
    assert_eq!(
        (
            targets[0].what.as_str(),
            targets[0].count,
            targets[0].minimum()
        ),
        ("any", 3, 1)
    );
}

// ---- F11 at run time and F7 (608.2b rechecks), with the trivial policy

fn spell(game: &mut mtgrules::Game, text: &str) -> ObjId {
    let (effect, targets) = parse_effect_text(text, "Spell");
    let mut ability = SpellAbility::new(effect);
    ability.targets = targets;
    card_in_hand_with(
        game,
        P0,
        CardSpec {
            name: "Spell",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    )
}

fn enemy(game: &mut mtgrules::Game, name: &'static str, size: i64) -> ObjId {
    creature_with(
        game,
        P1,
        CreatureSpec {
            name,
            power: size,
            toughness: size,
            ..CreatureSpec::default()
        },
    )
}

#[test]
fn required_count_must_be_met() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    enemy(&mut game, "Only", 2);
    let s = spell(&mut game, "Destroy two target creatures.");
    assert!(!game.cast_spell(P0, s, 0, false));
    assert_eq!(game.obj(s).zone, Zone::Hand);
}

#[test]
fn partial_illegality_resolves_the_rest() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    let a = enemy(&mut game, "A", 4);
    let b = enemy(&mut game, "B", 4);
    let s = spell(&mut game, "Destroy two target creatures.");
    assert!(game.cast_spell(P0, s, 0, false));
    game.move_zone(a, Zone::Hand); // bounced in response
    settle(&mut game);
    assert_eq!(game.obj(a).zone, Zone::Hand);
    assert_eq!(game.obj(b).zone, Zone::Graveyard);
    assert_eq!(game.obj(s).zone, Zone::Graveyard);
}

#[test]
fn all_targets_illegal_fizzles() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    let a = enemy(&mut game, "A", 4);
    let s = spell(&mut game, "Destroy up to two target creatures.");
    assert!(game.cast_spell(P0, s, 0, false));
    game.move_zone(a, Zone::Hand);
    let seen: Rc<RefCell<Vec<String>>> = Rc::new(RefCell::new(Vec::new()));
    let probe = Rc::clone(&seen);
    game.set_log(Box::new(move |_game, kind, _fields| {
        probe.borrow_mut().push(kind.to_owned());
    }));
    settle(&mut game);
    assert!(seen.borrow().iter().any(|k| k == "fizzle"));
    assert!(!seen.borrow().iter().any(|k| k == "resolve"));
}

#[test]
fn hexproof_gained_in_response_fizzles_the_spell() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let shy = enemy(&mut game, "Shy", 2);
    let mut ability = SpellAbility::new(Effect::destroy());
    ability.targets = vec![TargetSpec::new("creature")];
    let murder = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Murder",
            mana_cost: "{1}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    );
    assert!(game.cast_spell(P0, murder, 0, false));
    game.obj_mut(shy)
        .base_mut()
        .keywords
        .insert("hexproof".to_owned()); // "in response"
    game.bump();
    settle(&mut game);
    assert_eq!(game.obj(shy).zone, Zone::Battlefield);
    assert_eq!(game.obj(murder).zone, Zone::Graveyard);
}

#[test]
fn controller_change_makes_an_opponent_only_target_illegal() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let stolen = enemy(&mut game, "Stolen", 2);
    let mut ability = SpellAbility::new(Effect::destroy());
    ability.targets = vec![TargetSpec {
        controller: "opponent".to_owned(),
        ..TargetSpec::new("creature")
    }];
    let smite = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Smite",
            mana_cost: "{1}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    );
    assert!(game.cast_spell(P0, smite, 0, false));
    // gained control in response
    game.obj_mut(stolen).controller = P0;
    game.player_mut(P1).battlefield.retain(|o| *o != stolen);
    game.player_mut(P0).battlefield.push(stolen);
    game.bump();
    settle(&mut game);
    assert_eq!(game.obj(stolen).zone, Zone::Battlefield);
}

// ---- the default policy's target choices (F11 at run time, F9, P3)

use mtgrules::policy::is_beneficial;
use mtgrules::protocols::CountValue;
use mtgrules::stack::Target;

#[test]
fn up_to_two_destroys_the_two_biggest_threats() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    let small = enemy(&mut game, "Small", 1);
    let mid = enemy(&mut game, "Mid", 3);
    let big = enemy(&mut game, "Big", 6);
    let s = spell(&mut game, "Destroy up to two target creatures.");
    assert!(game.cast_spell(P0, s, 0, false));
    let item = game.stack.last().expect("the spell");
    assert_eq!(item.targets.len(), 2);
    let mut distinct = item.targets.clone();
    distinct.dedup();
    assert_eq!(distinct.len(), 2); // rule 115.3
    settle(&mut game);
    assert_eq!(game.obj(big).zone, Zone::Graveyard);
    assert_eq!(game.obj(mid).zone, Zone::Graveyard);
    assert_eq!(game.obj(small).zone, Zone::Battlefield);
}

#[test]
fn optional_targets_are_only_good_ones() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 3)]);
    creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Mine",
            power: 5,
            toughness: 5,
            ..CreatureSpec::default()
        },
    );
    let theirs = enemy(&mut game, "Theirs", 2);
    let s = spell(&mut game, "Destroy up to two target creatures.");
    assert!(game.cast_spell(P0, s, 0, false));
    assert_eq!(
        game.stack.last().expect("the spell").targets,
        vec![Target::Object(theirs)]
    );
}

fn forked(game: &mut mtgrules::Game) -> ObjId {
    let (effect, targets) = parse_effect_text(FORKED_TEXT, "Forked");
    let mut ability = SpellAbility::new(effect);
    ability.targets = targets;
    card_in_hand_with(
        game,
        P0,
        CardSpec {
            name: "Forked",
            mana_cost: "{1}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    )
}

#[test]
fn division_is_fixed_at_casting() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    enemy(&mut game, "Two", 2);
    let three = enemy(&mut game, "Three", 3);
    let s = forked(&mut game);
    assert!(game.cast_spell(P0, s, 0, false));
    let item = game.stack.last().expect("the spell");
    // 1 point can't kill the 2/2
    assert_eq!(
        item.targets,
        vec![Target::Object(three), Target::Player(P1)]
    );
    assert_eq!(item.division, vec![3, 1]);
}

#[test]
fn a_vanished_target_s_share_is_lost() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let a = enemy(&mut game, "A", 2);
    let b = enemy(&mut game, "B", 2);
    let s = forked(&mut game);
    assert!(game.cast_spell(P0, s, 0, false));
    assert_eq!(game.stack.last().expect("the spell").division, vec![2, 2]);
    game.move_zone(a, Zone::Hand);
    settle(&mut game);
    assert_eq!(game.obj(a).zone, Zone::Hand);
    assert_eq!(game.obj(b).zone, Zone::Graveyard);
    assert_eq!(game.player(P1).life, 40);
}

#[test]
fn player_is_a_legal_divided_target() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let s = forked(&mut game);
    assert!(game.cast_spell(P0, s, 0, false));
    assert_eq!(
        game.stack.last().expect("the spell").targets,
        vec![Target::Player(P1)]
    );
    settle(&mut game);
    assert_eq!(game.player(P1).life, 36);
}

fn targeted_spell(game: &mut mtgrules::Game, name: &'static str, effect: Effect) -> ObjId {
    let mut ability = SpellAbility::new(effect);
    ability.targets = vec![TargetSpec::new("creature")];
    card_in_hand_with(
        game,
        P0,
        CardSpec {
            name,
            mana_cost: "{1}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    )
}

#[test]
fn forced_harmful_target_is_the_least_valuable_own_creature() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let weak = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Weak",
            power: 1,
            toughness: 1,
            ..CreatureSpec::default()
        },
    );
    let strong = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Strong",
            power: 5,
            toughness: 5,
            ..CreatureSpec::default()
        },
    );
    let murder = targeted_spell(&mut game, "Murder", Effect::destroy());
    assert!(game.cast_spell(P0, murder, 0, false));
    settle(&mut game);
    assert_eq!(game.obj(weak).zone, Zone::Graveyard);
    assert_eq!(game.obj(strong).zone, Zone::Battlefield);
}

#[test]
fn forced_beneficial_target_is_the_least_valuable_enemy() {
    let mut game = make_game(2);
    give_mana(&mut game, P0, &[("C", 2)]);
    let weak = enemy(&mut game, "Weak", 1);
    let strong = enemy(&mut game, "Strong", 5);
    let grow = targeted_spell(
        &mut game,
        "Grow",
        Effect::counters("+1/+1", CountValue::Fixed(1)),
    );
    assert!(game.cast_spell(P0, grow, 0, false));
    settle(&mut game);
    assert_eq!(game.obj(weak).counter("+1/+1"), 1);
    assert_eq!(game.obj(strong).counter("+1/+1"), 0);
}

#[test]
fn untap_is_beneficial() {
    assert!(is_beneficial(Some(&Effect::Untap {
        on: "target".to_owned()
    })));
    assert!(!is_beneficial(Some(&Effect::destroy())));
}
