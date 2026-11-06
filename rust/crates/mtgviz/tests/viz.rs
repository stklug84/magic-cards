//! Recorder / replay conformance (port of the Python `test_viz.py`): a
//! seeded game's viz stream must be complete, JSON-serializable,
//! navigable, and its final snapshot must match the engine record. The
//! fixture decks are synthetic lists of cards from the knowledge graph.

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use mtgcards::{CardDatabase, Deck};
use mtgrules::adapter::{MatchOptions, run_game};
use mtgviz::replay::{GameStream, load_games, plain_replay};
use mtgviz::schema::{field, int, text};
use mtgviz::{Recorder, ViewState, VizWriter, format_event};
use pycompat::json::{Value, dumps};
use pycompat::random::PyRandom;

const COMMANDER: &str = "Pia Nalaar, Chief Mechanic";

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root")
        .to_path_buf()
}

fn fixture_decks() -> Vec<Deck> {
    let mut cards = vec!["Sol Ring".to_owned(), "Arcane Signet".to_owned()];
    cards.extend(std::iter::repeat_n("Swamp".to_owned(), 38));
    [1, 2]
        .into_iter()
        .map(|i| Deck {
            name: format!("Fixture #{i}"),
            path: format!("fixture-{i}.txt"),
            cards: cards.clone(),
            commander: Some(COMMANDER.to_owned()),
            fmt: String::new(),
            skipped: Vec::new(),
        })
        .collect()
}

/// Run one recorded game: `(engine record, viz records)`.
fn run_recorded(seed: i128, turn_cap: u32) -> (Value, Vec<Value>) {
    let mut db = CardDatabase::load(&repo_root(), None, &[]).expect("the graph loads");
    let records = Rc::new(RefCell::new(Vec::new()));
    let recorder = Recorder::collecting(Rc::clone(&records)).shared();
    let mut rng = PyRandom::new(seed);
    let options = MatchOptions {
        turn_cap,
        ..MatchOptions::default()
    };
    let rec = run_game(
        &fixture_decks(),
        &mut db,
        &mut rng,
        &options,
        None,
        Some(&recorder),
    );
    drop(recorder);
    let records = Rc::try_unwrap(records)
        .expect("the recorder is gone")
        .into_inner();
    (rec, records)
}

fn of_type<'a>(records: &'a [Value], t: &str) -> Vec<&'a Value> {
    records.iter().filter(|r| text(r, "t") == t).collect()
}

#[test]
fn stream_shape_and_determinism() {
    let (rec, records) = run_recorded(11, 12);
    // events, snapshots, and a final end record
    assert!(of_type(&records, "e").len() > 1);
    assert!(of_type(&records, "s").len() > 1);
    assert_eq!(text(records.last().expect("records"), "t"), "end");
    // seq strictly increasing over events + snapshots
    let seqs: Vec<i64> = records
        .iter()
        .filter(|r| field(r, "seq").is_some())
        .map(|r| int(r, "seq"))
        .collect();
    assert!(seqs.windows(2).all(|w| w[0] < w[1]));
    // every record serializes (the JSONL writer contract)
    for r in &records {
        assert!(dumps(r).len() > 2);
    }
    // golden invariant: the last snapshot's life totals == the game record
    let last_snap = of_type(&records, "s").last().copied().expect("a snapshot");
    let Some(Value::List(players)) = field(last_snap, "players") else {
        panic!("players");
    };
    let Some(Value::Dict(by_name)) = field(&rec, "players") else {
        panic!("record players");
    };
    for pd in players {
        let name = text(pd, "name");
        let engine = by_name
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v)
            .expect("the player's record");
        assert_eq!(int(pd, "life"), int(engine, "life"));
    }
    let end = records.last().expect("records");
    assert_eq!(text(end, "winner"), text(&rec, "winner"));
    assert_eq!(text(end, "reason"), text(&rec, "reason"));
    assert_eq!(int(end, "turns"), int(&rec, "turns"));
    // format_event renders every event kind to a non-empty line
    for r in of_type(&records, "e") {
        assert!(!format_event(r).is_empty(), "{}", dumps(r));
    }
    // the same seed reproduces the identical record stream
    let (_, again) = run_recorded(11, 12);
    assert_eq!(records.len(), again.len());
    assert_eq!(
        dumps(records.last().expect("records")),
        dumps(again.last().expect("records"))
    );
    assert_eq!(
        records.iter().map(dumps).collect::<Vec<_>>(),
        again.iter().map(dumps).collect::<Vec<_>>()
    );
}

#[test]
fn view_navigation() {
    let (_, records) = run_recorded(11, 12);
    let stream: Vec<Value> = records
        .iter()
        .filter(|r| matches!(text(r, "t"), "e" | "s"))
        .cloned()
        .collect();
    let n = i64::try_from(stream.len()).expect("fits");
    let mut view = ViewState::new(stream, Value::dict([("seed", Value::from(11_i64))]));
    view.jump_turn(3);
    assert!(int(view.snapshot().expect("a snapshot"), "turn") <= 3);
    let cur = view.cursor;
    view.next_phase(1);
    assert!(view.cursor > cur);
    view.next_turn(1);
    assert!(int(view.snapshot().expect("a snapshot"), "turn") >= 3);
    view.cursor = n - 1;
    assert!(view.at_end());
    // stepping back never panics and reaches the front
    for _ in 0..(n + 2) {
        view.step(-1);
    }
    assert!(view.cursor <= 0);
}

#[test]
fn viz_file_round_trips_through_load_games_and_the_plain_viewer() {
    let mut db = CardDatabase::load(&repo_root(), None, &[]).expect("the graph loads");
    let tmp = tempfile::tempdir().expect("tempdir");
    let path = tmp.path().join("games.jsonl");
    let writer = VizWriter::open(&path).expect("writable");
    let decks = fixture_decks();
    let options = MatchOptions {
        turn_cap: 4,
        ..MatchOptions::default()
    };
    let mut rng = PyRandom::new(3);
    for game_no in 1..=2 {
        let recorder = writer.recorder(game_no, 3);
        run_game(&decks, &mut db, &mut rng, &options, None, Some(&recorder));
    }
    writer.close().expect("flushed");
    let games = load_games(&path).expect("loads");
    assert_eq!(games.keys().copied().collect::<Vec<_>>(), [1, 2]);
    let game = &games[&2];
    assert_eq!(int(&game.meta, "seed"), 3);
    assert!(game.result.as_ref().is_some_and(|r| text(r, "t") == "end"));
    assert!(
        game.records
            .iter()
            .all(|r| matches!(text(r, "t"), "e" | "s"))
    );
    // the plain viewer walks the whole game on 'a'
    let stream = GameStream {
        meta: game.meta.clone(),
        records: game.records.clone(),
        result: game.result.clone(),
    };
    let mut input = std::io::Cursor::new(b"a\n".to_vec());
    let mut out = Vec::new();
    plain_replay(stream, &mut input, &mut out);
    let shown = String::from_utf8(out).expect("utf-8");
    assert!(shown.starts_with("replaying game 2 (seed 3), "), "{shown}");
    assert!(shown.contains("=== Turn 1 untap (active: "), "{shown}");
    assert!(shown.contains("=== winner: "), "{shown}");
    // the error paths carry the Python messages
    let missing = tmp.path().join("missing.jsonl");
    let err = load_games(&missing).expect_err("no file");
    assert!(
        err.starts_with("cannot open replay file: [Errno 2] No such file or directory: "),
        "{err}"
    );
    let err = mtgviz::replay::replay_file(&path, 7).expect_err("no game 7");
    assert_eq!(
        err,
        format!("{}: no game 7; available: 1, 2", path.display())
    );
    std::fs::write(&path, "").expect("truncate");
    let err = mtgviz::replay::replay_file(&path, 1).expect_err("no games");
    assert_eq!(
        err,
        format!(
            "{}: no recorded games found (record with --viz-file)",
            path.display()
        )
    );
}

#[test]
fn snapshots_name_planeswalker_targets_ability_stack_entries_and_unknown_commanders() {
    use mtgrules::Game;
    use mtgrules::abilities::{Ability, ActivatedAbility};
    use mtgrules::objects::{Characteristics, Defender, ObjId, PlayerId, Zone};
    use mtgrules::policy::TrivialPolicy;
    use mtgviz::schema::snapshot;

    let mut game = Game::new(
        &["p0", "p1"],
        PyRandom::new(1),
        &[Rc::new(TrivialPolicy)],
        40,
    );
    let (p0, p1) = (PlayerId(0), PlayerId(1));
    let creature = |name: &str| Characteristics {
        name: name.to_owned(),
        types: ["Creature".to_owned()].into_iter().collect(),
        power: Some(2),
        toughness: Some(2),
        ..Characteristics::default()
    };
    let walker = game.create_object(
        Characteristics {
            name: "Jace".to_owned(),
            types: ["Planeswalker".to_owned()].into_iter().collect(),
            ..Characteristics::default()
        },
        p1,
        false,
        None,
    );
    game.place(walker, p1, Zone::Battlefield);
    let attacker = game.create_object(creature("Bear"), p0, false, None);
    game.place(attacker, p0, Zone::Battlefield);
    game.obj_mut(attacker).attacking = Some(Defender::Object(walker));
    let blocker = game.create_object(creature("Wall"), p1, false, None);
    game.place(blocker, p1, Zone::Battlefield);
    game.obj_mut(blocker).blocking.push(attacker);
    game.obj_mut(blocker).damage = 1;
    game.obj_mut(blocker).counters.insert("+1/+1".to_owned(), 1);
    game.obj_mut(blocker).counters.insert("stun".to_owned(), 0); // dropped from the record
    // an ability on the stack
    let ability = Rc::new(ActivatedAbility {
        effect: Some(mtgrules::effects::Effect::draw(
            mtgrules::protocols::CountValue::Fixed(1),
        )),
        ..ActivatedAbility::default()
    });
    game.obj_mut(attacker)
        .base_mut()
        .abilities
        .push(Ability::Activated(Rc::clone(&ability)));
    assert!(game.activate_ability(p0, attacker, &ability, 0));
    // commander damage from a commander no longer anywhere
    game.player_mut(p1).commander_damage.insert(ObjId(999), 5);
    game.player_mut(p1).mana_pool.add("G", 2);
    game.player_mut(p1).mana_pool.add("W", 1);
    let snap = snapshot(&game, 3);
    let text = dumps(&snap);
    assert!(text.contains("\"attacking\": \"Jace\""), "{text}");
    assert!(text.contains("\"blocking\": [\"Bear\"]"), "{text}");
    assert!(text.contains("\"damage\": 1"), "{text}");
    assert!(text.contains("\"counters\": {\"+1/+1\": 1}"), "{text}");
    assert!(
        text.contains("\"stack\": [\"ability of Bear (p0)\"]"),
        "{text}"
    );
    assert!(text.contains("\"cmd_damage\": {\"999\": 5}"), "{text}");
    assert!(text.contains("\"mana_pool\": \"GGW\""), "{text}");
    assert!(
        text.contains("\"name\": \"Jace\", \"types\": [\"Planeswalker\"], \"tapped\": false}"),
        "{text}"
    );
}
