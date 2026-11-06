//! Determinism (fix F0): the canonical mana order and the engine's
//! reproducibility - port of the Python `test_determinism.py`. The
//! Python `PYTHONHASHSEED` sweep has no Rust counterpart (there is no
//! hash randomisation to defeat); its stand-in is the end-to-end replay
//! of the same fixture games from the same seed. The `_ManaPlan`
//! internals are covered by the engine vectors (`tests/vectors`).

mod common;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use common::FakeRef;
use mtgcards::{CardDatabase, Deck};
use mtgrules::adapter::{MatchOptions, run_game};
use mtgrules::compiler::compile_card;
use mtgrules::effects::Effect;
use mtgrules::manasys::{ManaPool, mana_order, parse_cost};
use pycompat::json::{Value, dumps};
use pycompat::random::PyRandom;

#[test]
fn mana_order_is_wubrgc() {
    assert_eq!(mana_order(["G", "C", "W", "B"]), ["W", "B", "G", "C"]);
    assert_eq!(
        mana_order(["R", "U", "B", "G", "W"]),
        ["W", "U", "B", "R", "G"]
    );
    assert_eq!(mana_order::<[&str; 0], &str>([]), Vec::<String>::new());
}

#[test]
fn unknown_types_sort_last_alphabetically() {
    assert_eq!(mana_order(["ANY", "C", "R", "A"]), ["R", "C", "A", "ANY"]);
}

#[test]
fn land_mana_ability_lists_colours_canonically() {
    let ch = compile_card(
        &FakeRef {
            types: vec!["Land"],
            mana_cost: "",
            power: None,
            toughness: None,
            land_colors: vec!["R", "B", "U"],
            ..FakeRef::new("Tri Land", "")
        }
        .card(),
    );
    let mana: Vec<_> = ch
        .abilities
        .iter()
        .filter_map(|a| a.as_activated().filter(|ab| ab.is_mana_ability))
        .collect();
    assert_eq!(mana.len(), 1);
    let Some(Effect::AddMana(add)) = &mana[0].effect else {
        panic!(
            "land mana ability has no AddMana effect: {:?}",
            mana[0].effect
        );
    };
    assert_eq!(add.types, ["U", "B", "R"]);
}

#[test]
fn hybrid_payment_from_pool_prefers_canonical_colour_on_ties() {
    let mut pool = ManaPool::new();
    pool.add("U", 1);
    pool.add("W", 1);
    assert!(pool.pay(&parse_cost("{W/U}")));
    assert_eq!((pool.get("W"), pool.get("U")), (0, 1));
}

// ---- the end-to-end replay

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(Path::to_path_buf)
        .expect("rust/crates/mtgrules sits three levels below the repo root")
}

/// Dual/tri lands, hybrid-cost spells, and cheap creatures: the cards
/// whose mana handling iterated colour sets before F0.
fn fixture_decks(db: &CardDatabase) -> Vec<Deck> {
    let mut names: BTreeSet<&str> = BTreeSet::new();
    let mut cards = Vec::new();
    // keyed by the record's own name, like the Python `{c.name: c}`
    for (_, card) in db.index.iter() {
        if names.insert(card.name.as_str()) {
            cards.push((card.name.as_str(), card));
        }
    }
    cards.sort_by_key(|(name, _)| *name);
    let pick = |pred: &dyn Fn(&mtgcards::CardData) -> bool, n: usize| -> Vec<String> {
        cards
            .iter()
            .filter(|(_, c)| pred(c))
            .map(|(name, _)| (*name).to_owned())
            .take(n)
            .collect()
    };
    let duals = pick(
        &|c| c.is_land() && c.behavior.land_colors().is_some_and(|l| l.len() >= 2),
        20,
    );
    let hybrid = pick(
        &|c| c.mana_cost.contains('/') && !c.mana_cost.contains('P'),
        19,
    );
    let creatures = pick(
        &|c| c.is_creature() && (1..=3).contains(&c.mv) && !c.supertypes.contains("Legendary"),
        40,
    );
    let commander = pick(
        &|c| c.is_creature() && c.supertypes.contains("Legendary") && c.mv <= 4,
        1,
    )
    .remove(0);
    let mut pool: Vec<String> = duals;
    for (basic, n) in [
        ("Plains", 4),
        ("Island", 3),
        ("Swamp", 3),
        ("Mountain", 3),
        ("Forest", 3),
    ] {
        pool.extend(std::iter::repeat_n(basic.to_owned(), n));
    }
    pool.extend(hybrid);
    pool.extend(creatures);
    pool.push("Sol Ring".to_owned());
    pool.push("Arcane Signet".to_owned());
    [1, 2]
        .into_iter()
        .map(|i| Deck {
            name: format!("Hash #{i}"),
            path: format!("hash-{i}.txt"),
            cards: pool.clone(),
            commander: Some(commander.clone()),
            fmt: String::new(),
            skipped: Vec::new(),
        })
        .collect()
}

/// The records of `games` fixture games from `seed`, as one string.
fn stream(db: &mut CardDatabase, seed: i128, games: u64) -> String {
    let decks = fixture_decks(db);
    let mut rng = PyRandom::new(seed);
    let options = MatchOptions {
        turn_cap: 20,
        ..MatchOptions::default()
    };
    let mut out = String::new();
    for _ in 0..games {
        let rec: Value = run_game(&decks, db, &mut rng, &options, None, None);
        out.push_str(&dumps(&rec));
        out.push('\n');
    }
    out
}

#[test]
fn the_same_seed_replays_the_same_games() {
    let mut db = CardDatabase::load(&repo_root(), None, &[]).expect("the checkout's graph loads");
    let first = stream(&mut db, 7, 3);
    let second = stream(&mut db, 7, 3);
    assert_eq!(first, second);
    assert_eq!(first.lines().count(), 3);
    assert_ne!(
        stream(&mut db, 8, 1),
        first
            .lines()
            .next()
            .map(|l| format!("{l}\n"))
            .unwrap_or_default()
    );
}
