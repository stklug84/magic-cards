//! Test fixtures: minimal games with synthetic cards (the Python
//! `mtgrules.tests.helpers`).

#![allow(dead_code, reason = "each test binary uses a subset of the helpers")]

use std::collections::BTreeSet;
use std::rc::Rc;

use mtgrules::Game;
use mtgrules::abilities::{Ability, SpellAbility, TargetSpec};
use mtgrules::effects::Effect;
use mtgrules::objects::{Characteristics, ObjId, PlayerId, Zone};
use mtgrules::policy::DefaultPolicy;
use pycompat::random::PyRandom;

/// A bare game of `n_players` seats with trivial policies and empty zones.
pub(crate) fn make_game(n_players: usize) -> Game {
    let names: Vec<String> = (0..n_players).map(|i| format!("p{i}")).collect();
    let names: Vec<&str> = names.iter().map(String::as_str).collect();
    Game::new(
        &names,
        PyRandom::new(7),
        &[Rc::new(DefaultPolicy::new())],
        40,
    )
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
        abilities: spec.abilities,
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

/// The knobs of a synthetic card in hand (the Python `card_in_hand`
/// keywords).
pub(crate) struct CardSpec {
    pub name: &'static str,
    pub mana_cost: &'static str,
    pub types: Vec<&'static str>,
    pub abilities: Vec<Ability>,
    pub power: Option<i64>,
    pub toughness: Option<i64>,
    pub subtypes: Vec<&'static str>,
}

impl Default for CardSpec {
    fn default() -> Self {
        CardSpec {
            name: "Spell",
            mana_cost: "{1}",
            types: vec!["Sorcery"],
            abilities: Vec::new(),
            power: None,
            toughness: None,
            subtypes: Vec::new(),
        }
    }
}

/// Put a synthetic card with every knob into the player's hand.
pub(crate) fn card_in_hand_with(game: &mut Game, player: PlayerId, spec: CardSpec) -> ObjId {
    let base = Characteristics {
        name: spec.name.to_owned(),
        mana_cost: spec.mana_cost.to_owned(),
        types: set(&spec.types),
        subtypes: set(&spec.subtypes),
        power: spec.power,
        toughness: spec.toughness,
        abilities: spec.abilities,
        ..Characteristics::default()
    };
    let id = game.create_object(base, player, false, None);
    game.place(id, player, Zone::Hand);
    id
}

/// Put a synthetic card on top of the player's library.
pub(crate) fn card_in_library(game: &mut Game, player: PlayerId, name: &str) -> ObjId {
    let base = Characteristics {
        name: name.to_owned(),
        types: set(&["Sorcery"]),
        ..Characteristics::default()
    };
    let id = game.create_object(base, player, false, None);
    game.place(id, player, Zone::Library);
    id
}

/// A synthetic sorcery with `effect` and `targets` in the player's hand.
pub(crate) fn sorcery(
    game: &mut Game,
    player: PlayerId,
    effect: Effect,
    targets: Vec<TargetSpec>,
    cost: &'static str,
) -> ObjId {
    let mut ability = SpellAbility::new(effect);
    ability.targets = targets;
    card_in_hand_with(
        game,
        player,
        CardSpec {
            name: "TestSpell",
            mana_cost: cost,
            abilities: vec![Ability::from(ability)],
            ..CardSpec::default()
        },
    )
}

/// `settle()` gives up after this many SBA/trigger/resolve iterations.
const SETTLE_GUARD: u32 = 200;

/// Run SBAs + put triggers on the stack + resolve everything.
pub(crate) fn settle(game: &mut Game) {
    let mut guard = 0;
    loop {
        guard += 1;
        assert!(guard < SETTLE_GUARD, "settle() did not converge");
        let acted = game.check_state_based_actions();
        let placed = game.put_triggers_on_stack();
        if !game.stack.is_empty() {
            game.resolve_top();
            continue;
        }
        if !acted && !placed {
            return;
        }
    }
}

/// Add floating mana to the player's pool.
pub(crate) fn give_mana(game: &mut Game, player: PlayerId, mana: &[(&str, i64)]) {
    for (t, n) in mana {
        game.player_mut(player).mana_pool.add(t, *n);
    }
}

/// A minimal card for the compiler: the Python `FakeRef` (a 1/1
/// `{1}` Creature unless the knobs say otherwise).
pub(crate) struct FakeRef {
    pub name: &'static str,
    pub oracle: &'static str,
    pub types: Vec<&'static str>,
    pub subtypes: Vec<&'static str>,
    pub mana_cost: &'static str,
    pub power: Option<i64>,
    pub toughness: Option<i64>,
    pub loyalty: Option<i64>,
    pub color_identity: Vec<&'static str>,
    pub land_colors: Vec<&'static str>,
}

impl Default for FakeRef {
    fn default() -> Self {
        FakeRef {
            name: "Card",
            oracle: "",
            types: vec!["Creature"],
            subtypes: Vec::new(),
            mana_cost: "{1}",
            power: Some(1),
            toughness: Some(1),
            loyalty: None,
            color_identity: Vec::new(),
            land_colors: Vec::new(),
        }
    }
}

impl FakeRef {
    /// A creature with `oracle` text.
    pub(crate) fn new(name: &'static str, oracle: &'static str) -> Self {
        FakeRef {
            name,
            oracle,
            ..FakeRef::default()
        }
    }

    /// The `CardData` the compiler reads.
    pub(crate) fn card(&self) -> mtgcards::CardData {
        let mut card = mtgcards::CardData::new(self.name, "test");
        self.oracle.clone_into(&mut card.oracle);
        card.types = set(&self.types);
        card.subtypes = set(&self.subtypes);
        self.mana_cost.clone_into(&mut card.mana_cost);
        card.power = self.power;
        card.toughness = self.toughness;
        card.loyalty = self.loyalty;
        card.color_identity = set(&self.color_identity);
        if !self.land_colors.is_empty() {
            card.behavior.insert(
                "land_colors",
                mtgcards::behavior::set_value(self.land_colors.iter().copied()),
            );
        }
        card
    }
}

/// Put the game into `phase` without the phase log line (the Python
/// tests assign `game.phase` directly).
pub(crate) fn set_phase(game: &mut Game, phase: &str) {
    phase.clone_into(&mut game.phase);
}

/// `(power, toughness)` of an object's current characteristics.
pub(crate) fn pt(game: &Game, obj: ObjId) -> (Option<i64>, Option<i64>) {
    let ch = game.chars(obj);
    (ch.power, ch.toughness)
}
