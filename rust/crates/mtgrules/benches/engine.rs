//! Criterion benchmarks of the engine's hot paths: compiling every card
//! of the checkout's graph, the mana solver on a developed board, and a
//! complete two-player game from decklists.
//!
//! ```text
//! cargo bench -p mtgrules
//! ```
//!
//! The graph benchmarks need the checkout (`MTG_GRAPH_ROOT` or the
//! repository above `rust/`) and skip with a notice without it; the
//! full game additionally needs the Comprehensive Rules text only for
//! `mtg-cr`, not here - the engine itself does not read it.

use std::path::{Path, PathBuf};
use std::rc::Rc;

use criterion::{Criterion, criterion_group, criterion_main};
use mtgcards::{CardDatabase, Deck};
use mtgrules::Game;
use mtgrules::abilities::{Ability, ActivatedAbility};
use mtgrules::adapter::{MatchOptions, run_game};
use mtgrules::compiler::compile_card;
use mtgrules::effects::{AddMana, Effect};
use mtgrules::manasys::parse_cost;
use mtgrules::objects::{Characteristics, PlayerId, Zone};
use mtgrules::policy::DefaultPolicy;
use pycompat::random::PyRandom;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root")
        .to_path_buf()
}

fn database() -> Option<CardDatabase> {
    let root = std::env::var_os("MTG_GRAPH_ROOT").map_or_else(repo_root, PathBuf::from);
    if !root.join("sets").is_dir() {
        eprintln!(
            "notice: no graph at {}, skipping the graph benchmarks",
            root.display()
        );
        return None;
    }
    CardDatabase::load(&root, None, &[]).ok()
}

/// Compile every distinct card record of the graph (what a match does
/// once per deck card).
fn bench_compile(c: &mut Criterion) {
    let Some(db) = database() else { return };
    let records: Vec<_> = db
        .index
        .unique_ids()
        .into_iter()
        .map(|id| db.index.record(id).clone())
        .collect();
    c.bench_function("compile_card / whole graph", |b| {
        b.iter(|| {
            for card in &records {
                std::hint::black_box(compile_card(card));
            }
        });
    });
}

/// `can_pay_mana` for a three-colour cost on a board of eight lands of
/// mixed production (the policy probes this for every castable card).
fn bench_mana(c: &mut Criterion) {
    let mut game = Game::new(
        &["p0", "p1"],
        PyRandom::new(1),
        &[Rc::new(DefaultPolicy::new())],
        40,
    );
    let land = |add: AddMana| -> Characteristics {
        Characteristics {
            name: "Land".to_owned(),
            types: ["Land".to_owned()].into_iter().collect(),
            abilities: vec![Ability::from(ActivatedAbility {
                tap_cost: true,
                is_mana_ability: true,
                effect: Some(Effect::AddMana(add)),
                ..ActivatedAbility::default()
            })],
            ..Characteristics::default()
        }
    };
    let sources = [
        AddMana::fixed(&["W"]),
        AddMana::fixed(&["U"]),
        AddMana::fixed(&["B"]),
        AddMana {
            choice: true,
            ..AddMana::fixed(&["R", "G"])
        },
        AddMana {
            choice: true,
            ..AddMana::fixed(&["W", "U"])
        },
        AddMana::any(),
        AddMana::fixed(&["C", "C"]),
        AddMana {
            commander_identity: true,
            ..AddMana::default()
        },
    ];
    for add in sources {
        let id = game.create_object(land(add), PlayerId(0), false, None);
        game.place(id, PlayerId(0), Zone::Battlefield);
    }
    let cost = parse_cost("{2}{W}{U}{B}{R/G}");
    c.bench_function("can_pay_mana / 8 sources, 3 colours + hybrid", |b| {
        b.iter(|| std::hint::black_box(game.can_pay_mana(PlayerId(0), &cost)));
    });
}

/// One complete two-player game (40 turns at most) from decklists over
/// the graph: setup, mulligans, every turn with the default policy.
fn bench_game(c: &mut Criterion) {
    let Some(mut db) = database() else { return };
    let mut cards = vec!["Sol Ring".to_owned(), "Arcane Signet".to_owned()];
    cards.extend(std::iter::repeat_n("Swamp".to_owned(), 38));
    let decks: Vec<Deck> = [1, 2]
        .into_iter()
        .map(|i| Deck {
            name: format!("Bench #{i}"),
            path: format!("bench-{i}.txt"),
            cards: cards.clone(),
            commander: Some("Pia Nalaar, Chief Mechanic".to_owned()),
            fmt: String::new(),
            skipped: Vec::new(),
        })
        .collect();
    let options = MatchOptions::default();
    c.bench_function("run_game / 2 players, 40 turns", |b| {
        b.iter(|| {
            let mut rng = PyRandom::new(7);
            std::hint::black_box(run_game(&decks, &mut db, &mut rng, &options, None, None))
        });
    });
}

criterion_group!(benches, bench_compile, bench_mana, bench_game);
criterion_main!(benches);
