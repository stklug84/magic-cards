//! The whole card pool in play: every distinct card of the checkout's
//! graph is dealt into decks and those decks play four-player games
//! under every AI profile. The assertion is robustness - no panic, every
//! game ends inside the turn cap, every record is well-formed - over the
//! real cards and their hand-written overrides, which the per-rule tests
//! exercise only a handful at a time.

use std::path::{Path, PathBuf};

use mtgcards::{CardData, CardDatabase, Deck};
use mtgrules::adapter::{MatchOptions, run_game};
use mtgrules::compiler::{self, compile_card};
use mtgrules::objects::Characteristics;
use mtgrules::overrides;
use mtgrules::policy::PolicyProfile;
use pycompat::json::Value;
use pycompat::random::PyRandom;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root")
        .to_path_buf()
}

fn db() -> CardDatabase {
    CardDatabase::load(&repo_root(), None, &[]).expect("the checkout's graph loads")
}

/// Every distinct card record, by name.
fn records(db: &CardDatabase) -> Vec<&CardData> {
    let mut out: Vec<&CardData> = db
        .index
        .unique_ids()
        .into_iter()
        .map(|id| db.index.record(id))
        .collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}

/// Decks of `spells_per_deck` nonland cards each plus 39 basic lands,
/// covering every nonland card of the graph once; the commanders are
/// the legendary creatures, cycled.
fn pool_decks(db: &CardDatabase, spells_per_deck: usize) -> Vec<Deck> {
    let all = records(db);
    let spells: Vec<&str> = all
        .iter()
        .filter(|c| !c.is_land() && !c.name.contains(" // "))
        .map(|c| c.name.as_str())
        .collect();
    let commanders: Vec<&str> = all
        .iter()
        .filter(|c| {
            c.is_creature() && c.supertypes.contains("Legendary") && !c.name.contains(" // ")
        })
        .map(|c| c.name.as_str())
        .collect();
    assert!(!commanders.is_empty() && spells.len() > spells_per_deck);
    let basics = ["Plains", "Island", "Swamp", "Mountain", "Forest"];
    spells
        .chunks(spells_per_deck)
        .enumerate()
        .map(|(i, chunk)| {
            let mut cards: Vec<String> = chunk.iter().map(|n| (*n).to_owned()).collect();
            for k in 0..39 {
                cards.push(basics[(i + k) % basics.len()].to_owned());
            }
            Deck {
                name: format!("Pool {i}"),
                path: format!("pool-{i}.txt"),
                cards,
                commander: Some(commanders[i % commanders.len()].to_owned()),
                fmt: String::new(),
                skipped: Vec::new(),
            }
        })
        .collect()
}

fn field<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    match record {
        Value::Dict(fields) => fields.iter().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

#[test]
fn every_card_compiles_and_the_override_table_is_used() {
    let db = db();
    compiler::clear_unknown_clauses();
    let mut compiled = 0;
    let mut overridden = 0;
    for card in records(&db) {
        let ch: Characteristics = compile_card(card);
        assert_eq!(ch.name, card.name);
        compiled += 1;
        if overrides::lookup(&card.name).is_some()
            || card
                .name
                .split_once(" // ")
                .is_some_and(|(front, _)| overrides::lookup(front).is_some())
        {
            overridden += 1;
        }
    }
    assert!(compiled > 1000, "{compiled} cards compiled");
    // the collection holds a good part of the 82 hand-written cards ...
    assert!(
        overridden >= 40,
        "{overridden} overridden cards in the graph"
    );
    assert!(!overrides::notes().is_empty());
    // ... and every implementation builds from a bare card record
    let mut names = 0;
    for name in overrides::names() {
        let card = CardData {
            types: ["Creature".to_owned()].into_iter().collect(),
            mana_cost: "{2}".to_owned(),
            ..CardData::new(name, "test")
        };
        let ch = compile_card(&card);
        assert_eq!(ch.name, name);
        assert!(
            !ch.abilities.is_empty() || !ch.keywords.is_empty(),
            "{name} compiled to nothing"
        );
        names += 1;
    }
    assert_eq!(names, 82);
    // the compiler reports what it could not read, never silently
    let unknown = compiler::unknown_clauses();
    assert!(!unknown.is_empty());
    for (name, clauses) in &unknown {
        assert_ne!(name, "");
        assert!(
            clauses.iter().all(|c| !c.trim().is_empty()),
            "{name}: {clauses:?}"
        );
    }
}

#[test]
fn the_whole_pool_plays_four_player_games_under_every_profile() {
    let mut db = db();
    let decks = pool_decks(&db, 60);
    assert!(decks.len() >= 8, "{} decks", decks.len());
    let profiles = [
        PolicyProfile::default(),
        PolicyProfile::aggressive(),
        PolicyProfile::control(),
        PolicyProfile::default(),
    ];
    let mut games = 0;
    let mut finished_early = 0;
    for (pod_no, pod) in decks.chunks(4).enumerate() {
        if pod.len() < 2 {
            continue;
        }
        let options = MatchOptions {
            turn_cap: 30,
            profiles: profiles.iter().take(pod.len()).cloned().map(Some).collect(),
            starting_player: None,
        };
        let mut rng = PyRandom::new(100 + i128::try_from(pod_no).expect("small"));
        for _ in 0..2 {
            let rec = run_game(pod, &mut db, &mut rng, &options, None, None);
            games += 1;
            let turns = match field(&rec, "turns") {
                Some(Value::Int(n)) => *n,
                other => panic!("turns: {other:?}"),
            };
            assert!((1..=30).contains(&turns), "{rec:?}");
            let reason = match field(&rec, "reason") {
                Some(Value::Str(s)) => s.as_str(),
                other => panic!("reason: {other:?}"),
            };
            assert!(
                [
                    "life",
                    "decked",
                    "poison",
                    "commander",
                    "alt_win",
                    "elimination",
                    "turn_cap",
                    "draw"
                ]
                .contains(&reason),
                "{reason}"
            );
            if reason != "turn_cap" {
                finished_early += 1;
            }
            let Some(Value::Dict(players)) = field(&rec, "players") else {
                panic!("players: {rec:?}");
            };
            assert_eq!(players.len(), pod.len());
            for (name, stats) in players {
                assert!(pod.iter().any(|d| d.name == *name));
                assert!(matches!(field(stats, "life"), Some(Value::Int(_))));
                assert!(matches!(field(stats, "cards_cast"), Some(Value::List(_))));
            }
        }
    }
    assert!(games >= 12, "{games} games");
    // the pool is strong enough that some games end before the cap
    assert!(
        finished_early >= 1,
        "no game ended before the turn cap in {games} games"
    );
}
