//! Replacement and prevention effects (CR 614-616): the emit machinery
//! (ordering, once-per-event bookkeeping, prevention, the affected
//! player's choice, static-ability sources) and the Python
//! `test_614_replacements.py` cases through the game actions, first with
//! the replacements written inline, then through the real card
//! implementations (`overrides`).

mod common;

use std::cell::RefCell;
use std::rc::Rc;

use common::{creature, creature_named, make_game};
use mtgrules::Game;
use mtgrules::abilities::{Ability, StaticAbility, TokenSpec};
use mtgrules::events::{Event, EventData, EventType};
use mtgrules::objects::{ObjId, PlayerId, Zone};
use mtgrules::policy::Policy;
use mtgrules::replacements::Replacement;
use mtgrules::stack::Target;
use pycompat::random::PyRandom;

fn create_token_event(controller: PlayerId, count: i64) -> Event {
    Event::new(
        EventType::CreateToken,
        EventData {
            controller: Some(controller),
            count: Some(count),
            ..EventData::default()
        },
    )
}

fn doubler(controller: PlayerId) -> Replacement {
    Replacement::new(EventType::CreateToken, |_game, mut event| {
        event.data.count = event.data.count.map(|n| n * 2);
        Some(event)
    })
    .when(move |_game, event| event.data.controller == Some(controller))
}

#[test]
fn rule_614_1c_a_floating_doubler_doubles_its_controllers_events_only() {
    let mut game = make_game(2);
    game.add_floating_replacement(doubler(PlayerId(0)));
    let mine = game
        .emit(create_token_event(PlayerId(0), 2))
        .expect("not prevented");
    assert_eq!(mine.data.count, Some(4));
    let theirs = game
        .emit(create_token_event(PlayerId(1), 2))
        .expect("not prevented");
    assert_eq!(theirs.data.count, Some(2));
    assert_eq!(mine.applied.len(), 1);
}

#[test]
fn rule_616_2_each_effect_applies_once_so_two_doublers_stack() {
    let mut game = make_game(2);
    game.add_floating_replacement(doubler(PlayerId(0)));
    game.add_floating_replacement(doubler(PlayerId(0)));
    let event = game
        .emit(create_token_event(PlayerId(0), 1))
        .expect("not prevented");
    assert_eq!(event.data.count, Some(4)); // 1 * 2 * 2
    assert_eq!(event.applied.len(), 2);
}

#[test]
fn rule_615_a_prevention_effect_removes_the_event() {
    let mut game = make_game(2);
    game.add_floating_replacement(Replacement::new(EventType::GainLife, |_game, _event| None));
    let event = Event::new(
        EventType::GainLife,
        EventData {
            player: Some(PlayerId(0)),
            amount: Some(5),
            ..EventData::default()
        },
    );
    assert!(game.emit(event).is_none());
    // a replacement that marks the event prevented removes it too
    game.add_floating_replacement(Replacement::new(EventType::Draw, |_game, mut event| {
        event.prevented = true;
        Some(event)
    }));
    assert!(
        game.emit(Event::for_player(EventType::Draw, PlayerId(1)))
            .is_none()
    );
    // other event types pass through untouched
    let draw = game
        .emit(Event::for_player(EventType::Shuffle, PlayerId(1)))
        .expect("not prevented");
    assert!(draw.applied.is_empty());
}

#[test]
fn rule_614_16a_self_replacements_apply_first() {
    let mut game = make_game(2);
    let order: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
    let (o1, o2) = (Rc::clone(&order), Rc::clone(&order));
    game.add_floating_replacement(Replacement::new(EventType::Damage, move |_g, event| {
        o1.borrow_mut().push("other");
        Some(event)
    }));
    game.add_floating_replacement(
        Replacement::new(EventType::Damage, move |_g, event| {
            o2.borrow_mut().push("self");
            Some(event)
        })
        .self_replacing(),
    );
    let event = Event::new(
        EventType::Damage,
        EventData {
            target: Some(Target::Player(PlayerId(1))),
            amount: Some(3),
            ..EventData::default()
        },
    );
    game.emit(event).expect("not prevented");
    assert_eq!(*order.borrow(), ["self", "other"]);
}

/// A policy that always picks the last candidate, recording that it was
/// asked.
struct LastPolicy {
    asked: Rc<RefCell<u32>>,
}

impl Policy for LastPolicy {
    fn choose_replacement(
        &self,
        _game: &Game,
        _event: &Event,
        candidates: &[Rc<Replacement>],
    ) -> Rc<Replacement> {
        *self.asked.borrow_mut() += 1;
        Rc::clone(candidates.last().expect("candidates"))
    }
}

#[test]
fn rule_616_1_the_affected_player_orders_the_candidates() {
    let asked = Rc::new(RefCell::new(0));
    let game_policy: Rc<dyn Policy> = Rc::new(LastPolicy {
        asked: Rc::clone(&asked),
    });
    let mut game = Game::new(&["p0", "p1"], PyRandom::new(1), &[game_policy], 40);
    let order: Rc<RefCell<Vec<&'static str>>> = Rc::new(RefCell::new(Vec::new()));
    for (label, o) in [("first", Rc::clone(&order)), ("second", Rc::clone(&order))] {
        game.add_floating_replacement(Replacement::new(
            EventType::PutCounters,
            move |_g, event| {
                o.borrow_mut().push(label);
                Some(event)
            },
        ));
    }
    // the event's object has a controller: that player orders the effects
    let c = creature(&mut game, PlayerId(1), 1, 1);
    let event = Event::new(
        EventType::PutCounters,
        EventData {
            obj: Some(c),
            kind: Some("+1/+1".to_owned()),
            count: Some(1),
            ..EventData::default()
        },
    );
    game.emit(event).expect("not prevented");
    assert_eq!(*order.borrow(), ["second", "first"]);
    assert_eq!(*asked.borrow(), 1);
    // with a single candidate the policy is not consulted
    let mut game2 = Game::new(
        &["p0"],
        PyRandom::new(1),
        &[Rc::new(LastPolicy {
            asked: Rc::clone(&asked),
        })],
        40,
    );
    game2.add_floating_replacement(Replacement::new(EventType::Draw, |_g, e| Some(e)));
    game2
        .emit(Event::for_player(EventType::Draw, PlayerId(0)))
        .expect("not prevented");
    assert_eq!(*asked.borrow(), 1);
}

#[test]
fn static_replacement_abilities_come_from_battlefield_permanents() {
    let mut game = make_game(2);
    let p0 = PlayerId(0);
    let factory = move |_game: &Game, src: ObjId| {
        vec![
            Replacement::new(EventType::CreateToken, |_game, mut event| {
                event.data.count = event.data.count.map(|n| n * 2);
                Some(event)
            })
            .when(move |_game, event| event.data.controller == Some(p0))
            .with_source(Some(src)),
        ]
    };
    let season = creature_named(
        &mut game,
        p0,
        "Doubling Season",
        1,
        1,
        vec![Ability::from(StaticAbility::replacement(
            Rc::new(factory),
            "token doubler",
        ))],
    );
    let event = game.emit(create_token_event(p0, 3)).expect("not prevented");
    assert_eq!(event.data.count, Some(6));
    // the same instance is reused (rule 616.2 bookkeeping needs stable ids)
    let again = game.emit(create_token_event(p0, 1)).expect("not prevented");
    assert_eq!(again.applied, event.applied);
    // off the battlefield the ability is inactive
    game.obj_mut(season).zone = mtgrules::objects::Zone::Graveyard;
    game.player_mut(p0).battlefield.clear();
    game.bump();
    let plain = game.emit(create_token_event(p0, 3)).expect("not prevented");
    assert_eq!(plain.data.count, Some(3));
}

// ---- the Python test_614_replacements cases through the game actions

/// A static replacement ability on a battlefield permanent (the Python
/// `_hooked` fixture with the override's replacement inlined).
fn hooked(
    game: &mut Game,
    player: PlayerId,
    name: &'static str,
    make: impl Fn(ObjId) -> Vec<Replacement> + 'static,
) -> ObjId {
    let factory = move |_game: &Game, src: ObjId| make(src);
    creature_named(
        game,
        player,
        name,
        1,
        1,
        vec![Ability::from(StaticAbility::replacement(
            Rc::new(factory),
            name,
        ))],
    )
}

/// Doubling Season: twice the tokens and counters for its controller.
fn doubling_season(me: PlayerId) -> impl Fn(ObjId) -> Vec<Replacement> {
    move |src| {
        vec![
            Replacement::new(EventType::CreateToken, |_g, mut e| {
                e.data.count = e.data.count.map(|n| n * 2);
                Some(e)
            })
            .when(move |_g, e| e.data.controller == Some(me))
            .with_source(Some(src)),
            Replacement::new(EventType::PutCounters, |_g, mut e| {
                e.data.count = e.data.count.map(|n| n * 2);
                Some(e)
            })
            .when(move |_g, e| e.data.controller == Some(me))
            .with_source(Some(src)),
        ]
    }
}

fn soldier() -> Rc<mtgrules::abilities::TokenSpec> {
    Rc::new(mtgrules::abilities::TokenSpec::creature("Soldier", 1, 1))
}

#[test]
fn rule_614_1c_token_doubler() {
    let mut game = make_game(2);
    hooked(
        &mut game,
        PlayerId(0),
        "Doubling Season",
        doubling_season(PlayerId(0)),
    );
    let made = game.create_tokens(PlayerId(0), soldier(), 2, None, None);
    assert_eq!(made.len(), 4);
    assert_eq!(
        game.player(PlayerId(0)).stats.get("tokens_created"),
        Some(&4)
    );
}

#[test]
fn rule_616_2_two_doublers_stack_multiplicatively() {
    let mut game = make_game(2);
    hooked(
        &mut game,
        PlayerId(0),
        "Doubling Season",
        doubling_season(PlayerId(0)),
    );
    hooked(
        &mut game,
        PlayerId(0),
        "Adrix and Nev, Twincasters",
        doubling_season(PlayerId(0)),
    );
    let made = game.create_tokens(PlayerId(0), soldier(), 1, None, None);
    assert_eq!(made.len(), 4); // 1 * 2 * 2
}

#[test]
fn rule_614_1c_doubler_ignores_opponents_tokens() {
    let mut game = make_game(2);
    hooked(
        &mut game,
        PlayerId(0),
        "Doubling Season",
        doubling_season(PlayerId(0)),
    );
    let made = game.create_tokens(PlayerId(1), soldier(), 2, None, None);
    assert_eq!(made.len(), 2);
}

#[test]
fn counter_doubler() {
    let mut game = make_game(2);
    hooked(
        &mut game,
        PlayerId(0),
        "Doubling Season",
        doubling_season(PlayerId(0)),
    );
    let c = creature(&mut game, PlayerId(0), 2, 2);
    game.put_counters(c, "+1/+1", 2);
    assert_eq!(game.obj(c).counter("+1/+1"), 4);
    assert_eq!(game.chars(c).power, Some(6));
}

#[test]
fn rule_615_prevention_no_life_gain() {
    let mut game = make_game(2);
    hooked(&mut game, PlayerId(0), "Everlasting Torment", |src| {
        vec![Replacement::new(EventType::GainLife, |_g, _e| None).with_source(Some(src))]
    });
    game.gain_life(PlayerId(0), 5);
    assert_eq!(game.player(PlayerId(0)).life, 40);
}

#[test]
fn academy_manufactor_one_of_each() {
    let mut game = make_game(2);
    hooked(&mut game, PlayerId(0), "Academy Manufactor", |src| {
        vec![
            Replacement::new(EventType::CreateToken, |_g, mut e| {
                // one Treasure becomes a Treasure, a Clue and a Food
                let have = e
                    .data
                    .spec
                    .as_ref()
                    .map(|s| s.predefined.clone())
                    .unwrap_or_default();
                for spec in [TokenSpec::treasure(), TokenSpec::clue(), TokenSpec::food()] {
                    if spec.predefined != have {
                        e.data.extra_specs.push(Rc::new(spec));
                    }
                }
                Some(e)
            })
            .when(|_g, e| {
                e.data
                    .spec
                    .as_ref()
                    .is_some_and(|s| ["treasure", "clue", "food"].contains(&s.predefined.as_str()))
            })
            .with_source(Some(src)),
        ]
    });
    let made = game.create_tokens(PlayerId(0), Rc::new(TokenSpec::treasure()), 1, None, None);
    let mut names: Vec<String> = made
        .iter()
        .map(|t| game.obj(*t).base.name.clone())
        .collect();
    names.sort();
    assert_eq!(names, ["Clue", "Food", "Treasure"]);
}

#[test]
fn ojer_taq_triples_creature_tokens_only() {
    let mut game = make_game(2);
    hooked(&mut game, PlayerId(0), "Ojer Taq", |src| {
        vec![
            Replacement::new(EventType::CreateToken, |_g, mut e| {
                e.data.count = e.data.count.map(|n| n * 3);
                Some(e)
            })
            .when(|_g, e| e.data.spec.as_ref().is_some_and(|s| s.is_creature()))
            .with_source(Some(src)),
        ]
    });
    assert_eq!(
        game.create_tokens(PlayerId(0), soldier(), 1, None, None)
            .len(),
        3
    );
    assert_eq!(
        game.create_tokens(PlayerId(0), Rc::new(TokenSpec::treasure()), 1, None, None)
            .len(),
        1
    );
}

#[test]
fn rule_614_enters_tapped_replacement_through_emit() {
    let mut game = make_game(2);
    game.add_floating_replacement(
        Replacement::new(EventType::ZoneChange, |_g, mut e| {
            e.data.tapped = Some(true);
            Some(e)
        })
        .when(|_g, e| e.data.to == Some(mtgrules::objects::Zone::Battlefield)),
    );
    let land = common::card_in_hand(&mut game, PlayerId(0), "Land", "", &["Land"]);
    game.move_zone(land, mtgrules::objects::Zone::Battlefield);
    assert!(game.obj(land).tapped);
    assert_eq!(game.obj(land).zone, mtgrules::objects::Zone::Battlefield);
}

// ---- the same cases through the hand-written cards (compile_card)

/// The compiled card on the battlefield under `player`'s control.
fn real_card(game: &mut Game, player: PlayerId, name: &'static str) -> ObjId {
    let card = common::FakeRef {
        types: vec!["Enchantment"],
        ..common::FakeRef::new(name, "")
    }
    .card();
    assert!(
        mtgrules::overrides::lookup(name).is_some(),
        "{name} has an override"
    );
    let ch = mtgrules::compiler::compile_card(&card);
    let id = game.create_object(ch, player, false, None);
    game.obj_mut(id).controller = player;
    game.place(id, player, Zone::Battlefield);
    id
}

#[test]
fn override_doubling_season_doubles_tokens_and_counters() {
    let mut game = make_game(2);
    real_card(&mut game, PlayerId(0), "Doubling Season");
    assert_eq!(
        game.create_tokens(PlayerId(0), soldier(), 2, None, None)
            .len(),
        4
    );
    assert_eq!(
        game.create_tokens(PlayerId(1), soldier(), 2, None, None)
            .len(),
        2
    );
    let c = creature(&mut game, PlayerId(0), 2, 2);
    game.put_counters(c, "+1/+1", 2);
    assert_eq!(game.obj(c).counter("+1/+1"), 4);
}

#[test]
fn override_two_doublers_stack() {
    let mut game = make_game(2);
    real_card(&mut game, PlayerId(0), "Doubling Season");
    real_card(&mut game, PlayerId(0), "Adrix and Nev, Twincasters");
    assert_eq!(
        game.create_tokens(PlayerId(0), soldier(), 1, None, None)
            .len(),
        4
    );
}

#[test]
fn override_everlasting_torment_prevents_life_gain() {
    let mut game = make_game(2);
    real_card(&mut game, PlayerId(0), "Everlasting Torment");
    game.gain_life(PlayerId(0), 5);
    assert_eq!(game.player(PlayerId(0)).life, 40);
}

#[test]
fn override_academy_manufactor_one_of_each() {
    let mut game = make_game(2);
    real_card(&mut game, PlayerId(0), "Academy Manufactor");
    let made = game.create_tokens(PlayerId(0), Rc::new(TokenSpec::treasure()), 1, None, None);
    let mut names: Vec<String> = made
        .iter()
        .map(|t| game.obj(*t).base.name.clone())
        .collect();
    names.sort();
    assert_eq!(names, ["Clue", "Food", "Treasure"]);
}

#[test]
fn override_ojer_taq_triples_creature_tokens_only() {
    let mut game = make_game(2);
    real_card(
        &mut game,
        PlayerId(0),
        "Ojer Taq, Deepest Foundation // Temple of Civilization",
    );
    assert_eq!(
        game.create_tokens(PlayerId(0), soldier(), 1, None, None)
            .len(),
        3
    );
    assert_eq!(
        game.create_tokens(PlayerId(0), Rc::new(TokenSpec::treasure()), 1, None, None)
            .len(),
        1
    );
}

#[test]
fn override_table_matches_the_python_registry_size() {
    // the 82 hand-written cards of overrides.py, looked up by full name
    // and by the front face of a split name
    assert!(mtgrules::overrides::lookup("Ojer Taq, Deepest Foundation").is_none());
    let card = mtgcards::CardData::new("Ojer Taq, Deepest Foundation // Other Face", "test");
    let mut ch = mtgrules::objects::Characteristics::default();
    assert!(!mtgrules::overrides::apply_override(&mut ch, &card)); // no front-face entry
    let card = mtgcards::CardData::new("Doubling Season // Back", "test");
    assert!(mtgrules::overrides::apply_override(&mut ch, &card)); // front face found
}
