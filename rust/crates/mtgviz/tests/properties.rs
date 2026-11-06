//! Property tests of the view state: over random record streams and
//! random key sequences the cursor stays in range, the board shown is
//! always the latest snapshot at or before the cursor, and the
//! formatters never panic.

use mtgviz::replay::{Key, ReplaySettings, autoplay_tick, goto_key, replay_key};
use mtgviz::schema::{Record, text};
use mtgviz::view::{ViewState, format_event, plain_frame_lines};
use proptest::prelude::*;
use pycompat::json::Value;

const KINDS: &[&str] = &[
    "phase",
    "turn",
    "draw",
    "cast",
    "resolve",
    "trigger",
    "activate",
    "land",
    "token",
    "attack",
    "block",
    "damage",
    "life",
    "dies",
    "counter",
    "fizzle",
    "player_loses",
    "cycle",
    "poison",
];

fn data_value() -> impl Strategy<Value = Value> {
    prop_oneof![
        Just(Value::Null),
        any::<bool>().prop_map(Value::Bool),
        (-50i64..50).prop_map(Value::from),
        "[A-Za-z ]{0,12}".prop_map(Value::from),
    ]
}

fn event(turn: i64) -> impl Strategy<Value = Record> {
    (
        prop::sample::select(KINDS),
        prop::collection::btree_map(
            prop::sample::select(vec![
                "who",
                "player",
                "target",
                "card",
                "phase",
                "n",
                "x",
                "commander",
                "tapped",
                "name",
                "pt",
                "src",
                "combat",
                "delta",
                "total",
                "token",
                "spell",
                "what",
                "why",
                "blocker",
                "attacker",
                "source",
            ]),
            data_value(),
            0..5,
        ),
    )
        .prop_map(move |(kind, data)| {
            Value::dict([
                ("t", Value::from("e")),
                ("seq", Value::from(0_i64)),
                ("turn", Value::from(turn)),
                ("phase", Value::from("main1")),
                ("kind", Value::from(kind)),
                ("data", Value::dict(data)),
            ])
        })
}

fn snapshot(turn: i64) -> Record {
    Value::dict([
        ("t", Value::from("s")),
        ("seq", Value::from(0_i64)),
        ("turn", Value::from(turn)),
        ("phase", Value::from("main1")),
        ("active", Value::from("p0")),
        ("stack", Value::List(vec![Value::from("Bolt (p1)")])),
        (
            "players",
            Value::List(vec![Value::dict([
                ("name", Value::from("p0")),
                ("life", Value::from(40_i64)),
                ("lost", Value::from("")),
                ("hand", Value::from(7_i64)),
                ("library", Value::from(90_i64)),
                ("graveyard", Value::from(0_i64)),
                (
                    "battlefield",
                    Value::List(vec![Value::dict([
                        ("name", Value::from("Bear")),
                        ("types", Value::List(vec![Value::from("Creature")])),
                        ("tapped", Value::Bool(false)),
                        ("pt", Value::from("2/2")),
                    ])]),
                ),
            ])]),
        ),
    ])
}

/// A stream of events and snapshots over increasing turns.
fn stream() -> impl Strategy<Value = Vec<Record>> {
    prop::collection::vec((1i64..6, any::<bool>()), 0..30).prop_flat_map(|shape| {
        let mut turn = 0;
        let items: Vec<BoxedStrategy<Record>> = shape
            .into_iter()
            .map(|(advance, is_event)| {
                turn += advance;
                if is_event {
                    event(turn).boxed()
                } else {
                    Just(snapshot(turn)).boxed()
                }
            })
            .collect();
        items
    })
}

fn key() -> impl Strategy<Value = Key> {
    prop_oneof![
        prop::sample::select(vec![
            ' ', 'b', 'n', 'N', 't', 'T', 'a', '+', '-', 'h', 'f', 'c', 'g', 'G', '1', '7', 'x'
        ])
        .prop_map(Key::Char),
        Just(Key::Enter),
        Just(Key::Esc),
        Just(Key::Left),
        Just(Key::Right),
        Just(Key::Up),
        Just(Key::Down),
    ]
}

proptest! {
    #[test]
    fn navigation_keeps_the_cursor_in_range(records in stream(), keys in prop::collection::vec(key(), 0..40)) {
        let n = i64::try_from(records.len()).unwrap();
        let mut view = ViewState::new(records, Value::dict([("seed", Value::from(1_i64))]));
        let mut st = ReplaySettings::default();
        let names = vec!["p0".to_owned()];
        for key in &keys {
            if st.goto.is_some() {
                goto_key(key, &mut view, &mut st);
            } else {
                prop_assert!(replay_key(key, &mut view, &mut st, &names));
            }
            autoplay_tick(&mut view, &mut st);
            prop_assert!((-1..n.max(0)).contains(&view.cursor) || (n == 0 && view.cursor <= 0), "cursor {} of {n}", view.cursor);
            if let Some(snap) = view.snapshot() {
                let at = view.records.iter().position(|r| std::ptr::eq(r, snap)).unwrap();
                prop_assert!(i64::try_from(at).unwrap() <= view.cursor.max(0));
                prop_assert_eq!(text(snap, "t"), "s");
            }
            for rec in view.visible_events(14, st.log_filter.as_deref()) {
                prop_assert!(!format_event(rec).is_empty());
            }
            let _ = plain_frame_lines(&view);
        }
    }
}
