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

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(seq: i64, turn: i64, kind: &str, data: Vec<(&str, Value)>) -> Record {
        Value::dict([
            ("t", Value::from("e")),
            ("seq", Value::from(seq)),
            ("turn", Value::from(turn)),
            ("phase", Value::from("main1")),
            ("kind", Value::from(kind)),
            ("data", Value::dict(data)),
        ])
    }

    fn snap(seq: i64, turn: i64) -> Record {
        Value::dict([
            ("t", Value::from("s")),
            ("seq", Value::from(seq)),
            ("turn", Value::from(turn)),
            ("phase", Value::from("main1")),
            ("active", Value::from("p0")),
            ("stack", Value::List(Vec::new())),
            ("players", Value::List(Vec::new())),
        ])
    }

    /// Three turns of phases with a counter (a highlight) in turn 2.
    fn stream() -> Vec<Record> {
        let mut out = vec![snap(1, 0)];
        let mut seq = 2;
        for turn in 1..=3 {
            for phase in ["untap", "main1", "combat", "end"] {
                out.push(ev(
                    seq,
                    turn,
                    "phase",
                    vec![("phase", Value::from(phase)), ("who", Value::from("p0"))],
                ));
                seq += 1;
                out.push(snap(seq, turn));
                seq += 1;
            }
            if turn == 2 {
                out.push(ev(
                    seq,
                    turn,
                    "counter",
                    vec![("spell", Value::from("Bolt")), ("who", Value::from("p1"))],
                ));
                seq += 1;
            }
        }
        out
    }

    fn view() -> ViewState {
        ViewState::new(
            stream(),
            Value::dict([("game", Value::from(1_i64)), ("seed", Value::from(5_i64))]),
        )
    }

    #[test]
    fn status_line_shows_mode_position_and_goto() {
        let mut view = view();
        let mut st = ReplaySettings::default();
        assert!(status_line(&view, &st).starts_with(" paused  0/"));
        view.step(1);
        st.autoplay = true;
        st.speed = 0.5;
        assert!(status_line(&view, &st).starts_with(" auto x0.5  1/"));
        st.goto = Some("12".to_owned());
        assert!(status_line(&view, &st).contains("  goto: 12_ |"));
    }

    #[test]
    fn autoplay_stops_at_highlights_and_at_the_end() {
        let mut view = view();
        let mut st = ReplaySettings {
            autoplay: true,
            ..ReplaySettings::default()
        };
        while st.autoplay {
            autoplay_tick(&mut view, &mut st);
        }
        let at = &view.records[usize::try_from(view.cursor).unwrap()];
        assert_eq!(text(at, "kind"), "counter");
        // without the highlight pause autoplay runs to the end
        st.autoplay = true;
        st.pause_hl = false;
        while st.autoplay {
            autoplay_tick(&mut view, &mut st);
        }
        assert!(view.at_end());
    }

    #[test]
    fn goto_collects_digits_and_jumps_on_enter() {
        let mut view = view();
        let mut st = ReplaySettings::default();
        mode_key(&Key::Char('g'), &mut view, &mut st, &[]);
        assert_eq!(st.goto.as_deref(), Some(""));
        goto_key(&Key::Char('3'), &mut view, &mut st);
        assert_eq!(st.goto.as_deref(), Some("3"));
        goto_key(&Key::Enter, &mut view, &mut st);
        assert!(st.goto.is_none());
        assert_eq!(
            int(&view.records[usize::try_from(view.cursor).unwrap()], "turn"),
            3
        );
        // anything else cancels
        st.goto = Some("2".to_owned());
        goto_key(&Key::Esc, &mut view, &mut st);
        assert!(st.goto.is_none());
    }

    #[test]
    fn mode_keys_toggle_the_settings() {
        let mut view = view();
        let mut st = ReplaySettings::default();
        let names = vec!["p0".to_owned(), "p1".to_owned()];
        mode_key(&Key::Char('a'), &mut view, &mut st, &names);
        assert!(st.autoplay);
        for _ in 0..6 {
            mode_key(&Key::Char('+'), &mut view, &mut st, &names);
        }
        assert_eq!(st.speed, 8.0);
        for _ in 0..8 {
            mode_key(&Key::Char('-'), &mut view, &mut st, &names);
        }
        assert_eq!(st.speed, 0.25);
        mode_key(&Key::Char('h'), &mut view, &mut st, &names);
        assert!(!st.pause_hl);
        mode_key(&Key::Char('f'), &mut view, &mut st, &names);
        assert_eq!(st.log_filter.as_deref(), Some("p0"));
        mode_key(&Key::Char('f'), &mut view, &mut st, &names);
        mode_key(&Key::Char('f'), &mut view, &mut st, &names);
        assert_eq!(st.log_filter, None);
        mode_key(&Key::Char('c'), &mut view, &mut st, &names);
        assert_eq!(st.bf_filter, "creatures");
        mode_key(&Key::Char('c'), &mut view, &mut st, &names);
        mode_key(&Key::Char('c'), &mut view, &mut st, &names);
        assert_eq!(st.bf_filter, "all");
        mode_key(&Key::Char('G'), &mut view, &mut st, &names);
        assert!(view.at_end());
        mode_key(&Key::Char('?'), &mut view, &mut st, &names); // unknown: no change
    }

    #[test]
    fn navigation_keys_move_the_cursor_and_q_quits() {
        let mut view = view();
        let mut st = ReplaySettings::default();
        assert!(replay_key(&Key::Char(' '), &mut view, &mut st, &[]));
        assert_eq!(view.cursor, 1);
        assert!(replay_key(&Key::Right, &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Left, &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Char('b'), &mut view, &mut st, &[]));
        assert_eq!(view.cursor, 0); // no event before the first: parked on the snapshot
        assert!(replay_key(&Key::Char('n'), &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Char('t'), &mut view, &mut st, &[]));
        assert_eq!(
            int(&view.records[usize::try_from(view.cursor).unwrap()], "turn"),
            2
        );
        assert!(replay_key(&Key::Down, &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Up, &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Char('T'), &mut view, &mut st, &[]));
        assert!(replay_key(&Key::Char('N'), &mut view, &mut st, &[]));
        assert!(!replay_key(&Key::Char('q'), &mut view, &mut st, &[]));
        assert!(!replay_key(&Key::Interrupt, &mut view, &mut st, &[]));
    }

    #[test]
    fn plain_replay_steps_phases_and_quits() {
        let stream = GameStream {
            meta: Value::dict([("game", Value::from(1_i64)), ("seed", Value::from(5_i64))]),
            records: stream(),
            result: Some(Value::dict([
                ("t", Value::from("end")),
                ("winner", Value::from("p0")),
                ("reason", Value::from("life")),
                ("turns", Value::from(3_i64)),
            ])),
        };
        let n = stream.records.len();
        let mut input = std::io::Cursor::new(b"\nq\n".to_vec());
        let mut out = Vec::new();
        plain_replay(stream, &mut input, &mut out);
        let shown = String::from_utf8(out).unwrap();
        assert!(
            shown.starts_with(&format!("replaying game 1 (seed 5), {n} records;")),
            "{shown}"
        );
        assert_eq!(shown.matches("=== Turn").count(), 2); // two phases shown, then q
        assert!(!shown.contains("=== winner")); // quit before the end
        // EOF on stdin ends the viewer as well
        let stream = GameStream {
            records: stream_records_only(),
            ..GameStream::default()
        };
        let mut input = std::io::Cursor::new(Vec::new());
        let mut out = Vec::new();
        plain_replay(stream, &mut input, &mut out);
        assert!(
            String::from_utf8(out)
                .unwrap()
                .starts_with("replaying game 1 (seed None), ")
        );
    }

    fn stream_records_only() -> Vec<Record> {
        stream()
    }

    #[test]
    fn load_games_skips_records_before_the_first_header() {
        let tmp = tempfile::tempdir().unwrap();
        let path = tmp.path().join("x.jsonl");
        std::fs::write(
            &path,
            "{\"t\": \"e\", \"seq\": 1}\n\n{\"t\": \"game\", \"game\": 3, \"seed\": 9}\n{\"t\": \"s\", \"seq\": 2}\n{\"t\": \"end\", \"winner\": \"a\"}\n",
        )
        .unwrap();
        let games = load_games(&path).unwrap();
        assert_eq!(games.keys().copied().collect::<Vec<_>>(), [3]);
        assert_eq!(games[&3].records.len(), 1);
        assert_eq!(text(games[&3].result.as_ref().unwrap(), "winner"), "a");
        std::fs::write(&path, "not json\n").unwrap();
        assert!(
            load_games(&path)
                .unwrap_err()
                .contains("invalid JSON record")
        );
    }
}
