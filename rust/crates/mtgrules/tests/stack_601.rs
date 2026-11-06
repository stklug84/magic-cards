//! Casting, the stack, priority, and resolution (CR 117, 405, 601-608):
//! port of the Python `test_601_stack.py`.

mod common;

use std::cell::RefCell;
use std::rc::Rc;

use common::{
    CardSpec, CreatureSpec, card_in_hand_with, card_in_library, creature, creature_with, give_mana,
    make_game, settle, sorcery,
};
use mtgrules::abilities::{Ability, SpellAbility, TargetSpec, TriggerSpec, TriggeredAbility};
use mtgrules::effects::Effect;
use mtgrules::events::{Event, EventData, EventType};
use mtgrules::objects::{ObjId, PlayerId, Zone};

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

#[test]
fn rule_601_2_cast_and_resolve() {
    let mut game = make_game(2);
    let spell = sorcery(&mut game, P0, Effect::draw(2), vec![], "{1}");
    for i in 0..3 {
        card_in_library(&mut game, P0, &format!("c{i}"));
    }
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    assert_eq!(game.stack.len(), 1);
    assert_eq!(game.obj(spell).zone, Zone::Stack);
    game.resolve_top();
    assert_eq!(game.player(P0).hand.len(), 2);
    assert_eq!(game.obj(spell).zone, Zone::Graveyard); // rule 608.2m
    assert_eq!(game.player(P0).stats.get("spells_cast"), Some(&1));
    assert_eq!(game.player(P0).cards_cast, vec!["TestSpell".to_owned()]);
}

#[test]
fn rule_601_2h_cannot_cast_without_mana() {
    let mut game = make_game(2);
    let spell = sorcery(&mut game, P0, Effect::draw(1), vec![], "{4}");
    assert!(!game.cast_spell(P0, spell, 0, false));
    assert_eq!(game.obj(spell).zone, Zone::Hand);
    assert!(game.stack.is_empty());
}

#[test]
fn rule_608_3_permanent_resolves_to_battlefield() {
    let mut game = make_game(2);
    let c = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Bear",
            mana_cost: "{1}",
            types: vec!["Creature"],
            power: Some(2),
            toughness: Some(2),
            ..CardSpec::default()
        },
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, c, 0, false));
    game.resolve_top();
    assert_eq!(game.obj(c).zone, Zone::Battlefield);
    assert!(game.obj(c).entered_this_turn);
    assert_eq!(game.player(P0).battlefield, vec![c]);
}

#[test]
fn rule_701_5_counterspell() {
    let mut game = make_game(2);
    let victim = sorcery(&mut game, P0, Effect::draw(1), vec![], "{1}");
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, victim, 0, false));
    let mut cancel = SpellAbility::new(Effect::CounterSpell);
    cancel.targets = vec![TargetSpec::new("spell")];
    let counter = card_in_hand_with(
        &mut game,
        P1,
        CardSpec {
            name: "Cancel",
            mana_cost: "{1}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(cancel)],
            ..CardSpec::default()
        },
    );
    give_mana(&mut game, P1, &[("C", 1)]);
    assert!(game.cast_spell(P1, counter, 0, false));
    game.resolve_top(); // the counter resolves first
    assert!(game.stack.is_empty()); // victim countered
    assert_eq!(game.obj(victim).zone, Zone::Graveyard);
    assert_eq!(game.player(P0).hand, Vec::<ObjId>::new()); // never resolved
    assert_eq!(
        game.player(P0).stats.get("spells_countered_against"),
        Some(&1)
    );
    assert_eq!(game.player(P1).stats.get("counterspells_used"), Some(&1));
}

#[test]
fn rule_608_2b_fizzle_on_illegal_target() {
    let mut game = make_game(2);
    let bear = creature(&mut game, P1, 2, 2);
    let spell = sorcery(
        &mut game,
        P0,
        Effect::destroy(),
        vec![TargetSpec::new("creature")],
        "{1}",
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    // the target dies in response
    game.move_zone(bear, Zone::Graveyard);
    game.resolve_top();
    assert_eq!(game.obj(spell).zone, Zone::Graveyard);
}

#[test]
fn rule_115_4_cannot_target_hexproof() {
    let mut game = make_game(2);
    creature_with(
        &mut game,
        P1,
        CreatureSpec {
            name: "Sneaky",
            keywords: vec!["hexproof"],
            ..CreatureSpec::default()
        },
    );
    let spell = sorcery(
        &mut game,
        P0,
        Effect::destroy(),
        vec![TargetSpec::new("creature")],
        "{1}",
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    // the only potential target is hexproof -> the cast fails for want of
    // a legal target (rule 601.2c)
    assert!(!game.cast_spell(P0, spell, 0, false));
    assert_eq!(game.obj(spell).zone, Zone::Hand);
}

#[test]
fn rule_115_4_own_hexproof_targetable() {
    let mut game = make_game(2);
    let mine = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "Mine",
            keywords: vec!["hexproof"],
            ..CreatureSpec::default()
        },
    );
    let spell = sorcery(
        &mut game,
        P0,
        Effect::destroy(),
        vec![TargetSpec::new("creature")],
        "{1}",
    );
    give_mana(&mut game, P0, &[("C", 1)]);
    assert!(game.cast_spell(P0, spell, 0, false));
    game.resolve_top();
    assert_eq!(game.obj(mine).zone, Zone::Graveyard);
}

#[test]
fn rule_603_3b_apnap_trigger_order() {
    let mut game = make_game(2);
    let order: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
    for (player, tag) in [(P0, "active"), (P1, "nonactive")] {
        let probe = Rc::clone(&order);
        let effect = Effect::custom("probe", move |_game, _ctx| probe.borrow_mut().push(tag));
        let trigger = TriggerSpec::when(
            EventType::BeginStep,
            Rc::new(|_g, _s, e: &Event| e.data.step.as_deref() == Some("test")),
        );
        let c = creature_with(
            &mut game,
            player,
            CreatureSpec {
                name: "watcher",
                ..CreatureSpec::default()
            },
        );
        game.obj_mut(c)
            .base_mut()
            .abilities
            .push(Ability::from(TriggeredAbility::new(trigger, effect)));
    }
    game.bump();
    game.queue_triggers(&Event::new(
        EventType::BeginStep,
        EventData {
            step: Some("test".to_owned()),
            ..EventData::default()
        },
    ));
    settle(&mut game);
    // the active player's trigger goes on the stack first, so the
    // nonactive player's resolves first (LIFO)
    assert_eq!(*order.borrow(), ["nonactive", "active"]);
}

#[test]
fn rule_903_8_commander_tax() {
    let mut game = make_game(2);
    let cmd = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "General",
            mana_cost: "{2}",
            types: vec!["Creature"],
            power: Some(2),
            toughness: Some(2),
            ..CardSpec::default()
        },
    );
    game.obj_mut(cmd).commander = true;
    game.player_mut(P0).hand.retain(|c| *c != cmd);
    game.obj_mut(cmd).zone = Zone::Command;
    game.player_mut(P0).command.push(cmd);
    game.player_mut(P0).commander_obj = Some(cmd);
    game.player_mut(P0).commander_casts = 1; // one previous cast
    give_mana(&mut game, P0, &[("C", 2)]);
    assert!(!game.cast_spell(P0, cmd, 0, true));
    give_mana(&mut game, P0, &[("C", 2)]); // now 4 total
    assert!(game.cast_spell(P0, cmd, 0, true));
    assert_eq!(game.player(P0).commander_casts, 2);
    assert_eq!(game.player(P0).command, Vec::<ObjId>::new());
}

#[test]
fn rule_903_9_commander_to_command_zone() {
    let mut game = make_game(2);
    let cmd = creature_with(
        &mut game,
        P0,
        CreatureSpec {
            name: "General",
            ..CreatureSpec::default()
        },
    );
    game.obj_mut(cmd).commander = true;
    game.player_mut(P0).commander_obj = Some(cmd);
    game.destroy(cmd);
    assert_eq!(game.obj(cmd).zone, Zone::Command);
    assert_eq!(game.player(P0).command, vec![cmd]);
    assert_eq!(game.player(P0).graveyard, Vec::<ObjId>::new());
}
