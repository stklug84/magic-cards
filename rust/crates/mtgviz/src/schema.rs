//! JSON-serializable visualization records (the Python `schema.py`).
//!
//! Three record types flow from the [`Recorder`](crate::recorder::Recorder)
//! to a sink (a JSONL file or an in-memory queue):
//!
//! ```text
//! {"t": "game", "game": N, "seed": S}                       game header
//! {"t": "e", "seq", "turn", "phase", "kind", "data"}         engine log event
//! {"t": "s", "seq", "turn", "phase", "active", "stack",
//!  "players": [...]}                                          full snapshot
//! {"t": "end", "winner", "reason", "turns"}                   game footer
//! ```
//!
//! Snapshots are complete board states; the TUI renders the latest
//! snapshot at or before the cursor and shows events as a scrolling log.
//! Records are [`Value`]s with the Python dict's key order, so
//! `json::dumps` writes the same bytes as the Python recorder.

use mtgrules::Game;
use mtgrules::objects::{Defender, ObjId, PlayerId};
use mtgrules::protocols::{LogFields, LogValue};
use mtgrules::stack::{StackItem, StackObject};
use pycompat::json::Value;

/// One recorder record (see the shapes above).
pub type Record = Value;

/// Event kinds worth pausing on during autoplay.
pub const HIGHLIGHTS: &[&str] = &["counter", "player_loses", "eliminated"];

/// A log value as a JSON value.
fn log_value(v: &LogValue) -> Value {
    match v {
        LogValue::Null => Value::Null,
        LogValue::Bool(b) => Value::Bool(*b),
        LogValue::Int(n) => Value::from(*n),
        LogValue::Str(s) => Value::from(s.as_str()),
    }
}

/// The value of a record field, if any.
#[must_use]
pub fn field<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    match record {
        Value::Dict(fields) => fields.iter().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

/// A record field as text (`""` when absent or not a string).
#[must_use]
pub fn text<'a>(record: &'a Value, key: &str) -> &'a str {
    match field(record, key) {
        Some(Value::Str(s)) => s,
        _ => "",
    }
}

/// A record field as an integer (`0` when absent or not a number).
#[must_use]
pub fn int(record: &Value, key: &str) -> i64 {
    match field(record, key) {
        Some(Value::Int(n)) => i64::try_from(*n).unwrap_or(i64::MAX),
        _ => 0,
    }
}

/// Whether a record field is truthy (Python `d.get(key)` in a boolean
/// context).
#[must_use]
pub fn truthy(record: &Value, key: &str) -> bool {
    field(record, key).is_some_and(|v| match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Int(n) => *n != 0,
        Value::Float(f) => *f != 0.0,
        Value::Str(s) => !s.is_empty(),
        Value::List(items) => !items.is_empty(),
        Value::Dict(fields) => !fields.is_empty(),
    })
}

/// One battlefield permanent as a display record.
fn perm(game: &Game, obj: ObjId) -> Value {
    let ch = game.chars(obj);
    let o = game.obj(obj);
    let mut d: Vec<(&str, Value)> = vec![
        (
            "name",
            Value::from(if ch.name.is_empty() {
                "(unnamed)"
            } else {
                ch.name.as_str()
            }),
        ),
        (
            "types",
            Value::List(ch.types.iter().map(|t| Value::from(t.as_str())).collect()),
        ),
        ("tapped", Value::Bool(o.tapped)),
    ];
    if o.is_token {
        d.push(("token", Value::Bool(true)));
    }
    if o.commander {
        d.push(("commander", Value::Bool(true)));
    }
    if ch.types.contains("Creature") {
        d.push((
            "pt",
            Value::from(format!(
                "{}/{}",
                ch.power.unwrap_or(0),
                ch.toughness.unwrap_or(0)
            )),
        ));
        if o.damage != 0 {
            d.push(("damage", Value::from(o.damage)));
        }
    }
    if !o.counters.is_empty() {
        d.push((
            "counters",
            Value::Dict(
                o.counters
                    .iter()
                    .filter(|(_, n)| **n != 0)
                    .map(|(k, n)| (k.clone(), Value::from(*n)))
                    .collect(),
            ),
        ));
    }
    if let Some(target) = o.attacking {
        // attack target is a player or a planeswalker (CR 508.1)
        let name = match target {
            Defender::Player(p) => game.player(p).name.clone(),
            Defender::Object(t) => game.obj(t).base.name.clone(),
        };
        d.push(("attacking", Value::from(name)));
    }
    if !o.blocking.is_empty() {
        d.push((
            "blocking",
            Value::List(
                o.blocking
                    .iter()
                    .map(|b| Value::from(game.obj(*b).base.name.as_str()))
                    .collect(),
            ),
        ));
    }
    Value::dict(d)
}

/// Display line for one stack item.
fn stack_entry(game: &Game, item: &StackItem) -> String {
    let name = match &item.obj {
        StackObject::Spell(obj) if item.is_spell => game.obj(*obj).base.name.clone(),
        _ => format!("ability of {}", game.obj(item.source).base.name),
    };
    format!("{name} ({})", game.player(item.controller).name)
}

/// One player's complete snapshot record.
fn player_state(game: &Game, p: PlayerId, cmd_names: &[(ObjId, String)]) -> Value {
    let player = game.player(p);
    let mut pool_entries: Vec<(&String, &i64)> = player.mana_pool.mana.iter().collect();
    pool_entries.sort();
    let pool: String = pool_entries
        .iter()
        .filter(|(_, n)| **n > 0)
        .map(|(sym, n)| sym.repeat(usize::try_from(**n).unwrap_or(0)))
        .collect();
    let commander_in_command = player
        .commander_obj
        .is_some_and(|cmd| game.obj(cmd).zone == mtgrules::objects::Zone::Command);
    let cmd_damage: Vec<(String, Value)> = player
        .commander_damage
        .iter()
        .filter(|(_, n)| **n != 0)
        .map(|(src, n)| {
            let name = cmd_names
                .iter()
                .find(|(id, _)| id == src)
                .map_or_else(|| src.0.to_string(), |(_, name)| name.clone());
            (name, Value::from(*n))
        })
        .collect();
    Value::dict([
        ("name", Value::from(player.name.as_str())),
        ("life", Value::from(player.life)),
        ("lost", Value::from(player.lose_reason.as_str())),
        ("hand", Value::from(player.hand.len())),
        ("library", Value::from(player.library.len())),
        ("graveyard", Value::from(player.graveyard.len())),
        ("exile", Value::from(player.exile.len())),
        ("energy", Value::from(player.energy)),
        ("poison", Value::from(player.poison)),
        ("mana_pool", Value::from(pool)),
        ("commander_in_command", Value::Bool(commander_in_command)),
        ("cmd_damage", Value::Dict(cmd_damage)),
        (
            "battlefield",
            Value::List(player.battlefield.iter().map(|o| perm(game, *o)).collect()),
        ),
    ])
}

/// Full board state of a game.
#[must_use]
pub fn snapshot(game: &Game, seq: u64) -> Value {
    let cmd_names: Vec<(ObjId, String)> = game
        .players
        .iter()
        .filter_map(|p| {
            p.commander_obj
                .map(|cmd| (cmd, game.obj(cmd).base.name.clone()))
        })
        .collect();
    Value::dict([
        ("t", Value::from("s")),
        ("seq", Value::from(i64::try_from(seq).unwrap_or(i64::MAX))),
        ("turn", Value::from(i64::from(game.turn))),
        ("phase", Value::from(game.phase.as_str())),
        (
            "active",
            Value::from(game.player(game.active_player()).name.as_str()),
        ),
        (
            "stack",
            Value::List(
                game.stack
                    .iter()
                    .map(|i| Value::from(stack_entry(game, i)))
                    .collect(),
            ),
        ),
        (
            "players",
            Value::List(
                (0..game.players.len())
                    .map(|i| player_state(game, PlayerId(i), &cmd_names))
                    .collect(),
            ),
        ),
    ])
}

/// One engine log event as a display record.
#[must_use]
pub fn event(game: &Game, seq: u64, kind: &str, data: &LogFields) -> Value {
    Value::dict([
        ("t", Value::from("e")),
        ("seq", Value::from(i64::try_from(seq).unwrap_or(i64::MAX))),
        ("turn", Value::from(i64::from(game.turn))),
        ("phase", Value::from(game.phase.as_str())),
        ("kind", Value::from(kind)),
        (
            "data",
            Value::Dict(
                data.iter()
                    .map(|(k, v)| ((*k).to_owned(), log_value(v)))
                    .collect(),
            ),
        ),
    ])
}

/// The game-header record of a `--viz-file`.
#[must_use]
pub fn game_header(game_no: u64, seed: i64) -> Value {
    Value::dict([
        ("t", Value::from("game")),
        (
            "game",
            Value::from(i64::try_from(game_no).unwrap_or(i64::MAX)),
        ),
        ("seed", Value::from(seed)),
    ])
}

/// The game-footer record.
#[must_use]
pub fn game_end(winner: Option<&str>, reason: &str, turns: u32) -> Value {
    Value::dict([
        ("t", Value::from("end")),
        ("winner", Value::from(winner.unwrap_or("draw"))),
        ("reason", Value::from(reason)),
        ("turns", Value::from(i64::from(turns))),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn record_accessors_tolerate_every_shape() {
        let rec = Value::dict([
            ("s", Value::from("x")),
            ("n", Value::from(3_i64)),
            ("f", Value::Float(0.0)),
            ("b", Value::Bool(false)),
            ("l", Value::List(vec![Value::Null])),
            ("d", Value::Dict(Vec::new())),
            ("z", Value::Null),
        ]);
        assert_eq!(text(&rec, "s"), "x");
        assert_eq!(text(&rec, "n"), "");
        assert_eq!(int(&rec, "n"), 3);
        assert_eq!(int(&rec, "s"), 0);
        assert!(truthy(&rec, "s") && truthy(&rec, "n") && truthy(&rec, "l"));
        assert!(
            !truthy(&rec, "f") && !truthy(&rec, "b") && !truthy(&rec, "d") && !truthy(&rec, "z")
        );
        assert!(!truthy(&rec, "missing") && field(&rec, "missing").is_none());
        assert!(field(&Value::Null, "s").is_none());
        assert_eq!(text(&Value::from(1_i64), "s"), "");
    }

    #[test]
    fn header_and_footer_records() {
        assert_eq!(
            pycompat::json::dumps(&game_header(2, 7)),
            "{\"t\": \"game\", \"game\": 2, \"seed\": 7}"
        );
        assert_eq!(
            pycompat::json::dumps(&game_end(None, "draw", 40)),
            "{\"t\": \"end\", \"winner\": \"draw\", \"reason\": \"draw\", \"turns\": 40}"
        );
        assert_eq!(text(&game_end(Some("Ann"), "life", 9), "winner"), "Ann");
    }
}
