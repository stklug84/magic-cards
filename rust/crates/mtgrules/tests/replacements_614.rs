//! Replacement and prevention effects (CR 614-616) over the emit
//! machinery: ordering, once-per-event bookkeeping, prevention, the
//! affected player's choice, and static-ability sources. The Python
//! `test_614_replacements.py` cases that go through token creation,
//! counters and life gain follow with those game actions in M6.

mod common;

use std::cell::RefCell;
use std::rc::Rc;

use common::{creature, creature_named, make_game};
use mtgrules::Game;
use mtgrules::abilities::{Ability, StaticAbility};
use mtgrules::events::{Event, EventData, EventType};
use mtgrules::objects::{ObjId, PlayerId};
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
        vec![Ability::Static(StaticAbility::replacement(
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
