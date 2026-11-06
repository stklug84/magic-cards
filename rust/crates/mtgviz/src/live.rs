//! `--watch`: run one rules-engine game and render it live (the Python
//! `live.py`).
//!
//! The Python version runs the engine on a worker thread paced by a
//! throttle. The Rust engine is single-threaded, so the recorder sink
//! renders the frame, handles the keys and dwells after each event
//! instead: the game unfolds at watchable speed, the UI stays
//! responsive between events, and `q` lets the game finish unpaced
//! before the epilogue.

use std::cell::RefCell;
use std::io::Write as _;
use std::rc::Rc;
use std::time::Duration;

use mtgcards::{CardDatabase, Deck};
use mtgrules::adapter::{MatchOptions, run_game};
use mtgrules::policy::PolicyProfile;
use pycompat::json::Value;
use pycompat::random::PyRandom;

use crate::keys::{read_key, stdin_is_terminal};
use crate::recorder::{Recorder, VizWriter};
use crate::replay::Key;
use crate::schema::{HIGHLIGHTS, Record, field, int, text};
use crate::tui::{Screen, render_frame};
use crate::view::{BF_FILTERS, ViewState, format_event};

/// The live view's key help.
pub const LIVE_HELP: &str =
    " p/space:pause-resume  +/-:speed  h:pause-on-highlight  c:battlefield  q:quit ";

/// Extra dwell time (seconds, at speed 1.0) after notable events.
const DWELL: &[(&str, f64)] = &[
    ("phase", 0.5),
    ("cast", 0.6),
    ("resolve", 0.6),
    ("attack", 0.5),
    ("block", 0.5),
    ("counter", 1.0),
    ("player_loses", 1.5),
    ("dies", 0.4),
    ("damage", 0.3),
    ("life", 0.2),
    ("token", 0.2),
];
const DEFAULT_DWELL: f64 = 0.08;
const MAX_SPEED: f64 = 8.0;
const MIN_SPEED: f64 = 0.25;

/// Optional `watch_game` settings.
#[derive(Clone, Debug)]
pub struct WatchOptions {
    /// the turn cap
    pub turn_cap: u32,
    /// per-seat AI profiles
    pub profiles: Vec<Option<PolicyProfile>>,
    /// also record the game to this `--viz-file`
    pub viz_path: Option<std::path::PathBuf>,
}

impl Default for WatchOptions {
    fn default() -> Self {
        WatchOptions {
            turn_cap: 40,
            profiles: Vec::new(),
            viz_path: None,
        }
    }
}

/// Mutable live-view mode switches, adjusted by keypresses.
struct LiveState {
    view: ViewState,
    paused: bool,
    speed: f64,
    pause_hl: bool,
    bf_filter: String,
    quit: bool,
    screen: Option<Screen>,
}

impl LiveState {
    fn status(&self) -> String {
        let mode = if self.paused {
            "PAUSED".to_owned()
        } else {
            format!("live x{}", pycompat::pyfloat::general(self.speed, 6))
        };
        format!(" {mode} |{LIVE_HELP}")
    }

    fn draw(&mut self, status: &str) {
        let Some(screen) = self.screen.as_mut() else {
            return;
        };
        let view = &self.view;
        let bf_filter = self.bf_filter.clone();
        if screen
            .terminal
            .draw(|frame| render_frame(frame, view, status, None, &bf_filter))
            .is_err()
        {
            self.quit = true;
        }
    }

    /// Mode toggles: pause/resume, speed, highlight pause, filter.
    fn key(&mut self, key: &Key) {
        match key {
            Key::Char('q') | Key::Interrupt => self.quit = true,
            Key::Char('p' | ' ') => self.paused = !self.paused,
            Key::Char('+') => self.speed = MAX_SPEED.min(self.speed * 2.0),
            Key::Char('-') => self.speed = MIN_SPEED.max(self.speed / 2.0),
            Key::Char('h') => self.pause_hl = !self.pause_hl,
            Key::Char('c') => {
                let i = BF_FILTERS
                    .iter()
                    .position(|f| *f == self.bf_filter)
                    .unwrap_or(0);
                BF_FILTERS[(i + 1) % BF_FILTERS.len()].clone_into(&mut self.bf_filter);
            }
            _ => {}
        }
    }

    /// Auto-pause when the newest event is a highlight.
    fn maybe_pause_on_highlight(&mut self) {
        if !self.pause_hl || self.paused {
            return;
        }
        let tail = self.view.visible_events(1, None);
        if tail
            .last()
            .is_some_and(|r| HIGHLIGHTS.contains(&text(r, "kind")))
        {
            self.paused = true;
        }
    }

    /// After an event: render, dwell (handling keys), block while paused.
    fn pace(&mut self, kind: &str) {
        if self.quit {
            return;
        }
        self.view.cursor = i64::try_from(self.view.records.len()).unwrap_or(i64::MAX) - 1;
        self.maybe_pause_on_highlight();
        let dwell = DWELL
            .iter()
            .find(|(k, _)| *k == kind)
            .map_or(DEFAULT_DWELL, |(_, d)| *d);
        let mut remaining = Duration::from_secs_f64(dwell / self.speed);
        loop {
            let status = self.status();
            self.draw(&status);
            if self.quit {
                return;
            }
            let slice = if self.paused {
                Duration::from_millis(100)
            } else {
                remaining.min(Duration::from_millis(100))
            };
            let started = std::time::Instant::now();
            if let Some(key) = read_key(Some(slice)) {
                self.key(&key);
                if self.quit {
                    return;
                }
                continue;
            }
            if self.paused {
                continue;
            }
            remaining = remaining.saturating_sub(started.elapsed());
            if remaining.is_zero() {
                return;
            }
        }
    }
}

/// Run one seeded game and render it live; the engine record.
///
/// # Errors
///
/// The `--viz-file` cannot be written.
pub fn watch_game(
    decks: &[Deck],
    db: &mut CardDatabase,
    seed: i64,
    options: &WatchOptions,
) -> Result<Value, String> {
    let writer = match &options.viz_path {
        Some(path) => Some(
            VizWriter::open(path).map_err(|e| pycompat::pyio::os_error(&e, path).to_string())?,
        ),
        None => None,
    };
    let mut file_sink = writer.as_ref().map(|w| w.game_sink(1, seed));
    let interactive = stdin_is_terminal();
    let screen = if interactive {
        Screen::enter().ok()
    } else {
        None
    };
    if screen.is_none() {
        eprintln!("note: plain event stream ('rich' missing or not a TTY)");
    }
    let state = Rc::new(RefCell::new(LiveState {
        view: ViewState::new(
            Vec::new(),
            Value::dict([("seed", Value::from(seed)), ("game", Value::from(1_i64))]),
        ),
        paused: false,
        speed: 1.0,
        pause_hl: true,
        bf_filter: "all".to_owned(),
        quit: false,
        screen,
    }));
    let plain = state.borrow().screen.is_none();
    let sink_state = Rc::clone(&state);
    let sink = Box::new(move |rec: Record| {
        if let Some(file_sink) = file_sink.as_mut() {
            file_sink(rec.clone());
        }
        let is_event = text(&rec, "t") == "e";
        let kind = text(&rec, "kind").to_owned();
        if plain {
            if is_event {
                println!("T{:>2} [{kind}] {}", int(&rec, "turn"), format_event(&rec));
            }
            return;
        }
        let mut st = sink_state.borrow_mut();
        st.view.append(rec);
        if is_event {
            st.pace(&kind);
        }
    });
    let recorder = Recorder::new(sink).shared();
    let mut rng = PyRandom::new(i128::from(seed));
    let match_options = MatchOptions {
        turn_cap: options.turn_cap,
        profiles: options.profiles.clone(),
        starting_player: None,
    };
    let outcome = run_game(decks, db, &mut rng, &match_options, None, Some(&recorder));
    drop(recorder);
    // game over: show the final board until a key is pressed
    {
        let mut st = state.borrow_mut();
        if st.screen.is_some() && !st.quit {
            st.view.cursor = i64::try_from(st.view.records.len()).unwrap_or(i64::MAX) - 1;
            st.draw(" game over - any key to exit ");
            let _ = read_key(None);
        }
        st.screen = None; // restores the terminal
    }
    if let (Some(writer), Some(path)) = (&writer, &options.viz_path) {
        writer
            .close()
            .map_err(|e| pycompat::pyio::os_error(&e, path).to_string())?;
        println!("viz recording written to {}", path.display());
    }
    println!(
        "winner: {} ({}) after {} turns",
        field(&outcome, "winner").map_or_else(|| "None".to_owned(), mtgcards::jsonval::py_str),
        field(&outcome, "reason").map_or_else(|| "None".to_owned(), mtgcards::jsonval::py_str),
        field(&outcome, "turns").map_or_else(|| "None".to_owned(), mtgcards::jsonval::py_str)
    );
    let _ = std::io::stdout().flush();
    Ok(outcome)
}
