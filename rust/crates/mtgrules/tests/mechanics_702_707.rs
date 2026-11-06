//! Infect, toxic, the poison SBA (CR 702.90, 702.164, 704.5c), spell
//! copies (CR 707.10), cycling (702.29a), cast modifiers (601.2b/f) and
//! the compiler's removal-rider and dies-with-counter templates: port of
//! the Python `test_702_707_mechanics.py`.

mod common;

use std::rc::Rc;

use common::{
    CardSpec, CreatureSpec, FakeRef, card_in_hand_with, card_in_library, creature, creature_with,
    give_mana, make_game, settle,
};
use mtgrules::Game;
use mtgrules::abilities::{Ability, SpellAbility};
use mtgrules::compiler::compile_card;
use mtgrules::effects::Effect;
use mtgrules::objects::{Characteristics, ObjId, PlayerId, Zone};
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

// ---- the compiler templates

/// The parts of a spell's effect (a `Sequence` flattened one level).
fn spell_parts(ch: &Characteristics) -> Vec<&Effect> {
    let sa = ch
        .abilities
        .iter()
        .find_map(|a| a.as_spell())
        .expect("a spell ability");
    match &sa.effect {
        Effect::Sequence(parts) => parts.iter().collect(),
        other => vec![other],
    }
}

/// A compiled card on the battlefield under `player`'s control.
fn permanent(game: &mut Game, ch: &Characteristics, player: PlayerId) -> ObjId {
    let id = game.create_object(ch.clone(), player, false, None);
    game.obj_mut(id).controller = player;
    game.place(id, player, Zone::Battlefield);
    id
}

/// A compiled card in `player`'s hand.
fn in_hand(game: &mut Game, ch: &Characteristics, player: PlayerId) -> ObjId {
    let id = game.create_object(ch.clone(), player, false, None);
    game.place(id, player, Zone::Hand);
    id
}

#[test]
fn compiler_copy_template() {
    let ch = compile_card(
        &FakeRef {
            types: vec!["Instant"],
            ..FakeRef::new(
                "Fork2",
                "Copy target instant or sorcery spell. You may choose new targets for the copy.",
            )
        }
        .card(),
    );
    assert!(
        spell_parts(&ch)
            .iter()
            .any(|p| matches!(p, Effect::CopySpell { .. }))
    );
    let sa = ch
        .abilities
        .iter()
        .find_map(|a| a.as_spell())
        .expect("a spell");
    assert_eq!(sa.targets[0].what, "spell");
}

#[test]
fn uncounterable_static() {
    let mut game = make_game(2);
    let ch = compile_card(
        &FakeRef {
            types: vec!["Artifact"],
            ..FakeRef::new("Chimil2", "Spells you control can't be countered.")
        }
        .card(),
    );
    let st = ch
        .abilities
        .iter()
        .find_map(|a| a.as_static())
        .expect("a static ability");
    assert!(st.uncounterable_spells);
    // runtime: counter_spell is a no-op
    permanent(&mut game, &ch, P0);
    let spell = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Rite",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: vec![Ability::from(SpellAbility::new(Effect::GainLife {
                amount: CountValue::Fixed(1),
            }))],
            ..CardSpec::default()
        },
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    let top = game.stack.last().expect("the spell").id;
    game.counter_spell(top);
    assert_eq!(game.stack.len(), 1); // still there
}

#[test]
fn rule_702_29a_cycle_from_hand() {
    let mut game = make_game(2);
    let ch = compile_card(
        &FakeRef {
            types: vec!["Land"],
            mana_cost: "",
            ..FakeRef::new("Slough2", "Cycling {2}")
        }
        .card(),
    );
    let ab = ch
        .abilities
        .iter()
        .find_map(|a| a.as_activated().filter(|ab| ab.from_hand))
        .map(Rc::clone)
        .expect("the cycling ability");
    let card = in_hand(&mut game, &ch, P0);
    let top = card_in_library(&mut game, P0, "TopCard");
    give_mana(&mut game, P0, &[("C", 2)]);
    assert!(game.activate_ability(P0, card, &ab, 0));
    assert_eq!(game.obj(card).zone, Zone::Graveyard); // discarded as cost
    settle(&mut game);
    assert!(game.player(P0).hand.contains(&top)); // drew a card
}

#[test]
fn rule_601_2f_cost_reduction_per_creature() {
    let mut game = make_game(2);
    let ch = compile_card(
        &FakeRef {
            types: vec!["Sorcery"],
            mana_cost: "{8}{R}",
            ..FakeRef::new(
                "Blasphemy2",
                "This spell costs {1} less to cast for each creature on the battlefield.\nBlasphemy2 deals 13 damage to each creature.",
            )
        }
        .card(),
    );
    assert_eq!(ch.cost_less_per_creature, 1);
    for _ in 0..8 {
        creature(&mut game, P1, 2, 2);
    }
    let spell = in_hand(&mut game, &ch, P0);
    give_mana(&mut game, P0, &[("R", 1)]); // {8} reduced to {0}
    assert!(game.cast_spell(P0, spell, 0, false));
}

#[test]
fn rule_601_2b_additional_cost_sacrifice() {
    let mut game = make_game(2);
    let ch = compile_card(
        &FakeRef {
            types: vec!["Sorcery"],
            mana_cost: "{B}",
            ..FakeRef::new(
                "Intent2",
                "As an additional cost to cast this spell, sacrifice a creature.\nSearch your library for a card, put that card into your hand, then shuffle.",
            )
        }
        .card(),
    );
    assert_eq!(ch.additional_cost, "sacrifice_creature");
    let spell = in_hand(&mut game, &ch, P0);
    give_mana(&mut game, P0, &[("B", 1)]);
    // no creature to sacrifice -> cannot cast
    assert!(!game.cast_spell(P0, spell, 0, false));
    let fodder = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Fodder",
            ..CreatureSpec::default()
        },
    );
    assert!(game.cast_spell(P0, spell, 0, false));
    assert_eq!(game.obj(fodder).zone, Zone::Graveyard);
}

#[test]
fn swords_rider() {
    let ch = compile_card(
        &FakeRef {
            types: vec!["Instant"],
            mana_cost: "{W}",
            ..FakeRef::new(
                "StP2",
                "Exile target creature. Its controller gains life equal to its power.",
            )
        }
        .card(),
    );
    assert!(
        spell_parts(&ch)
            .iter()
            .any(|p| matches!(p, Effect::TargetControllerGainsPower { .. }))
    );
    let mut game = make_game(2);
    let bear = creature(&mut game, P1, 4, 4);
    let spell = in_hand(&mut game, &ch, P0);
    give_mana(&mut game, P0, &[("W", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    game.resolve_top();
    assert_eq!(game.obj(bear).zone, Zone::Exile);
    assert_eq!(game.player(P1).life, 44);
}

#[test]
fn trophy_and_feed_riders_compile() {
    let ch = compile_card(
        &FakeRef {
            types: vec!["Instant"],
            ..FakeRef::new(
                "Trophy2",
                "Destroy target permanent an opponent controls. Its controller may search their library for a basic land card, put it onto the battlefield, then shuffle.",
            )
        }
        .card(),
    );
    assert!(
        spell_parts(&ch)
            .iter()
            .any(|p| matches!(p, Effect::TargetControllerBasicLand { .. }))
    );
    let ch = compile_card(
        &FakeRef {
            types: vec!["Sorcery"],
            ..FakeRef::new(
                "Feed2",
                "Destroy target creature or enchantment an opponent controls. You lose life equal to that permanent's mana value.",
            )
        }
        .card(),
    );
    assert!(
        spell_parts(&ch)
            .iter()
            .any(|p| matches!(p, Effect::LoseLifeTargetMV { .. }))
    );
}

#[test]
fn exsanguinate_merges_to_drain() {
    let ch = compile_card(
        &FakeRef {
            types: vec!["Sorcery"],
            mana_cost: "{X}{B}{B}",
            ..FakeRef::new(
                "Exsang2",
                "Each opponent loses X life. You gain life equal to the life lost this way.",
            )
        }
        .card(),
    );
    assert!(
        spell_parts(&ch)
            .iter()
            .any(|p| matches!(p, Effect::Drain { .. }))
    );
}

#[test]
fn village_pillagers_style() {
    let ch = compile_card(
        &FakeRef::new(
            "Pillager2",
            "Whenever a creature an opponent controls with a counter on it dies, you create a tapped Treasure token.",
        )
        .card(),
    );
    assert!(ch.abilities.iter().any(|a| a.as_triggered().is_some()));
    let mut game = make_game(2);
    permanent(&mut game, &ch, P0);
    let victim = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            name: "Marked",
            power: 1,
            toughness: 1,
            ..CreatureSpec::default()
        },
    );
    game.put_counters(victim, "-1/-1", 1);
    settle(&mut game); // SBA kills the 1/1
    assert_eq!(game.obj(victim).zone, Zone::Graveyard);
    let treasures: Vec<ObjId> = game
        .player(P0)
        .battlefield
        .iter()
        .copied()
        .filter(|o| game.obj(*o).base.subtypes.contains("Treasure"))
        .collect();
    assert_eq!(treasures.len(), 1);
    assert!(game.obj(treasures[0]).tapped);
}

#[test]
fn once_each_turn_gate() {
    let ch = compile_card(
        &FakeRef::new(
            "Reaper2",
            "Whenever a creature an opponent controls with a -1/-1 counter on it dies, you may put that card onto the battlefield under your control. Do this only once each turn.",
        )
        .card(),
    );
    let trig = ch
        .abilities
        .iter()
        .find_map(|a| a.as_triggered())
        .expect("a trigger");
    assert!(trig.once_each_turn);
    let mut game = make_game(2);
    permanent(&mut game, &ch, P0);
    for name in ["A", "B"] {
        let v = creature_with(
            &mut game,
            P1,
            CreatureSpec {
                name,
                ..CreatureSpec::default()
            },
        );
        game.put_counters(v, "-1/-1", 2);
    }
    settle(&mut game); // both die to SBAs
    let stolen = game
        .player(P0)
        .battlefield
        .iter()
        .filter(|o| ["A", "B"].contains(&game.obj(**o).base.name.as_str()))
        .count();
    assert_eq!(stolen, 1); // only once this turn
}
