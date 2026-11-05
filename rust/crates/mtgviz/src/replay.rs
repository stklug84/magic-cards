//! Load recorded viz JSONL files and replay them (the Python
//! `replay.py`).
//!
//! [`replay_file`] is the `--replay` entry point; [`run_replay`] is the
//! interactive keyboard-driven app on top of the ratatui frame, with
//! [`plain_replay`] as the line-based fallback when stdin is not a
//! terminal.

use std::io::{BufRead, Write};
use std::path::Path;

use indexmap::IndexMap;
use mtgcards::jsonval::py_str;
use pycompat::json::Value;

use crate::schema::{HIGHLIGHTS, Record, field, int, text};
use crate::view::{BF_FILTERS, ViewState, format_event, plain_frame_lines};

/// One recorded game: its header, records and footer.
#[derive(Debug)]
pub struct GameStream {
    /// the `{"t": "game", ...}` header
    pub meta: Record,
    /// the events and snapshots
    pub records: Vec<Record>,
    /// the `{"t": "end", ...}` footer, once seen
    pub result: Option<Record>,
}

impl Default for GameStream {
    fn default() -> Self {
        GameStream {
            meta: Value::Dict(Vec::new()),
            records: Vec::new(),
            result: None,
        }
    }
}

/// Split a `--viz-file` JSONL into per-game streams, by game number.
///
/// # Errors
///
/// `cannot open replay file: <OSError>` when the file cannot be read.
pub fn load_games(path: &Path) -> Result<IndexMap<i64, GameStream>, String> {
    let file = std::fs::File::open(path).map_err(|e| {
        format!(
            "cannot open replay file: {}",
            pycompat::pyio::os_error(&e, path)
        )
    })?;
    let mut games: IndexMap<i64, GameStream> = IndexMap::new();
    let mut current: Option<i64> = None;
    for raw in std::io::BufReader::new(file).lines() {
        let raw = raw.map_err(|e| {
            format!(
                "cannot open replay file: {}",
                pycompat::pyio::os_error(&e, path)
            )
        })?;
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        let parsed: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| format!("{}: invalid JSON record: {e}", path.display()))?;
        let rec = mtgcards::jsonval::from_serde(&parsed);
        match text(&rec, "t") {
            "game" => {
                let number = int(&rec, "game");
                games.insert(
                    number,
                    GameStream {
                        meta: rec,
                        records: Vec::new(),
                        result: None,
                    },
                );
                current = Some(number);
            }
            _ if current.is_none() => {}
            "end" => {
                if let Some(game) = current.and_then(|n| games.get_mut(&n)) {
                    game.result = Some(rec);
                }
            }
            _ => {
                if let Some(game) = current.and_then(|n| games.get_mut(&n)) {
                    game.records.push(rec);
                }
            }
        }
    }
    Ok(games)
}

/// Replay one recorded game from `path` (the `--replay` entry point).
///
/// # Errors
///
/// No recorded games, or no game `game`, with the Python messages.
pub fn replay_file(path: &Path, game: i64) -> Result<(), String> {
    let mut games = load_games(path)?;
    if games.is_empty() {
        return Err(format!(
            "{}: no recorded games found (record with --viz-file)",
            path.display()
        ));
    }
    let Some(stream) = games.shift_remove(&game) else {
        let mut available: Vec<i64> = games.keys().copied().collect();
        available.sort_unstable();
        let listed: Vec<String> = available.iter().map(ToString::to_string).collect();
        return Err(format!(
            "{}: no game {game}; available: {}",
            path.display(),
            listed.join(", ")
        ));
    };
    run_replay(stream);
    Ok(())
}

/// Fallback viewer: prints one phase per `<Enter>`, `a` for all, `q` to
/// quit. `input` supplies the answers (stdin), `out` takes the lines.
pub fn plain_replay(stream: GameStream, input: &mut dyn BufRead, out: &mut dyn Write) {
    let GameStream {
        meta,
        records,
        result,
    } = stream;
    let n_records = records.len();
    let mut view = ViewState::new(records, meta);
    let game_no = field(&view.meta, "game").map_or_else(|| "1".to_owned(), py_str);
    let seed = field(&view.meta, "seed").map_or_else(|| "None".to_owned(), py_str);
    let _ = writeln!(
        out,
        "replaying game {game_no} (seed {seed}), {n_records} records; <Enter> for next phase, 'a<Enter>' for all, 'q<Enter>' to quit"
    );
    let mut auto = false;
    while !view.at_end() {
        let start = view.cursor;
        view.next_phase(1);
        for i in (start + 1)..=view.cursor {
            if let Some(rec) = usize::try_from(i).ok().and_then(|i| view.records.get(i))
                && text(rec, "t") == "e"
                && text(rec, "kind") != "phase"
            {
                let _ = writeln!(out, "    {}", format_event(rec));
            }
        }
        for line in plain_frame_lines(&view) {
            let _ = writeln!(out, "{line}");
        }
        if auto {
            continue;
        }
        let _ = write!(out, "> ");
        let _ = out.flush();
        let mut answer = String::new();
        match input.read_line(&mut answer) {
            Ok(0) | Err(_) => return, // EOF
            Ok(_) => {}
        }
        match answer.trim().to_lowercase().as_str() {
            "q" => return,
            "a" => auto = true,
            _ => {}
        }
    }
    if let Some(result) = result {
        let _ = writeln!(
            out,
            "=== winner: {} ({}) after {} turns ===",
            text(&result, "winner"),
            text(&result, "reason"),
            int(&result, "turns")
        );
    }
}

/// The replay app's key help.
pub const REPLAY_HELP: &str = " space:event  n/N:phase  t/T:turn  arrows:event/turn  g<turn>:jump  a:auto  +/-:speed  h:pause-on-highlight  f:log-filter  c:battlefield  q:quit ";

const MAX_SPEED: f64 = 8.0;
const MIN_SPEED: f64 = 0.25;

/// Mutable replay-app mode switches, adjusted by keypresses.
#[derive(Debug, Clone, PartialEq)]
pub struct ReplaySettings {
    /// advancing on a timer
    pub autoplay: bool,
    /// the autoplay speed factor
    pub speed: f64,
    /// stop autoplay on highlight events
    pub pause_hl: bool,
    /// the log filter (a player name)
    pub log_filter: Option<String>,
    /// the battlefield filter (`BF_FILTERS`)
    pub bf_filter: String,
    /// digits typed after `g`; `None` when inactive
    pub goto: Option<String>,
}

impl Default for ReplaySettings {
    fn default() -> Self {
        ReplaySettings {
            autoplay: false,
            speed: 1.0,
            pause_hl: true,
            log_filter: None,
            bf_filter: "all".to_owned(),
            goto: None,
        }
    }
}

/// A keypress, as the Python `keys.read_key` names it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Key {
    /// a character
    Char(char),
    /// the Enter key
    Enter,
    /// the Escape key
    Esc,
    /// the left arrow
    Left,
    /// the right arrow
    Right,
    /// the up arrow
    Up,
    /// the down arrow
    Down,
    /// Ctrl-C
    Interrupt,
}

/// The status line of the replay app.
#[must_use]
pub fn status_line(view: &ViewState, st: &ReplaySettings) -> String {
    let mode = if st.autoplay {
        format!("auto x{}", pycompat::pyfloat::general(st.speed, 6))
    } else {
        "paused".to_owned()
    };
    let pos = format!("{}/{}", view.cursor.max(0), view.records.len());
    let g = st
        .goto
        .as_ref()
        .map_or_else(String::new, |g| format!("  goto: {g}_"));
    format!(" {mode}  {pos}{g} |{REPLAY_HELP}")
}

/// Advance one event; stop at the end or on highlight events.
pub fn autoplay_tick(view: &mut ViewState, st: &mut ReplaySettings) {
    let moved = view.step(1);
    let at_highlight = usize::try_from(view.cursor)
        .ok()
        .and_then(|i| view.records.get(i))
        .is_some_and(|r| text(r, "t") == "e" && HIGHLIGHTS.contains(&text(r, "kind")));
    if (!moved && view.at_end()) || (st.pause_hl && at_highlight) {
        st.autoplay = false;
    }
}

/// Collect digits after `g`; `<Enter>` jumps, anything else cancels.
pub fn goto_key(key: &Key, view: &mut ViewState, st: &mut ReplaySettings) {
    if let Key::Char(c) = key
        && c.is_ascii_digit()
    {
        st.goto.get_or_insert_with(String::new).push(*c);
        return;
    }
    if *key == Key::Enter
        && let Some(digits) = &st.goto
        && let Ok(turn) = digits.parse::<i64>()
    {
        view.jump_turn(turn);
    }
    st.goto = None;
}

/// Cycle `current` through `options` (the Python `opts[(i + 1) % n]`).
fn cycle<'a>(options: &[&'a str], current: &str) -> &'a str {
    let i = options.iter().position(|o| *o == current).unwrap_or(0);
    options[(i + 1) % options.len()]
}

/// Mode toggles: autoplay, speed, pauses, filters, goto, end.
pub fn mode_key(key: &Key, view: &mut ViewState, st: &mut ReplaySettings, player_names: &[String]) {
    match key {
        Key::Char('a') => st.autoplay = !st.autoplay,
        Key::Char('+') => st.speed = MAX_SPEED.min(st.speed * 2.0),
        Key::Char('-') => st.speed = MIN_SPEED.max(st.speed / 2.0),
        Key::Char('h') => st.pause_hl = !st.pause_hl,
        Key::Char('f') => {
            let mut options: Vec<Option<&str>> = vec![None];
            options.extend(player_names.iter().map(|n| Some(n.as_str())));
            let i = options
                .iter()
                .position(|o| *o == st.log_filter.as_deref())
                .unwrap_or(0);
            st.log_filter = options[(i + 1) % options.len()].map(str::to_owned);
        }
        Key::Char('c') => st.bf_filter = cycle(BF_FILTERS, &st.bf_filter).to_owned(),
        Key::Char('g') => st.goto = Some(String::new()),
        Key::Char('G') => view.cursor = i64::try_from(view.records.len()).unwrap_or(i64::MAX) - 1,
        _ => {}
    }
}

/// Apply one keypress; `false` when the app should quit.
pub fn replay_key(
    key: &Key,
    view: &mut ViewState,
    st: &mut ReplaySettings,
    player_names: &[String],
) -> bool {
    match key {
        Key::Char('q') | Key::Interrupt => return false,
        Key::Char(' ') | Key::Right => {
            view.step(1);
        }
        Key::Left | Key::Char('b') => {
            view.step(-1);
        }
        Key::Char('n') => {
            view.next_phase(1);
        }
        Key::Char('N') => {
            view.next_phase(-1);
        }
        Key::Char('t') | Key::Down => {
            view.next_turn(1);
        }
        Key::Char('T') | Key::Up => {
            view.next_turn(-1);
        }
        other => mode_key(other, view, st, player_names),
    }
    true
}

/// Interactive replay of one recorded game (the plain fallback when
/// stdin is not a terminal).
pub fn run_replay(stream: GameStream) {
    if !crate::keys::stdin_is_terminal() {
        let stdin = std::io::stdin();
        let mut input = stdin.lock();
        let mut out = std::io::stdout();
        plain_replay(stream, &mut input, &mut out);
        return;
    }
    crate::tui::replay_app(stream);
}
