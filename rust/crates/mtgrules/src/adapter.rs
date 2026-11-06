//! Match runner: decklists + knowledge graph -> rules-engine games (the
//! Python `adapter.py`).
//!
//! Bridges the `mtgcards` data layer (`CardDatabase`, deck loading, stats
//! conventions) to the `mtgrules` kernel and reports what the card
//! compiler could not model, so nothing is skipped silently.

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;
use mtgcards::{CardData, CardDatabase, Deck};
use pycompat::json::Value;
use pycompat::random::PyRandom;

use crate::Game;
use crate::compiler;
use crate::objects::{Player, PlayerId, Zone};
use crate::overrides;
use crate::policy::{DefaultPolicy, Policy, PolicyProfile};
use crate::protocols::{LogFields, LogFn, LogValue};
use crate::rule;

/// Rule 103.5: a London mulligan can go down to a zero-card keep at most
/// six times from a seven-card hand.
const MAX_MULLIGANS: usize = 6;
/// Rule 103.5c applies to multiplayer games: more players than this.
const TWO_PLAYER_GAME: usize = 2;
/// The opening hand (rule 103.5).
const HAND_SIZE: usize = 7;

/// Per-match knobs shared by [`setup_game`] and [`run_game`].
#[derive(Clone, Debug)]
pub struct MatchOptions {
    /// the turn cap
    pub turn_cap: u32,
    /// per-seat AI profiles (`None` entries use the default profile)
    pub profiles: Vec<Option<PolicyProfile>>,
    /// seat index of the starting player; `None` = chosen at random
    /// (rule 103.1). Self-play fixes it so its two seatings of a
    /// pairing cancel the play/draw advantage.
    pub starting_player: Option<usize>,
}

impl Default for MatchOptions {
    fn default() -> Self {
        MatchOptions {
            turn_cap: 40,
            profiles: Vec::new(),
            starting_player: None,
        }
    }
}

/// The slice of a viz recorder that [`run_game`] drives: bound after
/// setup, told every log event, finished with the outcome.
pub trait Recorder {
    /// Bind the game before the first turn.
    fn attach(&mut self, game: &Game);
    /// Record one engine log event.
    fn on_event(&mut self, game: &Game, kind: &str, fields: &LogFields);
    /// Emit the end-of-game records.
    fn finish(&mut self, game: &Game, winner: Option<PlayerId>, reason: &str);
}

/// A shared recorder handle (the log fan-out and the finish both reach
/// it).
pub type SharedRecorder = Rc<RefCell<dyn Recorder>>;

/// Compile every deck card into an object owned by `owner`, in deck
/// order; returns the library (top first).
fn build_library(
    game: &mut Game,
    deck: &Deck,
    db: &mut CardDatabase,
    owner: PlayerId,
) -> Vec<crate::objects::ObjId> {
    let mut refs: IndexMap<String, Rc<CardData>> = IndexMap::new();
    let mut objs = Vec::with_capacity(deck.cards.len());
    for name in &deck.cards {
        let card = refs
            .entry(name.clone())
            .or_insert_with(|| Rc::new(db.get(name).clone()));
        let base = compiler::compile_card(card);
        let id = game.create_object(base, owner, false, Some(Rc::clone(card)));
        game.obj_mut(id).zone = Zone::Library;
        objs.push(id);
    }
    objs
}

/// Build a game: starting player, libraries, commanders, mulligans.
///
/// Rule 103.1: the starting player is chosen at random (fix F12); every
/// following step runs in turn order from that player - the shuffles
/// (103.3) and the mulligan rounds (103.5).
#[rule("903.6", "103.1", "103.3", "103.4", "103.5")]
#[must_use]
pub fn setup_game(
    decks: &[Deck],
    db: &mut CardDatabase,
    rng: PyRandom,
    options: &MatchOptions,
    log: Option<LogFn>,
) -> Game {
    let players: Vec<Player> = decks
        .iter()
        .enumerate()
        .map(|(i, deck)| Player::new(PlayerId(i), &deck.name, &deck.name))
        .collect();
    let policies: Vec<Rc<dyn Policy>> = (0..players.len())
        .map(|i| {
            let profile = options
                .profiles
                .get(i)
                .cloned()
                .flatten()
                .unwrap_or_default();
            Rc::new(DefaultPolicy::with_profile(profile)) as Rc<dyn Policy>
        })
        .collect();
    let mut game = Game::with_players(players, rng, &policies, options.turn_cap);
    if let Some(log) = log {
        game.set_log(log);
    }
    let n = game.players.len();
    game.active_idx = match options.starting_player {
        None => usize::try_from(game.rng.randrange(n as u128)).unwrap_or(0), // rule 103.1
        Some(seat) => seat % n,
    };
    let order: Vec<PlayerId> = (0..n)
        .map(|i| PlayerId((game.active_idx + i) % n))
        .collect();

    for (i, deck) in decks.iter().enumerate() {
        let p = PlayerId(i);
        let library = build_library(&mut game, deck, db, p);
        game.player_mut(p).library = library;
        if let Some(commander) = &deck.commander {
            let card = Rc::new(db.get(commander).clone());
            let base = compiler::compile_card(&card);
            let cmd = game.create_object(base, p, false, Some(card));
            game.obj_mut(cmd).commander = true;
            game.place(cmd, p, Zone::Command);
            game.player_mut(p).commander_obj = Some(cmd);
        }
    }
    for &p in &order {
        let mut library = std::mem::take(&mut game.player_mut(p).library);
        game.rng.shuffle(&mut library); // rule 103.3
        game.player_mut(p).library = library;
    }
    mulligans(&mut game, &order);
    game
}

/// London mulligans in rounds, in turn order (rule 103.5).
///
/// Each round every undecided player looks at a fresh seven-card hand
/// and keeps or mulligans; a player who keeps after N counted mulligans
/// puts N cards on the bottom. In a game of three or more players the
/// first mulligan does not count (rule 103.5c, fix F8): the policy never
/// sees it and it costs no card. `stats["mulligans"]` records the number
/// of mulligans taken, free one included.
#[rule("103.5", "103.5c")]
pub fn mulligans(game: &mut Game, order: &[PlayerId]) {
    let multiplayer = game.players.len() > TWO_PLAYER_GAME;
    let mut taken: IndexMap<PlayerId, usize> = order.iter().map(|p| (*p, 0)).collect();
    let mut counted: IndexMap<PlayerId, usize> = order.iter().map(|p| (*p, 0)).collect();
    let mut undecided: Vec<PlayerId> = order.to_vec();
    while !undecided.is_empty() {
        let mut still = Vec::new();
        for &p in &undecided {
            let policy = game.policy(p);
            let hand: Vec<_> = game
                .player(p)
                .library
                .iter()
                .take(HAND_SIZE)
                .copied()
                .collect();
            let mulls = counted[&p];
            if policy.keep_hand(game, p, &hand, mulls) || taken[&p] >= MAX_MULLIGANS {
                let rest: Vec<_> = game
                    .player(p)
                    .library
                    .iter()
                    .skip(HAND_SIZE)
                    .copied()
                    .collect();
                game.player_mut(p).library = rest;
                game.player_mut(p).hand.clone_from(&hand);
                for &c in &hand {
                    game.obj_mut(c).zone = Zone::Hand;
                }
                if mulls > 0 {
                    let bottom = policy.bottom_cards(game, p, &hand, mulls);
                    for &c in bottom.iter().take(mulls) {
                        game.player_mut(p).hand.retain(|h| *h != c);
                        game.obj_mut(c).zone = Zone::Library;
                        game.player_mut(p).library.push(c);
                    }
                }
                continue;
            }
            taken[&p] += 1;
            if !(multiplayer && taken[&p] == 1) {
                counted[&p] += 1; // rule 103.5c: the first one is free
            }
            let mut library = std::mem::take(&mut game.player_mut(p).library);
            game.rng.shuffle(&mut library);
            game.player_mut(p).library = library;
            still.push(p);
        }
        undecided = still;
    }
    for &p in order {
        let n = i64::try_from(taken[&p]).unwrap_or(i64::MAX);
        game.player_mut(p).stats.insert("mulligans".to_owned(), n);
    }
}

/// Raw rules-engine loss reasons (rule citations) -> short report labels.
const REASONS: &[(&str, &str)] = &[
    ("life 0 or less (704.5a)", "life"),
    ("drew from empty library (704.5b)", "decked"),
    ("ten or more poison counters (704.5c)", "poison"),
    ("21+ commander damage (903.10a)", "commander"),
];

fn reason_label(raw: &str, fallback: &str) -> String {
    REASONS
        .iter()
        .find(|(k, _)| *k == raw)
        .map_or_else(|| fallback.to_owned(), |(_, v)| (*v).to_owned())
}

/// Play one game and return an `Aggregator`-compatible record.
///
/// The record is `{winner, turns, reason, starting_player, players:
/// {name: {stats..., life, lost, cards_cast}}}`. `rng` is the caller's
/// stream: the game draws from it and hands it back advanced (the Python
/// `random.Random` is shared by every game of a seed). `log` is an
/// optional `(event, fields)` sink; `recorder` an optional viz recorder
/// (attached after setup, notified of every log event, finished with the
/// outcome).
pub fn run_game(
    decks: &[Deck],
    db: &mut CardDatabase,
    rng: &mut PyRandom,
    options: &MatchOptions,
    log: Option<LogFn>,
    recorder: Option<&SharedRecorder>,
) -> Value {
    let last_loss: Rc<RefCell<Option<String>>> = Rc::new(RefCell::new(None));
    let sinks: Rc<RefCell<Vec<LogFn>>> = Rc::new(RefCell::new(log.into_iter().collect()));
    let fanout: LogFn = {
        let last_loss = Rc::clone(&last_loss);
        let sinks = Rc::clone(&sinks);
        let recorder = recorder.cloned();
        Box::new(move |game: &Game, event: &str, fields: LogFields| {
            if event == "player_loses" {
                let why = fields
                    .iter()
                    .find(|(k, _)| *k == "why")
                    .and_then(|(_, v)| match v {
                        LogValue::Str(s) => Some(s.clone()),
                        _ => None,
                    });
                *last_loss.borrow_mut() = why;
            }
            for sink in sinks.borrow_mut().iter_mut() {
                sink(game, event, fields.clone());
            }
            if let Some(rec) = &recorder {
                rec.borrow_mut().on_event(game, event, &fields);
            }
        })
    };
    let game_rng = std::mem::replace(rng, PyRandom::new(0));
    let mut game = setup_game(decks, db, game_rng, options, Some(fanout));
    let starting_player = game.active_player();
    if let Some(rec) = recorder {
        rec.borrow_mut().attach(&game);
    }
    let n = game.players.len();
    while !game.game_over && game.turn < options.turn_cap {
        game.take_turn();
        if game.game_over {
            break;
        }
        advance_active(&mut game, n);
    }
    let (winner, reason) = decide_outcome(&game, last_loss.borrow().as_deref());
    if let Some(rec) = recorder {
        rec.borrow_mut().finish(&game, winner, &reason);
    }
    let record = game_record(&game, winner, &reason, starting_player);
    *rng = std::mem::replace(&mut game.rng, PyRandom::new(0));
    record
}

/// The `Aggregator` record of a finished game.
fn game_record(
    game: &Game,
    winner: Option<PlayerId>,
    reason: &str,
    starting_player: PlayerId,
) -> Value {
    let players: Vec<(String, Value)> = game
        .players
        .iter()
        .map(|p| {
            let mut fields: Vec<(String, Value)> = p
                .stats
                .iter()
                .map(|(k, v)| (k.clone(), Value::from(*v)))
                .collect();
            fields.push(("life".to_owned(), Value::from(p.life)));
            fields.push(("lost".to_owned(), Value::from(p.lose_reason.as_str())));
            fields.push((
                "cards_cast".to_owned(),
                Value::List(
                    p.cards_cast
                        .iter()
                        .map(|c| Value::from(c.as_str()))
                        .collect(),
                ),
            ));
            (p.name.clone(), Value::Dict(fields))
        })
        .collect();
    Value::dict([
        (
            "winner",
            winner.map_or_else(
                || Value::from("draw"),
                |w| Value::from(game.player(w).name.as_str()),
            ),
        ),
        ("turns", Value::from(i64::from(game.turn))),
        ("reason", Value::from(reason)),
        (
            "starting_player",
            Value::from(game.player(starting_player).name.as_str()),
        ),
        ("players", Value::Dict(players)),
    ])
}

/// Advance the turn marker to the next surviving player.
fn advance_active(game: &mut Game, n: usize) {
    for _ in 0..n {
        game.active_idx = (game.active_idx + 1) % n;
        if !game.players[game.active_idx].lost {
            return;
        }
    }
}

/// Determine `(winner, short reason label)` for the finished game.
#[must_use]
pub fn decide_outcome(game: &Game, last_why: Option<&str>) -> (Option<PlayerId>, String) {
    let why = last_why.unwrap_or("");
    if let Some(winner) = game.winner {
        if game
            .player(winner)
            .stats
            .get("mechanized_wins")
            .copied()
            .unwrap_or(0)
            != 0
        {
            return (Some(winner), "alt_win".to_owned());
        }
        let fallback = if why.is_empty() { "elimination" } else { why };
        return (Some(winner), reason_label(why, fallback));
    }
    let alive = game.alive();
    if alive.len() == 1 {
        return (Some(alive[0]), reason_label(why, "elimination"));
    }
    if !alive.is_empty() {
        // turn-cap tiebreak: the first player with the most life
        let mut best = alive[0];
        for &p in &alive[1..] {
            if game.player(p).life > game.player(best).life {
                best = p;
            }
        }
        return (Some(best), "turn_cap".to_owned());
    }
    (None, "draw".to_owned())
}

/// The lines of the model-coverage report: cards with unmodeled clauses
/// or documented simplifications among the decks' cards.
#[must_use]
pub fn model_coverage_lines(decks: &[Deck]) -> Vec<String> {
    let mut pool: BTreeSet<&str> = BTreeSet::new();
    for d in decks {
        pool.extend(d.cards.iter().map(String::as_str));
        if let Some(c) = &d.commander {
            pool.insert(c.as_str());
        }
    }
    let in_pool = |name: &str| {
        pool.contains(name) || pool.contains(name.split(" // ").next().unwrap_or(name))
    };
    let unknown: Vec<(String, BTreeSet<String>)> = compiler::unknown_clauses()
        .into_iter()
        .filter(|(name, _)| in_pool(name))
        .collect();
    let notes = overrides::notes()
        .into_iter()
        .filter(|(n, _)| in_pool(n))
        .count();
    let mut lines = Vec::new();
    if notes > 0 {
        lines.push(format!("  simplified implementations: {notes}"));
    }
    if !unknown.is_empty() {
        let n_clauses: usize = unknown.iter().map(|(_, c)| c.len()).sum();
        lines.push(format!(
            "  unmodeled oracle clauses: {n_clauses} on {} cards (inert, not silently wrong):",
            unknown.len()
        ));
        for (name, clauses) in &unknown {
            for c in clauses {
                let shown: String = c.chars().take(90).collect();
                lines.push(format!("    - {name}: {shown}"));
            }
        }
    }
    lines
}

/// Report cards with unmodeled clauses or documented simplifications.
pub fn report_model_coverage(decks: &[Deck]) {
    for line in model_coverage_lines(decks) {
        println!("{line}");
    }
}
