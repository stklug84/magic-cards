//! Game setup (CR 103): the random starting player (fix F12), the
//! shuffles and the London mulligans with the free first mulligan of a
//! multiplayer game (rule 103.5c, fix F8), and the first-turn draw skip
//! (103.8a). Port of the Python `test_103_game_setup.py`; needs the
//! checkout's knowledge graph.

mod common;

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use indexmap::IndexMap;
use mtgcards::{CardDatabase, Deck};
use mtgrules::Game;
use mtgrules::adapter::{MatchOptions, mulligans, run_game, setup_game};
use mtgrules::objects::{ObjId, PlayerId};
use mtgrules::policy::{DefaultPolicy, Policy};
use pycompat::json::Value;
use pycompat::random::PyRandom;

/// An all-land library: the default policy always mulligans (> 5 lands)
/// until its `max_mulligans` (3), which makes the hand sizes predictable.
const COMMANDER: &str = "Pia Nalaar, Chief Mechanic";

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(Path::to_path_buf)
        .expect("rust/crates/mtgrules sits three levels below the repo root")
}

fn db() -> CardDatabase {
    CardDatabase::load(&repo_root(), None, &[]).expect("the checkout's graph loads")
}

fn decks(n: usize) -> Vec<Deck> {
    (0..n)
        .map(|i| Deck {
            name: format!("Seat {i}"),
            path: format!("seat-{i}.txt"),
            cards: vec!["Swamp".to_owned(); 40],
            commander: Some(COMMANDER.to_owned()),
            fmt: String::new(),
            skipped: Vec::new(),
        })
        .collect()
}

fn setup(db: &mut CardDatabase, n: usize, seed: i128) -> Game {
    setup_game(
        &decks(n),
        db,
        PyRandom::new(seed),
        &MatchOptions::default(),
        None,
    )
}

#[test]
fn rule_103_1_starting_player_is_random() {
    let mut db = db();
    let starters: BTreeSet<usize> = (0..12)
        .map(|seed| setup(&mut db, 2, seed).active_idx)
        .collect();
    assert_eq!(starters, BTreeSet::from([0, 1]));
}

#[test]
fn rule_103_1_starting_player_is_seeded() {
    let mut db = db();
    assert_eq!(
        setup(&mut db, 3, 5).active_idx,
        setup(&mut db, 3, 5).active_idx
    );
}

#[test]
fn rule_103_5_two_players_bottom_one_card_per_mulligan() {
    let mut db = db();
    let game = setup(&mut db, 2, 1);
    for p in &game.players {
        assert_eq!(p.stats.get("mulligans"), Some(&3));
        assert_eq!(p.hand.len(), 4);
        assert_eq!(p.library.len(), 36);
    }
}

#[test]
fn rule_103_5c_multiplayer_first_mulligan_is_free() {
    // the default policy stops after three *counted* mulligans, so it
    // takes four in total and still bottoms three
    let mut db = db();
    let game = setup(&mut db, 3, 1);
    for p in &game.players {
        assert_eq!(p.stats.get("mulligans"), Some(&4));
        assert_eq!(p.hand.len(), 4);
        assert_eq!(p.library.len(), 36);
    }
}

/// A fixed decision: mulligan twice, then keep.
struct TwoMulligans {
    inner: DefaultPolicy,
    calls: RefCell<IndexMap<PlayerId, usize>>,
}

impl Policy for TwoMulligans {
    fn keep_hand(&self, _game: &Game, player: PlayerId, _hand: &[ObjId], _mulls: usize) -> bool {
        let mut calls = self.calls.borrow_mut();
        let n = calls.entry(player).or_insert(0);
        *n += 1;
        *n > 2
    }

    fn bottom_cards(&self, game: &Game, player: PlayerId, hand: &[ObjId], n: usize) -> Vec<ObjId> {
        self.inner.bottom_cards(game, player, hand, n)
    }
}

#[test]
fn rule_103_5c_only_the_first_mulligan_is_free() {
    let mut db = db();
    for (n_players, hand_size) in [(2, 5), (3, 6)] {
        let mut game = setup(&mut db, n_players, 1);
        let policy: Rc<dyn Policy> = Rc::new(TwoMulligans {
            inner: DefaultPolicy::new(),
            calls: RefCell::new(IndexMap::new()),
        });
        let seats: Vec<PlayerId> = (0..n_players).map(PlayerId).collect();
        for &p in &seats {
            game.set_policy(p, Rc::clone(&policy));
            // re-run the mulligan phase alone on fresh libraries
            let hand = std::mem::take(&mut game.player_mut(p).hand);
            let library = std::mem::take(&mut game.player_mut(p).library);
            game.player_mut(p).library = hand.into_iter().chain(library).collect();
        }
        game.rng = PyRandom::new(2);
        mulligans(&mut game, &seats);
        for p in &game.players {
            assert_eq!(p.stats.get("mulligans"), Some(&2), "{}", p.name);
            assert_eq!(p.hand.len(), hand_size, "{}", p.name);
        }
    }
}

#[test]
fn starting_player_is_recorded() {
    let mut db = db();
    let mut rng = PyRandom::new(3);
    let options = MatchOptions {
        turn_cap: 1,
        ..MatchOptions::default()
    };
    let rec = run_game(&decks(2), &mut db, &mut rng, &options, None, None);
    let Value::Dict(fields) = &rec else {
        panic!("a record dict");
    };
    let get = |key: &str| {
        fields
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.clone())
    };
    assert!(
        matches!(get("starting_player"), Some(Value::Str(s)) if s == "Seat 0" || s == "Seat 1")
    );
    assert_eq!(get("turns"), Some(Value::from(1_i64)));
}

#[test]
fn rule_103_8a_starting_player_skips_the_first_draw() {
    let mut db = db();
    for seed in 0..6 {
        let mut game = setup(&mut db, 2, seed);
        let active = game.active_player();
        let hand_before = game.player(active).hand.len();
        game.take_turn();
        // a land drop is the only hand change of an all-land hand
        assert_eq!(
            game.player(active).hand.len(),
            hand_before - 1,
            "seed {seed}"
        );
    }
}

#[test]
fn starting_player_option_overrides_the_random_choice() {
    // MatchOptions.starting_player overrides the random choice (103.1)
    let mut db = db();
    let decks: Vec<Deck> = (0..2)
        .map(|i| Deck {
            name: format!("S{i}"),
            path: format!("s{i}.txt"),
            cards: vec!["Swamp".to_owned(); 10],
            commander: Some("Sol Ring".to_owned()),
            fmt: String::new(),
            skipped: Vec::new(),
        })
        .collect();
    for start in 0..2 {
        let options = MatchOptions {
            starting_player: Some(start),
            ..MatchOptions::default()
        };
        let game = setup_game(&decks, &mut db, PyRandom::new(3), &options, None);
        assert_eq!(game.active_idx, start);
    }
}
