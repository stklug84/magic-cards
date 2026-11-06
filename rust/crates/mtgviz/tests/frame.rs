//! The terminal frame rendered headlessly (ratatui's `TestBackend`) and
//! frozen as insta snapshots: a synthetic board that exercises every
//! panel detail (counters, tokens, a commander, attackers and blockers,
//! poison, energy, a floating pool, commander damage, a lost player,
//! the stack), the pod layout with its truncation, the filters, the
//! end-of-game header, and the plain text frame of the same board.

use mtgviz::replay::{ReplaySettings, status_line};
use mtgviz::schema::Record;
use mtgviz::tui::render_frame;
use mtgviz::view::{ViewState, plain_frame_lines};
use pycompat::json::Value;
use ratatui::Terminal;
use ratatui::backend::TestBackend;

fn s(v: &str) -> Value {
    Value::from(v)
}

fn perm(name: &str, types: &[&str], tapped: bool, extra: Vec<(&str, Value)>) -> Value {
    let mut fields = vec![
        ("name", s(name)),
        ("types", Value::List(types.iter().map(|t| s(t)).collect())),
        ("tapped", Value::Bool(tapped)),
    ];
    fields.extend(extra);
    Value::dict(fields)
}

fn creature(name: &str, pt: &str, tapped: bool, extra: Vec<(&str, Value)>) -> Value {
    let mut fields = vec![("pt", s(pt))];
    fields.extend(extra);
    perm(name, &["Creature"], tapped, fields)
}

fn player(name: &str, life: i64, battlefield: Vec<Value>, extra: Vec<(&str, Value)>) -> Value {
    let mut fields = vec![
        ("name", s(name)),
        ("life", Value::from(life)),
        ("lost", s("")),
        ("hand", Value::from(5_i64)),
        ("library", Value::from(60_i64)),
        ("graveyard", Value::from(3_i64)),
        ("exile", Value::from(0_i64)),
        ("energy", Value::from(0_i64)),
        ("poison", Value::from(0_i64)),
        ("mana_pool", s("")),
        ("commander_in_command", Value::Bool(false)),
        ("cmd_damage", Value::Dict(Vec::new())),
    ];
    for (k, v) in extra {
        if let Some(slot) = fields.iter_mut().find(|(key, _)| *key == k) {
            slot.1 = v;
        } else {
            fields.push((k, v));
        }
    }
    fields.push(("battlefield", Value::List(battlefield)));
    Value::dict(fields)
}

fn snapshot(
    seq: i64,
    turn: i64,
    phase: &str,
    active: &str,
    stack: Vec<&str>,
    players: Vec<Value>,
) -> Record {
    Value::dict([
        ("t", s("s")),
        ("seq", Value::from(seq)),
        ("turn", Value::from(turn)),
        ("phase", s(phase)),
        ("active", s(active)),
        ("stack", Value::List(stack.into_iter().map(s).collect())),
        ("players", Value::List(players)),
    ])
}

fn event(seq: i64, turn: i64, kind: &str, data: Vec<(&str, Value)>) -> Record {
    Value::dict([
        ("t", s("e")),
        ("seq", Value::from(seq)),
        ("turn", Value::from(turn)),
        ("phase", s("combat")),
        ("kind", s(kind)),
        ("data", Value::dict(data)),
    ])
}

/// A two-player board in the middle of combat.
#[expect(
    clippy::too_many_lines,
    reason = "one literal board with every panel detail"
)]
fn duel() -> Vec<Record> {
    let alice = player(
        "Alice",
        34,
        vec![
            creature(
                "Hapatra, Vizier of Poisons",
                "2/2",
                false,
                vec![
                    ("commander", Value::Bool(true)),
                    ("attacking", s("Bob")),
                    ("counters", Value::dict([("+1/+1", Value::from(2_i64))])),
                ],
            ),
            creature(
                "Snake",
                "1/1",
                false,
                vec![("token", Value::Bool(true)), ("attacking", s("Bob"))],
            ),
            creature(
                "Snake",
                "1/1",
                false,
                vec![("token", Value::Bool(true)), ("attacking", s("Bob"))],
            ),
            creature("Snake", "1/1", true, vec![("token", Value::Bool(true))]),
            perm("Sol Ring", &["Artifact"], true, vec![]),
            perm("Swamp", &["Land"], true, vec![]),
            perm("Swamp", &["Land"], true, vec![]),
            perm("Forest", &["Land"], false, vec![]),
        ],
        vec![
            ("mana_pool", s("BG")),
            ("energy", Value::from(2_i64)),
            (
                "cmd_damage",
                Value::dict([("Pia Nalaar, Chief Mechanic", Value::from(6_i64))]),
            ),
        ],
    );
    let bob = player(
        "Bob",
        11,
        vec![
            creature(
                "Wall of Omens",
                "0/4",
                false,
                vec![
                    (
                        "blocking",
                        Value::List(vec![s("Hapatra, Vizier of Poisons")]),
                    ),
                    ("damage", Value::from(1_i64)),
                ],
            ),
            creature("Thopter", "1/1", false, vec![("token", Value::Bool(true))]),
            perm("Island", &["Land"], false, vec![]),
            perm("Mountain", &["Land"], true, vec![]),
        ],
        vec![
            ("poison", Value::from(3_i64)),
            ("commander_in_command", Value::Bool(true)),
        ],
    );
    vec![
        event(
            1,
            1,
            "turn",
            vec![("n", Value::from(1_i64)), ("who", s("Alice"))],
        ),
        snapshot(
            2,
            1,
            "main1",
            "Alice",
            vec![],
            vec![alice.clone(), bob.clone()],
        ),
        event(
            3,
            4,
            "phase",
            vec![
                ("phase", s("combat")),
                ("turn", Value::from(4_i64)),
                ("who", s("Alice")),
            ],
        ),
        event(
            4,
            4,
            "attack",
            vec![
                ("who", s("Alice")),
                ("card", s("Hapatra, Vizier of Poisons")),
                ("target", s("Bob")),
            ],
        ),
        event(
            5,
            4,
            "block",
            vec![
                ("who", s("Bob")),
                ("blocker", s("Wall of Omens")),
                ("attacker", s("Hapatra, Vizier of Poisons")),
            ],
        ),
        event(
            6,
            4,
            "damage",
            vec![
                ("src", s("Snake")),
                ("target", s("Bob")),
                ("n", Value::from(1_i64)),
                ("combat", Value::Bool(true)),
            ],
        ),
        event(
            7,
            4,
            "life",
            vec![
                ("who", s("Bob")),
                ("delta", Value::from(-1_i64)),
                ("total", Value::from(11_i64)),
            ],
        ),
        event(
            8,
            4,
            "poison",
            vec![
                ("who", s("Bob")),
                ("n", Value::from(1_i64)),
                ("total", Value::from(3_i64)),
            ],
        ),
        snapshot(
            9,
            4,
            "combat",
            "Alice",
            vec!["Counterspell (Bob)", "Swords to Plowshares (Alice)"],
            vec![alice, bob],
        ),
    ]
}

fn render(
    view: &ViewState,
    status: &str,
    log_filter: Option<&str>,
    bf_filter: &str,
    width: u16,
    height: u16,
) -> String {
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("a test terminal");
    terminal
        .draw(|frame| render_frame(frame, view, status, log_filter, bf_filter))
        .expect("a frame");
    terminal.backend().to_string()
}

#[test]
fn duel_frame_in_combat() {
    let mut view = ViewState::new(
        duel(),
        Value::dict([("seed", Value::from(7_i64)), ("game", Value::from(2_i64))]),
    );
    view.cursor = 8;
    let status = status_line(&view, &ReplaySettings::default());
    insta::assert_snapshot!(render(&view, &status, None, "all", 120, 42));
}

#[test]
fn duel_frame_filtered_and_finished() {
    let mut view = ViewState::new(duel(), Value::dict([("seed", Value::from(7_i64))]));
    view.cursor = 8;
    view.result = Some(Value::dict([
        ("t", s("end")),
        ("winner", s("Alice")),
        ("reason", s("poison")),
        ("turns", Value::from(4_i64)),
    ]));
    let st = ReplaySettings {
        autoplay: true,
        speed: 2.0,
        log_filter: Some("Bob".to_owned()),
        bf_filter: "creatures".to_owned(),
        ..ReplaySettings::default()
    };
    let status = status_line(&view, &st);
    insta::assert_snapshot!(render(
        &view,
        &status,
        st.log_filter.as_deref(),
        &st.bf_filter,
        120,
        42
    ));
}

#[test]
fn waiting_frame_before_the_first_snapshot() {
    let view = ViewState::new(
        vec![event(
            1,
            1,
            "turn",
            vec![("n", Value::from(1_i64)), ("who", s("Alice"))],
        )],
        Value::Null,
    );
    insta::assert_snapshot!(render(&view, " paused ", None, "all", 60, 8));
}

/// A four-player board where one seat has more rows than fit.
fn pod() -> Vec<Record> {
    let busy: Vec<Value> = (0..12)
        .map(|i| {
            creature(
                &format!("Construct {i}"),
                &format!("{}/{}", i + 1, i + 1),
                i % 3 == 0,
                vec![],
            )
        })
        .chain([
            perm("Plains", &["Land"], false, vec![]),
            perm("Plains", &["Land"], true, vec![]),
        ])
        .collect();
    let players = vec![
        player("North", 40, busy, vec![]),
        player(
            "East",
            0,
            vec![],
            vec![("lost", s("life 0 or less (704.5a)"))],
        ),
        player(
            "South",
            25,
            vec![creature("Bear", "2/2", false, vec![])],
            vec![],
        ),
        player(
            "West",
            38,
            vec![perm("Swamp", &["Land"], false, vec![])],
            vec![],
        ),
    ];
    vec![
        event(
            1,
            9,
            "player_loses",
            vec![("who", s("East")), ("why", s("life 0 or less (704.5a)"))],
        ),
        snapshot(2, 9, "main2", "South", vec![], players),
    ]
}

#[test]
fn pod_frame_truncates_the_busiest_board() {
    let mut view = ViewState::new(pod(), Value::dict([("seed", Value::from(1_i64))]));
    view.cursor = 1;
    insta::assert_snapshot!(render(&view, " live x1 |", None, "nonland", 120, 40));
}

#[test]
fn plain_frame_of_the_duel() {
    let mut view = ViewState::new(duel(), Value::Null);
    view.cursor = 8;
    insta::assert_snapshot!(plain_frame_lines(&view).join("\n"));
    let mut pod_view = ViewState::new(pod(), Value::Null);
    pod_view.cursor = 1;
    insta::assert_snapshot!(plain_frame_lines(&pod_view).join("\n"));
}
