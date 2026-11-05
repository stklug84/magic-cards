//! Infect, toxic, the poison SBA (CR 702.90, 702.164, 704.5c) and spell
//! copies (CR 707.10): the engine half of the Python
//! `test_702_707_mechanics.py`. (The compiler-template cases land with
//! the compiler port.)

mod common;

use common::{CardSpec, CreatureSpec, card_in_hand_with, creature_with, give_mana, make_game};
use mtgrules::abilities::{Ability, SpellAbility};
use mtgrules::effects::Effect;
use mtgrules::objects::{PlayerId, Zone};
use mtgrules::protocols::CountValue;
use mtgrules::stack::{StackObject, Target};

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

#[test]
fn rule_702_90b_infect_damage_is_poison() {
    let mut game = make_game(2);
    let atk = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Blighted",
            power: 3,
            keywords: vec!["infect"],
            ..CreatureSpec::default()
        },
    );
    game.deal_damage(Some(atk), Target::Player(P1), 3, true);
    assert_eq!(game.player(P1).poison, 3);
    assert_eq!(game.player(P1).life, 40); // no life loss
}

#[test]
fn rule_702_164_toxic_adds_poison() {
    let mut game = make_game(2);
    let atk = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Rat",
            power: 1,
            keywords: vec!["toxic:2"],
            ..CreatureSpec::default()
        },
    );
    game.deal_damage(Some(atk), Target::Player(P1), 1, true);
    assert_eq!(game.player(P1).life, 39); // normal damage too
    assert_eq!(game.player(P1).poison, 2);
}

#[test]
fn rule_704_5c_ten_poison_loses() {
    let mut game = make_game(2);
    game.player_mut(P1).poison = 10;
    game.check_state_based_actions();
    assert!(game.player(P1).lost);
    assert!(game.player(P1).lose_reason.contains("704.5c"));
}

#[test]
fn rule_707_10_copy_resolves_and_ceases() {
    let mut game = make_game(2);
    let spell = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Rite",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: vec![Ability::from(SpellAbility::new(Effect::GainLife {
                amount: CountValue::Fixed(3),
            }))],
            ..CardSpec::default()
        },
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    let item = game.stack.last().expect("the spell is on the stack").id;
    let copy = game.copy_spell(item, P1).expect("copy_spell made a copy");
    assert_eq!(game.stack.len(), 2);
    let copy_obj = match game.stack_item(copy).expect("the copy is on the stack").obj {
        StackObject::Spell(obj) => obj,
        ref other => panic!("the copy is not a spell: {other:?}"),
    };
    assert!(game.obj(copy_obj).is_copy);
    game.resolve_top(); // the copy (p1's) resolves
    game.resolve_top(); // the original resolves
    assert_eq!(game.player(P1).life, 43);
    assert_eq!(game.player(P0).life, 43);
    // rules 704.5e / 707.10a: the copy is in no graveyard
    assert!(!game.player(P1).graveyard.contains(&copy_obj));
    assert!(!game.player(P0).graveyard.contains(&copy_obj));
    assert_eq!(game.obj(copy_obj).zone, Zone::Ceased);
    assert_eq!(game.obj(spell).zone, Zone::Graveyard);
}
