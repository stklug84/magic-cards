//! The view state over a record stream, event formatting and the plain
//! text frame (the toolkit-free half of the Python `tui.py`).
//!
//! The board is always the latest snapshot at or before the cursor;
//! events form a scrolling log.

use mtgcards::jsonval::py_str;
use pycompat::json::Value;
use pycompat::pyrepr::repr;

use crate::schema::{Record, field, int, text, truthy};

/// Cursor over a list of schema records (events + snapshots).
#[derive(Debug)]
pub struct ViewState {
    /// the records (shared, appendable)
    pub records: Vec<Record>,
    /// the game-header record
    pub meta: Record,
    /// index of the last visible record (-1 before the first)
    pub cursor: i64,
    /// `{"t": "end", ...}` once known
    pub result: Option<Record>,
}

/// Whether a record is of type `t`.
fn is_type(rec: &Value, t: &str) -> bool {
    text(rec, "t") == t
}

/// The event kind of a record (`""` for snapshots).
fn kind(rec: &Value) -> &str {
    text(rec, "kind")
}

impl Default for ViewState {
    fn default() -> Self {
        ViewState::new(Vec::new(), Value::Dict(Vec::new()))
    }
}

impl ViewState {
    /// Wrap `records` and the game-header `meta`.
    #[must_use]
    pub fn new(records: Vec<Record>, meta: Record) -> Self {
        ViewState {
            records,
            meta,
            cursor: -1,
            result: None,
        }
    }

    /// Add a live record; the end record is kept aside as the result.
    pub fn append(&mut self, rec: Record) {
        if is_type(&rec, "end") {
            self.result = Some(rec);
        } else {
            self.records.push(rec);
        }
    }

    fn len(&self) -> i64 {
        i64::try_from(self.records.len()).unwrap_or(i64::MAX)
    }

    fn record(&self, i: i64) -> Option<&Record> {
        usize::try_from(i).ok().and_then(|i| self.records.get(i))
    }

    // ---- queries

    /// Latest snapshot at or before the cursor.
    #[must_use]
    pub fn snapshot(&self) -> Option<&Record> {
        let mut i = self.cursor.min(self.len() - 1);
        while i >= 0 {
            if let Some(rec) = self.record(i)
                && is_type(rec, "s")
            {
                return Some(rec);
            }
            i -= 1;
        }
        None
    }

    /// Collect the last `n` events up to the cursor (per player, opt.).
    #[must_use]
    pub fn visible_events(&self, n: usize, player: Option<&str>) -> Vec<&Record> {
        let mut out = Vec::new();
        let mut i = self.cursor.min(self.len() - 1);
        while i >= 0 {
            if let Some(rec) = self.record(i)
                && is_type(rec, "e")
                && player.is_none_or(|p| event_players(rec).contains(&p))
            {
                out.push(rec);
                if out.len() >= n {
                    break;
                }
            }
            i -= 1;
        }
        out.reverse();
        out
    }

    /// Whether the cursor is at (or past) the last record.
    #[must_use]
    pub fn at_end(&self) -> bool {
        self.cursor >= self.len() - 1
    }

    // ---- navigation

    /// Move to the next/previous *event* record.
    pub fn step(&mut self, direction: i64) -> bool {
        let len = self.len();
        let mut i = self.cursor + direction;
        while 0 <= i && i < len {
            let is_event = self.record(i).is_some_and(|r| is_type(r, "e"));
            if is_event || (direction > 0 && i == len - 1) {
                self.cursor = i;
                return true;
            }
            i += direction;
        }
        self.cursor = (self.cursor + direction).clamp(-1, len - 1);
        false
    }

    /// Advance until an event satisfying `matches` (e.g. the next phase).
    pub fn seek_event(&mut self, direction: i64, matches: &dyn Fn(&Record) -> bool) -> bool {
        let len = self.len();
        let mut i = self.cursor + direction;
        while 0 <= i && i < len {
            if let Some(rec) = self.record(i)
                && is_type(rec, "e")
                && matches(rec)
            {
                self.cursor = i;
                return true;
            }
            i += direction;
        }
        self.cursor = if direction > 0 { len - 1 } else { -1 };
        false
    }

    /// Seek to the next/previous phase boundary.
    pub fn next_phase(&mut self, direction: i64) -> bool {
        self.seek_event(direction, &|r| kind(r) == "phase")
    }

    /// Seek to the untap step of the next/previous turn.
    pub fn next_turn(&mut self, direction: i64) -> bool {
        let here = self.turn_at(self.cursor);
        self.seek_event(direction, &|r| {
            kind(r) == "phase"
                && field(r, "data").is_some_and(|d| text(d, "phase") == "untap")
                && if direction > 0 {
                    int(r, "turn") > here
                } else {
                    int(r, "turn") < here
                }
        })
    }

    /// Jump to the first phase event of `turn` (from the start).
    pub fn jump_turn(&mut self, turn: i64) {
        self.cursor = -1;
        self.seek_event(1, &|r| kind(r) == "phase" && int(r, "turn") >= turn);
    }

    fn turn_at(&self, i: i64) -> i64 {
        self.record(i).map_or(0, |r| int(r, "turn"))
    }
}

/// The player names an event mentions (`who`, `player`, `target`).
fn event_players(rec: &Record) -> Vec<&str> {
    let Some(Value::Dict(data)) = field(rec, "data") else {
        return Vec::new();
    };
    data.iter()
        .filter(|(k, _)| ["who", "player", "target"].contains(&k.as_str()))
        .filter_map(|(_, v)| match v {
            Value::Str(s) => Some(s.as_str()),
            _ => None,
        })
        .collect()
}

// ---------------------------------------------------------------- formatting

/// Event kind -> rich style name (kept for the frame's colours).
pub const EVENT_STYLE: &[(&str, &str)] = &[
    ("phase", "dim"),
    ("turn", "bold"),
    ("draw", "dim"),
    ("cast", "cyan"),
    ("resolve", "blue"),
    ("trigger", "dim cyan"),
    ("activate", "cyan"),
    ("land", "green4"),
    ("token", "green"),
    ("attack", "yellow"),
    ("block", "gold3"),
    ("damage", "red"),
    ("life", "white"),
    ("dies", "red3"),
    ("counter", "magenta"),
    ("fizzle", "magenta"),
    ("player_loses", "bold red"),
];

/// The Python `str()` of a data field (`None` when absent).
fn data_str(d: &Value, key: &str) -> String {
    field(d, key).map_or_else(|| "None".to_owned(), py_str)
}

/// The Python `repr()` of the event data dict (the fallback line).
fn data_repr(d: &Value) -> String {
    repr(d)
}

/// Render one engine event record as a log line.
#[must_use]
pub fn format_event(rec: &Record) -> String {
    let empty = Value::Dict(Vec::new());
    let d = field(rec, "data").unwrap_or(&empty);
    let get = |key: &str| data_str(d, key);
    match kind(rec) {
        "phase" => format!(
            "-- T{} {} ({}) --",
            int(rec, "turn"),
            get("phase"),
            get("who")
        ),
        "turn" => format!("== Turn {}: {} ==", get("n"), get("who")),
        "cast" => {
            let extra = if truthy(d, "x") {
                format!(" (X={})", get("x"))
            } else {
                String::new()
            };
            let cmd = if truthy(d, "commander") {
                " [commander]"
            } else {
                ""
            };
            format!("{} casts {}{extra}{cmd}", get("who"), get("card"))
        }
        "resolve" => format!("resolves: {}", get("what")),
        "trigger" => format!("trigger: {} ({})", get("what"), get("who")),
        "activate" => format!("{} activates {}", get("who"), get("source")),
        "land" => {
            let t = if truthy(d, "tapped") { " (tapped)" } else { "" };
            format!("{} plays {}{t}", get("who"), get("card"))
        }
        "token" => {
            let pt = if truthy(d, "pt") {
                format!(" {}", get("pt"))
            } else {
                String::new()
            };
            format!("{} creates {}{pt} token", get("who"), get("name"))
        }
        "attack" => format!("{} attacks {}", get("card"), get("target")),
        "block" => format!("{} blocks {}", get("blocker"), get("attacker")),
        "damage" => {
            let c = if truthy(d, "combat") { " (combat)" } else { "" };
            format!("{} deals {} to {}{c}", get("src"), get("n"), get("target"))
        }
        "life" => format!(
            "{} {:+} life -> {}",
            get("who"),
            int(d, "delta"),
            get("total")
        ),
        "dies" => {
            let t = if truthy(d, "token") { " token" } else { "" };
            format!("{}{t} dies ({})", get("card"), get("who"))
        }
        "counter" => format!("{} ({}) is countered", get("spell"), get("who")),
        "fizzle" => format!("fizzles: {}", get("what")),
        "draw" => format!("{} draws", get("who")),
        "player_loses" => format!("{} LOSES: {}", get("who"), get("why")),
        other => format!("{other} {}", data_repr(d)),
    }
}

/// Battlefield display filters, cycled with the `c` key.
pub const BF_FILTERS: &[&str] = &["all", "creatures", "nonland"];

/// Whether a permanent passes the battlefield filter.
#[must_use]
pub fn bf_visible(p: &Record, bf_filter: &str) -> bool {
    let types: Vec<&str> = match field(p, "types") {
        Some(Value::List(items)) => items
            .iter()
            .filter_map(|v| match v {
                Value::Str(s) => Some(s.as_str()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    };
    match bf_filter {
        "creatures" => types.contains(&"Creature"),
        "nonland" => !types.contains(&"Land"),
        _ => true,
    }
}

/// Group identical battlefield entries: `3x Insect 1/1`.
#[must_use]
pub fn group_battlefield(perms: &[Record]) -> Vec<(usize, &Record)> {
    let key_of = |p: &Record| -> String {
        // the Python grouping key, as one string
        let mut counters: Vec<String> = match field(p, "counters") {
            Some(Value::Dict(items)) => items
                .iter()
                .map(|(k, v)| format!("{k}={}", py_str(v)))
                .collect(),
            _ => Vec::new(),
        };
        counters.sort();
        let blocking: Vec<String> = match field(p, "blocking") {
            Some(Value::List(items)) => items.iter().map(py_str).collect(),
            _ => Vec::new(),
        };
        format!(
            "{}|{}|{}|{}|{}|{}|{}|{}|{}",
            text(p, "name"),
            field(p, "pt").map_or_else(|| "None".to_owned(), py_str),
            truthy(p, "tapped"),
            counters.join(","),
            field(p, "attacking").map_or_else(|| "None".to_owned(), py_str),
            blocking.join(","),
            truthy(p, "token"),
            truthy(p, "commander"),
            int(p, "damage"),
        )
    };
    let mut groups: Vec<(String, usize, &Record)> = Vec::new();
    for p in perms {
        let key = key_of(p);
        match groups.iter_mut().find(|(k, _, _)| *k == key) {
            Some((_, n, _)) => *n += 1,
            None => groups.push((key, 1, p)),
        }
    }
    groups.into_iter().map(|(_, n, p)| (n, p)).collect()
}

/// The battlefield list of a player record.
#[must_use]
pub fn battlefield(pd: &Record) -> &[Record] {
    match field(pd, "battlefield") {
        Some(Value::List(items)) => items,
        _ => &[],
    }
}

/// The player records of a snapshot.
#[must_use]
pub fn players(snap: &Record) -> &[Record] {
    match field(snap, "players") {
        Some(Value::List(items)) => items,
        _ => &[],
    }
}

/// The stack entries of a snapshot.
#[must_use]
pub fn stack(snap: &Record) -> Vec<&str> {
    match field(snap, "stack") {
        Some(Value::List(items)) => items
            .iter()
            .filter_map(|v| match v {
                Value::Str(s) => Some(s.as_str()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// One-frame text dump (the fallback when there is no terminal UI): the
/// lines `print_plain_frame` prints.
#[must_use]
pub fn plain_frame_lines(view: &ViewState) -> Vec<String> {
    let Some(snap) = view.snapshot() else {
        return Vec::new();
    };
    let mut lines = vec![format!(
        "=== Turn {} {} (active: {}) ===",
        int(snap, "turn"),
        text(snap, "phase"),
        text(snap, "active")
    )];
    for pd in players(snap) {
        let lost = if truthy(pd, "lost") {
            format!("  LOST: {}", text(pd, "lost"))
        } else {
            String::new()
        };
        lines.push(format!(
            "  {}: {} life, hand {}, lib {}, gy {}{lost}",
            text(pd, "name"),
            int(pd, "life"),
            int(pd, "hand"),
            int(pd, "library"),
            int(pd, "graveyard")
        ));
        for (count, p) in group_battlefield(battlefield(pd)) {
            let n = if count > 1 {
                format!("{count}x ")
            } else {
                String::new()
            };
            let pt = if truthy(p, "pt") {
                format!(" {}", text(p, "pt"))
            } else {
                String::new()
            };
            let tap = if truthy(p, "tapped") { " (T)" } else { "" };
            lines.push(format!("      {n}{}{pt}{tap}", text(p, "name")));
        }
    }
    let items = stack(snap);
    if !items.is_empty() {
        lines.push(format!("  stack: {}", items.join(" <- ")));
    }
    lines
}
