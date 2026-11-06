//! Mana production (CR 106, 605), fixes F1 and F6: what the compiler
//! makes of the oracle "Add ..." templates, and the engine's mana
//! accounting for each source shape. Port of the Python
//! `test_106_mana_production.py`.

mod common;

use std::collections::BTreeSet;
use std::rc::Rc;

use common::{CreatureSpec, FakeRef, creature_with, make_game, set};
use mtgcards::CardData;
use mtgrules::Game;
use mtgrules::abilities::{Ability, ActivatedAbility};
use mtgrules::compiler::compile_card;
use mtgrules::effects::{AddMana, Ctx, Effect};
use mtgrules::manasys::parse_cost;
use mtgrules::objects::{Characteristics, ObjId, PlayerId, Zone};

const P0: PlayerId = PlayerId(0);

/// A tapped-for-mana land whose only ability is `effect`.
fn source(game: &mut Game, effect: AddMana, name: &'static str) -> ObjId {
    let ability = ActivatedAbility {
        tap_cost: true,
        is_mana_ability: true,
        effect: Some(Effect::AddMana(effect)),
        ..ActivatedAbility::default()
    };
    creature_with(
        game,
        P0,
        CreatureSpec {
            name,
            types: vec!["Land"],
            abilities: vec![Ability::from(ability)],
            ..CreatureSpec::default()
        },
    )
}

fn ability(game: &Game, obj: ObjId) -> Rc<ActivatedAbility> {
    Rc::clone(
        game.obj(obj).base.abilities[0]
            .as_activated()
            .expect("the source's first ability is activated"),
    )
}

/// A commander in the command zone whose card has `identity`.
fn commander(game: &mut Game, name: &str, identity: &[&str]) -> ObjId {
    let card = CardData {
        color_identity: set(identity),
        ..CardData::new(name, "test")
    };
    let base = Characteristics {
        name: name.to_owned(),
        ..Characteristics::default()
    };
    let id = game.create_object(base, P0, false, Some(Rc::new(card)));
    game.obj_mut(id).commander = true;
    game.place(id, P0, Zone::Command);
    game.player_mut(P0).commander_obj = Some(id);
    id
}

fn choice_of(types: &[&str]) -> AddMana {
    AddMana {
        choice: true,
        ..AddMana::fixed(types)
    }
}

#[test]
fn dual_land_pays_one_mana() {
    let mut game = make_game(2);
    let land = source(&mut game, choice_of(&["B", "R"]), "Source");
    assert_eq!(Game::mana_amount(&ability(&game, land)), 1);
    assert!(!game.can_pay_mana(P0, &parse_cost("{2}")));
    assert!(game.pay_mana(P0, &parse_cost("{R}")));
    assert!(game.obj(land).tapped);
    assert_eq!(game.player(P0).mana_pool.total(), 0);
}

#[test]
fn dual_land_chooses_the_needed_colour() {
    let mut game = make_game(2);
    source(&mut game, choice_of(&["B", "R"]), "Source");
    assert!(game.pay_mana(P0, &parse_cost("{B}")));
    assert_eq!(game.player(P0).mana_pool.total(), 0);
}

#[test]
fn bounce_land_pays_two() {
    let mut game = make_game(2);
    source(&mut game, AddMana::fixed(&["U", "B"]), "Source");
    assert!(game.pay_mana(P0, &parse_cost("{U}{B}")));
    assert_eq!(game.player(P0).mana_pool.total(), 0);
}

#[test]
fn any_one_color_amount() {
    let mut game = make_game(2);
    source(
        &mut game,
        AddMana {
            amount: 3,
            ..AddMana::any()
        },
        "Source",
    );
    assert!(game.pay_mana(P0, &parse_cost("{G}{G}{G}")));
    assert_eq!(game.player(P0).mana_pool.total(), 0);
}

#[test]
fn commander_identity_restricts_colours() {
    let mut game = make_game(2);
    commander(&mut game, "Green Legend", &["G"]);
    let identity = AddMana {
        commander_identity: true,
        ..AddMana::default()
    };
    let src = source(&mut game, identity, "Signet");
    let colors: BTreeSet<String> = game.mana_colors_of(src, &ability(&game, src));
    assert_eq!(colors, set(&["G"]));
    assert!(!game.can_pay_mana(P0, &parse_cost("{U}")));
    assert!(game.can_pay_mana(P0, &parse_cost("{G}")));
}

#[test]
fn resolving_a_choice_adds_one_mana() {
    let mut game = make_game(2);
    game.resolve_effect(
        &Effect::AddMana(choice_of(&["U", "B"])),
        &Ctx::new(P0, None),
    );
    assert_eq!(game.player(P0).mana_pool.total(), 1);
    assert_eq!(game.player(P0).mana_pool.get("U"), 1); // canonical first
}

#[test]
fn resolving_identity_mana_uses_canonical_order() {
    let mut game = make_game(2);
    commander(&mut game, "Gruul Legend", &["R", "G"]);
    let identity = AddMana {
        commander_identity: true,
        ..AddMana::default()
    };
    game.resolve_effect(&Effect::AddMana(identity), &Ctx::new(P0, None));
    assert_eq!(game.player(P0).mana_pool.get("R"), 1);
}

#[test]
fn no_commander_means_every_colour() {
    let mut game = make_game(2);
    let identity = AddMana {
        commander_identity: true,
        ..AddMana::default()
    };
    let src = source(&mut game, identity, "Signet");
    assert_eq!(
        game.mana_colors_of(src, &ability(&game, src)),
        set(&["W", "U", "B", "R", "G"])
    );
    assert!(game.can_pay_mana(P0, &parse_cost("{U}")));
}

// ---- what the compiler makes of the oracle "Add ..." templates

fn mana_abilities(card: &FakeRef) -> Vec<Rc<ActivatedAbility>> {
    compile_card(&card.card())
        .abilities
        .iter()
        .filter_map(|a| a.as_activated().filter(|ab| ab.is_mana_ability).cloned())
        .collect()
}

fn add_mana(card: &FakeRef) -> AddMana {
    let [ab] = &mana_abilities(card)[..] else {
        panic!("one mana ability");
    };
    match &ab.effect {
        Some(Effect::AddMana(add)) => add.clone(),
        other => panic!("not a plain mana ability: {other:?}"),
    }
}

fn artifact(name: &'static str, oracle: &'static str, cost: &'static str) -> FakeRef {
    FakeRef {
        types: vec!["Artifact"],
        mana_cost: cost,
        ..FakeRef::new(name, oracle)
    }
}

fn land(name: &'static str, oracle: &'static str) -> FakeRef {
    FakeRef {
        types: vec!["Land"],
        mana_cost: "",
        ..FakeRef::new(name, oracle)
    }
}

#[test]
fn or_is_a_choice_of_one() {
    let eff = add_mana(&artifact("Talisman", "{T}: Add {U} or {B}.", "{2}"));
    assert_eq!(eff.types, ["U", "B"]);
    assert!(eff.choice);
    assert_eq!(eff.amount, 1);
}

#[test]
fn three_way_choice() {
    let eff = add_mana(&land("Tri", "{T}: Add {W}, {U}, or {B}."));
    assert_eq!(eff.types, ["W", "U", "B"]);
    assert!(eff.choice);
}

#[test]
fn sequence_adds_all() {
    let eff = add_mana(&land("Aqueduct", "{T}: Add {U}{B}."));
    assert_eq!(eff.types, ["U", "B"]);
    assert!(!eff.choice);
}

#[test]
fn sol_ring_stays_two_colorless() {
    let eff = add_mana(&artifact("Sol Ring", "{T}: Add {C}{C}.", "{1}"));
    assert_eq!(eff.types, ["C", "C"]);
    assert!(!eff.choice);
}

#[test]
fn n_mana_of_any_one_color() {
    let eff = add_mana(&artifact(
        "Gilded Lotus",
        "{T}: Add three mana of any one color.",
        "{5}",
    ));
    assert!(eff.any_color);
    assert_eq!(eff.amount, 3);
}

#[test]
fn commander_identity_is_not_any_color() {
    let eff = add_mana(&artifact(
        "Arcane Signet",
        "{T}: Add one mana of any color in your commander's color identity.",
        "{2}",
    ));
    assert!(eff.commander_identity);
    assert!(!eff.any_color);
}

#[test]
fn graph_dual_land_is_a_choice() {
    let eff = add_mana(&FakeRef {
        land_colors: vec!["B", "R"],
        ..land("Canyon Slough", "")
    });
    assert_eq!(eff.types, ["B", "R"]);
    assert!(eff.choice);
}

#[test]
fn graph_basic_land_is_one_colour() {
    let eff = add_mana(&FakeRef {
        land_colors: vec!["G"],
        ..land("Forest", "")
    });
    assert_eq!(eff.types, ["G"]);
    assert!(!eff.choice);
}
