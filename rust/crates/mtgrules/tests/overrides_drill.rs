//! Every hand-written card put through the same battery: it enters the
//! battlefield, sees counters placed and a countered enemy die, a token
//! of its controller die, the beginning of each step, its own attack,
//! combat damage it dealt, a spell cast, every activated ability it has
//! (with mana, loyalty and a graveyard stay), and - for the spells - is
//! cast with legal targets available. The assertion is robustness: no
//! panic, the game settles, the state stays consistent. The per-card
//! semantics are pinned by the per-rule tests (and a change to them is
//! reviewed with the `dump_abilities` example).

mod common;

use std::rc::Rc;

use common::{
    CardSpec, CreatureSpec, card_in_hand_with, creature, creature_with, give_mana, make_game,
    settle,
};
use mtgcards::CardData;
use mtgrules::Game;
use mtgrules::abilities::{Ability, SpellAbility, TokenSpec};
use mtgrules::compiler::compile_card;
use mtgrules::effects::Effect;
use mtgrules::events::{Event, EventData, EventType};
use mtgrules::objects::{Defender, ObjId, PlayerId, Zone};
use mtgrules::overrides;
use mtgrules::protocols::CountValue;
use mtgrules::stack::Target;

const P0: PlayerId = PlayerId(0);
const P1: PlayerId = PlayerId(1);

const SPELLS: &[&str] = &[
    "Fire Covenant",
    "Black Sun's Zenith",
    "Chaos Warp",
    "Cultivate",
    "Farewell",
    "Austere Command",
    "Akroma's Will",
    "Spell Swindle",
    "Brass's Bounty",
    "Heroic Reinforcements",
    "Swan Song",
    "Arcane Denial",
    "Persist",
    "Aberrant Return",
    "Grave Venerations",
];
const AURAS: &[&str] = &["Mechanized Production", "Imprisoned in the Moon"];
const PLANESWALKERS: &[&str] = &["Kaya, Geist Hunter", "Saheeli Rai"];
const LANDS: &[&str] = &["Treasure Vault", "Myriad Landscape"];

const INSTANTS: &[&str] = &[
    "Swan Song",
    "Arcane Denial",
    "Spell Swindle",
    "Fire Covenant",
];

fn types_of(name: &str) -> Vec<&'static str> {
    if INSTANTS.contains(&name) {
        vec!["Instant"]
    } else if SPELLS.contains(&name) {
        vec!["Sorcery"]
    } else if AURAS.contains(&name) {
        vec!["Enchantment", "Aura"]
    } else if PLANESWALKERS.contains(&name) {
        vec!["Planeswalker"]
    } else if LANDS.contains(&name) {
        vec!["Land"]
    } else {
        vec!["Artifact", "Creature"]
    }
}

fn compiled(name: &str) -> mtgrules::objects::Characteristics {
    let mana_cost = if LANDS.contains(&name) {
        String::new()
    } else {
        "{1}{B}".to_owned()
    };
    let card = CardData {
        types: types_of(name).into_iter().map(str::to_owned).collect(),
        // black, as its cost says; a land is colourless
        colors: mtgcards::mana::parse_cost(&mana_cost).colors(),
        mana_cost,
        color_identity: ["B".to_owned()].into_iter().collect(),
        power: Some(2),
        toughness: Some(3),
        loyalty: PLANESWALKERS.contains(&name).then_some(4),
        ..CardData::new(name, "test")
    };
    compile_card(&card)
}

fn step(game: &mut Game, name: &str) {
    game.queue_triggers(&Event::new(
        EventType::BeginStep,
        EventData {
            step: Some(name.to_owned()),
            player: Some(P0),
            ..EventData::default()
        },
    ));
    settle(game);
}

fn mana(game: &mut Game) {
    give_mana(
        game,
        P0,
        &[("W", 6), ("U", 6), ("B", 6), ("R", 6), ("G", 6), ("C", 6)],
    );
    give_mana(game, P1, &[("U", 3)]);
}

/// A dummy instant of the opponent on the stack (a target for the
/// counterspells).
fn opposing_spell(game: &mut Game) {
    let bolt = card_in_hand_with(
        game,
        P1,
        CardSpec {
            name: "Opposing Instant",
            mana_cost: "{U}",
            types: vec!["Instant"],
            abilities: vec![Ability::from(SpellAbility::new(Effect::GainLife {
                amount: CountValue::Fixed(1),
            }))],
            ..CardSpec::default()
        },
    );
    assert!(
        game.cast_spell(P1, bolt, 0, false),
        "the opposing instant casts"
    );
}

/// The battery for one permanent.
#[expect(
    clippy::too_many_lines,
    reason = "the battery is one scripted sequence of events"
)]
fn drill_permanent(name: &str) {
    let mut game = make_game(2);
    mana(&mut game);
    // a board to interact with: an enemy with counters, an own token, lands
    let victim = creature_with(
        &mut game,
        P1,
        CreatureSpec {
            name: "Victim",
            power: 3,
            toughness: 3,
            ..CreatureSpec::default()
        },
    );
    let own = creature(&mut game, P0, 2, 2);
    for _ in 0..3 {
        creature_with(
            &mut game,
            P0,
            CreatureSpec {
                name: "Swamp",
                types: vec!["Land"],
                subtypes: vec!["Swamp"],
                ..CreatureSpec::default()
            },
        );
    }
    // the card enters from the hand (ETB triggers, replacements)
    let ch = compiled(name);
    let obj = game.create_object(ch, P0, false, None);
    game.place(obj, P0, Zone::Hand);
    game.player_mut(P0).hand.retain(|c| *c != obj);
    game.obj_mut(obj).zone = Zone::Library;
    game.move_zone(obj, Zone::Battlefield);
    if AURAS.contains(&name) {
        game.attach(
            obj,
            if name == "Mechanized Production" {
                own
            } else {
                victim
            },
        );
    }
    if PLANESWALKERS.contains(&name) {
        game.obj_mut(obj).counters.insert("loyalty".to_owned(), 5);
    }
    if name == "Hangarback Walker" {
        game.put_counters(obj, "+1/+1", 2);
    }
    settle(&mut game);
    // tokens and counters: doublers, Esix, Manufactor, the counter triggers
    let tokens = game.create_tokens(
        P0,
        Rc::new(TokenSpec::creature("Soldier", 1, 1)),
        1,
        None,
        None,
    );
    game.create_tokens(P0, Rc::new(TokenSpec::treasure()), 1, None, None);
    game.put_counters(victim, "-1/-1", 1);
    game.put_counters(own, "+1/+1", 1);
    settle(&mut game);
    // the steps
    for s in ["upkeep", "combat_begin", "end"] {
        step(&mut game, s);
    }
    // the card attacks alone and connects
    game.obj_mut(obj).attacking = Some(Defender::Player(P1));
    game.queue_triggers(&Event::new(
        EventType::Attacks,
        EventData {
            obj: Some(obj),
            player: Some(P0),
            defender: Some(Defender::Player(P1)),
            alone: Some(true),
            ..EventData::default()
        },
    ));
    settle(&mut game);
    game.deal_damage(Some(obj), Target::Player(P1), 2, true);
    settle(&mut game);
    game.obj_mut(obj).attacking = None;
    // a cast of the controller (Sai, Saheeli)
    let rock = card_in_hand_with(
        &mut game,
        P0,
        CardSpec {
            name: "Rock",
            mana_cost: "{1}",
            types: vec!["Artifact"],
            ..CardSpec::default()
        },
    );
    if game.cast_spell(P0, rock, 0, false) {
        settle(&mut game);
    }
    // every activated ability, with the targets it may want
    let abilities: Vec<Rc<_>> = game
        .chars(obj)
        .abilities
        .iter()
        .filter_map(|a| a.as_activated().cloned())
        .collect();
    for ab in &abilities {
        mana(&mut game);
        if game.activation_legal(P0, obj, ab) {
            game.activate_ability(P0, obj, ab, 0);
            settle(&mut game);
        }
    }
    // the deaths: the countered enemy, an own token, the card itself
    game.put_counters(victim, "-1/-1", 2);
    settle(&mut game);
    if game.obj(victim).zone == Zone::Battlefield {
        game.destroy(victim);
        settle(&mut game);
    }
    if let Some(tok) = tokens.first()
        && game.obj(*tok).zone == Zone::Battlefield
    {
        game.destroy(*tok);
        settle(&mut game);
    }
    if game.obj(obj).zone == Zone::Battlefield {
        game.destroy(obj);
        settle(&mut game);
    }
    // from the graveyard (Reassembling Skeleton) and back on the field
    for ab in &abilities {
        if ab.from_graveyard && game.obj(obj).zone == Zone::Graveyard {
            mana(&mut game);
            if game.activation_legal(P0, obj, ab) {
                game.activate_ability(P0, obj, ab, 0);
                settle(&mut game);
            }
        }
    }
    game.end_of_turn_cleanup();
    assert!(game.players.iter().all(|p| p.life <= 40 + 50));
}

/// The battery for one spell: cast with every kind of target around.
fn drill_spell(name: &str) {
    let mut game = make_game(2);
    mana(&mut game);
    creature_with(
        &mut game,
        P1,
        CreatureSpec {
            name: "Big",
            power: 5,
            toughness: 5,
            ..CreatureSpec::default()
        },
    );
    let dead = creature(&mut game, P0, 3, 3);
    game.move_zone(dead, Zone::Graveyard);
    for _ in 0..4 {
        creature_with(
            &mut game,
            P0,
            CreatureSpec {
                name: "Forest",
                types: vec!["Land"],
                subtypes: vec!["Forest"],
                ..CreatureSpec::default()
            },
        );
    }
    let ch = compiled(name);
    let spell = game.create_object(ch, P0, false, None);
    game.place(spell, P0, Zone::Hand);
    if INSTANTS.contains(&name) && name != "Fire Covenant" {
        opposing_spell(&mut game);
    }
    let cast = game.cast_spell(P0, spell, 2, false);
    assert!(cast, "{name} casts with its targets available");
    settle(&mut game);
    assert_ne!(game.obj(spell).zone, Zone::Hand);
    game.end_of_turn_cleanup();
}

#[test]
fn every_hand_written_permanent_survives_the_battery() {
    for name in overrides::names() {
        if SPELLS.contains(&name) {
            continue;
        }
        drill_permanent(name);
    }
}

#[test]
fn every_hand_written_spell_casts_and_resolves() {
    for name in SPELLS {
        drill_spell(name);
    }
}

#[test]
fn the_drill_reaches_the_card_effects() {
    // a few spot checks that the battery is not inert
    let mut game = make_game(2);
    mana(&mut game);
    let ch = compiled("Grave Titan");
    let titan = game.create_object(ch, P0, false, None);
    game.place(titan, P0, Zone::Library);
    game.move_zone(titan, Zone::Battlefield);
    settle(&mut game);
    let zombies = game
        .player(P0)
        .battlefield
        .iter()
        .filter(|o| game.obj(**o).base.name == "Zombie")
        .count();
    assert_eq!(zombies, 2);
    let ch = compiled("Reef Worm");
    let worm = game.create_object(ch, P0, false, None);
    game.place(worm, P0, Zone::Battlefield);
    game.destroy(worm);
    settle(&mut game);
    let fish: Vec<ObjId> = game
        .player(P0)
        .battlefield
        .iter()
        .copied()
        .filter(|o| game.obj(*o).base.name == "Fish")
        .collect();
    assert_eq!(fish.len(), 1);
    game.destroy(fish[0]);
    settle(&mut game);
    assert!(
        game.player(P0)
            .battlefield
            .iter()
            .any(|o| game.obj(*o).base.name == "Whale")
    );
}
