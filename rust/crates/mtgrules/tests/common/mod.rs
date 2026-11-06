//! Test fixtures: minimal games with synthetic cards (the Python
//! `mtgrules.tests.helpers`).

#![allow(dead_code, reason = "each test binary uses a subset of the helpers")]

use std::collections::BTreeSet;
use std::rc::Rc;

use mtgrules::Game;
use mtgrules::abilities::Ability;
use mtgrules::objects::{Characteristics, ObjId, PlayerId, Zone};
use mtgrules::policy::TrivialPolicy;
use pycompat::random::PyRandom;

/// A bare game of `n_players` seats with trivial policies and empty zones.
pub(crate) fn make_game(n_players: usize) -> Game {
    let names: Vec<String> = (0..n_players).map(|i| format!("p{i}")).collect();
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    Game::new(&names, PyRandom::new(7), &[Rc::new(TrivialPolicy)], 40)
}

pub(crate) fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

/// The knobs of a synthetic creature.
pub(crate) struct CreatureSpec {
    pub name: &'static str,
    pub power: i64,
    pub toughness: i64,
    pub keywords: Vec<&'static str>,
    pub abilities: Vec<Ability>,
    pub supertypes: Vec<&'static str>,
    pub subtypes: Vec<&'static str>,
    pub colors: Vec<&'static str>,
    pub types: Vec<&'static str>,
    pub tapped: bool,
    pub entered_this_turn: bool,
}

impl Default for CreatureSpec {
    fn default() -> Self {
        CreatureSpec {
            name: "Bear",
            power: 2,
            toughness: 2,
            keywords: Vec::new(),
            abilities: Vec::new(),
            supertypes: Vec::new(),
            subtypes: Vec::new(),
            colors: Vec::new(),
            types: vec!["Creature"],
            tapped: false,
            entered_this_turn: false,
        }
    }
}

/// Put a synthetic creature onto the player's battlefield.
pub(crate) fn creature_with(game: &mut Game, player: PlayerId, spec: CreatureSpec) -> ObjId {
    let base = Characteristics {
        name: spec.name.to_owned(),
        types: set(&spec.types),
        supertypes: set(&spec.supertypes),
        subtypes: set(&spec.subtypes),
        colors: set(&spec.colors),
        power: Some(spec.power),
        toughness: Some(spec.toughness),
        keywords: set(&spec.keywords),
        abilities: spec.abilities.into_iter().map(Rc::new).collect(),
        ..Characteristics::default()
    };
    let id = game.create_object(base, player, false, None);
    {
        let obj = game.obj_mut(id);
        obj.controller = player;
        obj.tapped = spec.tapped;
        obj.entered_this_turn = spec.entered_this_turn;
    }
    game.place(id, player, Zone::Battlefield);
    id
}

/// A `power`/`toughness` creature with the default name.
pub(crate) fn creature(game: &mut Game, player: PlayerId, power: i64, toughness: i64) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            power,
            toughness,
            ..CreatureSpec::default()
        },
    )
}

/// A named creature with abilities.
pub(crate) fn creature_named(
    game: &mut Game,
    player: PlayerId,
    name: &'static str,
    power: i64,
    toughness: i64,
    abilities: Vec<Ability>,
) -> ObjId {
    creature_with(
        game,
        player,
        CreatureSpec {
            name,
            power,
            toughness,
            abilities,
            ..CreatureSpec::default()
        },
    )
}

/// Put a synthetic card into the player's hand.
pub(crate) fn card_in_hand(
    game: &mut Game,
    player: PlayerId,
    name: &str,
    mana_cost: &str,
    types: &[&str],
) -> ObjId {
    let base = Characteristics {
        name: name.to_owned(),
        mana_cost: mana_cost.to_owned(),
        types: set(types),
        ..Characteristics::default()
    };
    let id = game.create_object(base, player, false, None);
    game.place(id, player, Zone::Hand);
    id
}

/// Add floating mana to the player's pool.
pub(crate) fn give_mana(game: &mut Game, player: PlayerId, mana: &[(&str, i64)]) {
    for (t, n) in mana {
        game.player_mut(player).mana_pool.add(t, *n);
    }
}

/// `(power, toughness)` of an object's current characteristics.
pub(crate) fn pt(game: &Game, obj: ObjId) -> (Option<i64>, Option<i64>) {
    let ch = game.chars(obj);
    (ch.power, ch.toughness)
}
