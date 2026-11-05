//! Targeting (CR 115) and trigger matching (CR 603.1) over the kernel:
//! `TargetSpec::legal`, `Game::legal_targets`, `still_legal_target`,
//! `TriggerSpec::matches`, APNAP order and hexproof.

mod common;

use std::rc::Rc;

use common::{CreatureSpec, card_in_hand, creature, creature_with, make_game};
use mtgrules::abilities::{TargetSpec, TriggerSpec};
use mtgrules::effects::Ctx;
use mtgrules::events::{Event, EventData, EventType};
use mtgrules::objects::{PlayerId, Zone};
use mtgrules::stack::Target;

#[test]
fn player_and_opponent_specs() {
    let mut game = make_game(3);
    let ctx = Ctx::new(PlayerId(0), None);
    let any_player = TargetSpec::new("player");
    assert!(any_player.legal(&game, &ctx, Target::Player(PlayerId(0))));
    let opponent = TargetSpec::new("opponent");
    assert!(!opponent.legal(&game, &ctx, Target::Player(PlayerId(0))));
    assert!(opponent.legal(&game, &ctx, Target::Player(PlayerId(2))));
    game.player_mut(PlayerId(2)).lost = true;
    assert!(!opponent.legal(&game, &ctx, Target::Player(PlayerId(2))));
    assert_eq!(
        game.legal_targets(&opponent, &ctx),
        vec![Target::Player(PlayerId(1))]
    );
    assert_eq!(game.opponents(PlayerId(0)), vec![PlayerId(1)]);
    // a creature is never a legal "player" target
    let c = creature(&mut game, PlayerId(1), 1, 1);
    assert!(!any_player.legal(&game, &ctx, Target::Object(c)));
}

#[test]
fn object_specs_check_zone_type_controller_and_hexproof() {
    let mut game = make_game(2);
    let (p0, p1) = (PlayerId(0), PlayerId(1));
    let mine = creature(&mut game, p0, 2, 2);
    let theirs = creature_with(
        &mut game,
        p1,
        CreatureSpec {
            name: "Shrouded",
            keywords: vec!["hexproof"],
            ..CreatureSpec::default()
        },
    );
    let artifact = creature_with(
        &mut game,
        p1,
        CreatureSpec {
            name: "Rock",
            types: vec!["Artifact"],
            ..CreatureSpec::default()
        },
    );
    let in_hand = card_in_hand(&mut game, p0, "Grizzly", "{1}{G}", &["Creature"]);
    let ctx = Ctx::new(p0, Some(mine));
    let creature_spec = TargetSpec::new("creature");
    assert!(creature_spec.legal(&game, &ctx, Target::Object(mine)));
    // hexproof: not by an opponent's effect, fine for its own controller
    assert!(!creature_spec.legal(&game, &ctx, Target::Object(theirs)));
    assert!(creature_spec.legal(&game, &Ctx::new(p1, None), Target::Object(theirs)));
    assert!(!creature_spec.legal(&game, &ctx, Target::Object(artifact)));
    assert!(!creature_spec.legal(&game, &ctx, Target::Object(in_hand)));
    assert!(TargetSpec::new("art_ench").legal(&game, &ctx, Target::Object(artifact)));
    assert!(TargetSpec::new("nonland").legal(&game, &ctx, Target::Object(artifact)));
    assert!(TargetSpec::new("permanent").legal(&game, &ctx, Target::Object(artifact)));
    assert!(TargetSpec::new("whatever").legal(&game, &ctx, Target::Object(artifact)));
    assert!(!TargetSpec::new("land").legal(&game, &ctx, Target::Object(artifact)));
    // "another target creature"
    let other = TargetSpec {
        other: true,
        ..TargetSpec::new("creature")
    };
    assert!(!other.legal(&game, &ctx, Target::Object(mine)));
    // controller restrictions
    let yours = TargetSpec {
        controller: "you".to_owned(),
        ..TargetSpec::new("permanent")
    };
    assert!(
        yours.legal(&game, &ctx, Target::Object(mine))
            && !yours.legal(&game, &ctx, Target::Object(artifact))
    );
    let opp = TargetSpec {
        controller: "opponent".to_owned(),
        ..TargetSpec::new("permanent")
    };
    assert!(
        !opp.legal(&game, &ctx, Target::Object(mine))
            && opp.legal(&game, &ctx, Target::Object(artifact))
    );
    // "any": creatures, planeswalkers and players
    let any = TargetSpec::new("any");
    let legal = game.legal_targets(&any, &ctx);
    assert_eq!(
        legal,
        vec![Target::Object(mine), Target::Player(p0), Target::Player(p1)]
    );
}

#[test]
fn rule_608_2b_recheck_on_resolution() {
    let mut game = make_game(2);
    let p0 = PlayerId(0);
    let c = creature(&mut game, p0, 2, 2);
    let mut ctx = Ctx::new(p0, None);
    ctx.targets = vec![Target::Object(c)];
    // without a spec only the zone is known
    assert!(game.still_legal_target(Target::Object(c), &ctx, 0));
    assert_eq!(ctx.legal_group(&game, 0), vec![Target::Object(c)]);
    game.obj_mut(c).zone = Zone::Graveyard;
    assert!(!game.still_legal_target(Target::Object(c), &ctx, 0));
    assert_eq!(ctx.legal_group(&game, 0), Vec::<Target>::new());
    // with the spec the full check runs again (fix F7): a creature that
    // stopped being one is no longer a legal "creature" target
    game.obj_mut(c).zone = Zone::Battlefield;
    ctx.specs = vec![TargetSpec::new("creature")];
    assert!(game.still_legal_target(Target::Object(c), &ctx, 0));
    game.obj_mut(c).base_mut().types = common::set(&["Artifact"]);
    game.bump();
    assert!(!game.still_legal_target(Target::Object(c), &ctx, 0));
    // players stay legal without a spec
    assert!(game.still_legal_target(Target::Player(PlayerId(1)), &ctx, 3));
}

#[test]
fn trigger_specs_default_to_their_source() {
    let mut game = make_game(2);
    let a = creature(&mut game, PlayerId(0), 1, 1);
    let b = creature(&mut game, PlayerId(0), 1, 1);
    let own = TriggerSpec::on(EventType::Dies);
    assert!(own.matches(&game, a, &Event::about(EventType::Dies, a)));
    assert!(!own.matches(&game, a, &Event::about(EventType::Dies, b)));
    assert!(!own.matches(&game, a, &Event::about(EventType::EntersBattlefield, a)));
    let any_creature = TriggerSpec::when(
        EventType::Dies,
        Rc::new(|game, _source, event| {
            event
                .data
                .obj
                .is_some_and(|o| game.chars(o).types.contains("Creature"))
        }),
    );
    assert!(any_creature.matches(&game, a, &Event::about(EventType::Dies, b)));
    let upkeep = TriggerSpec {
        step: "upkeep".to_owned(),
        ..TriggerSpec::when(
            EventType::BeginStep,
            Rc::new(|_g, _s, e| e.data.step.as_deref() == Some("upkeep")),
        )
    };
    let event = Event::new(
        EventType::BeginStep,
        EventData {
            step: Some("upkeep".to_owned()),
            player: Some(PlayerId(0)),
            ..EventData::default()
        },
    );
    assert!(upkeep.matches(&game, a, &event));
}

#[test]
fn apnap_order_and_kernel_helpers() {
    let mut game = make_game(4);
    game.active_idx = 2;
    assert_eq!(
        game.players_apnap(),
        vec![PlayerId(2), PlayerId(3), PlayerId(0), PlayerId(1)]
    );
    game.player_mut(PlayerId(3)).lost = true;
    assert_eq!(
        game.players_apnap(),
        vec![PlayerId(2), PlayerId(0), PlayerId(1)]
    );
    assert_eq!(game.alive().len(), 3);
    assert_eq!(game.active_player(), PlayerId(2));
    let a = creature(&mut game, PlayerId(1), 1, 1);
    let b = creature(&mut game, PlayerId(0), 1, 1);
    // battlefields in seat order, each in order of entry
    assert_eq!(game.battlefield_objects(), vec![b, a]);
    assert_eq!(
        game.commander_identity(PlayerId(0)),
        common::set(&["W", "U", "B", "R", "G"])
    );
    assert_eq!(game.obj(a).to_string(), "<Bear#1 battlefield>");
    assert!(game.next_timestamp() > game.obj(b).timestamp);
}
