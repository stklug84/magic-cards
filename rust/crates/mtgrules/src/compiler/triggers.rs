//! `When/Whenever/At ..., <effect>` oracle lines.

use std::rc::Rc;
use std::sync::LazyLock;

use regex::Regex;

use super::clauses::parse_effect_text;
use super::{re, short_name};
use crate::Game;
use crate::abilities::{TriggerSpec, TriggeredAbility};
use crate::events::{Event, EventType};
use crate::objects::ObjId;
use crate::protocols::TriggerCondition;

/// One row of the trigger table: how the head of the line is matched,
/// and the trigger it yields.
enum Head {
    /// `^when(?:ever)? <subject> <verb>` where the subject is one of the
    /// listed phrases or the card's short name
    Named {
        subjects: &'static [&'static str],
        verb: &'static str,
    },
    /// a fixed pattern anchored at the start of the head
    Fixed(Regex),
}

fn fixed(pattern: &str) -> Head {
    Head::Fixed(re(pattern))
}

struct Row {
    head: Head,
    make: fn() -> TriggerSpec,
}

static WHEN_RE: LazyLock<Regex> = LazyLock::new(|| re(r"^when(?:ever)? "));

impl Head {
    fn matches(&self, head: &str, short: &str) -> bool {
        match self {
            Head::Fixed(pattern) => pattern.is_match(head),
            Head::Named { subjects, verb } => {
                let Some(m) = WHEN_RE.find(head) else {
                    return false;
                };
                let rest = &head[m.end()..];
                subjects
                    .iter()
                    .copied()
                    .chain(std::iter::once(short))
                    .any(|subject| {
                        rest.strip_prefix(subject)
                            .is_some_and(|after| after.starts_with(verb))
                    })
            }
        }
    }
}

const ENTERS_SUBJECTS: &[&str] = &[
    "this creature",
    "this permanent",
    "this artifact",
    "this enchantment",
    "this land",
    "~",
];
const DIES_SUBJECTS: &[&str] = &["this creature", "this permanent", "~"];
const ATTACKS_SUBJECTS: &[&str] = &["this creature", "~"];

static TRIGGER_TABLE: LazyLock<Vec<Row>> = LazyLock::new(|| {
    vec![
        Row {
            head: Head::Named {
                subjects: ENTERS_SUBJECTS,
                verb: " enters",
            },
            make: || TriggerSpec::on(EventType::EntersBattlefield),
        },
        Row {
            head: Head::Named {
                subjects: DIES_SUBJECTS,
                verb: " dies",
            },
            make: || TriggerSpec::on(EventType::Dies),
        },
        Row {
            head: Head::Named {
                subjects: ATTACKS_SUBJECTS,
                verb: " attacks",
            },
            make: || TriggerSpec::on(EventType::Attacks),
        },
        Row {
            head: fixed(r"^at the beginning of your upkeep"),
            make: || TriggerSpec::when(EventType::BeginStep, step("upkeep", true)),
        },
        Row {
            head: fixed(r"^at the beginning of each (?:player's )?upkeep"),
            make: || TriggerSpec::when(EventType::BeginStep, step("upkeep", false)),
        },
        Row {
            head: fixed(r"^at the beginning of your end step"),
            make: || TriggerSpec::when(EventType::BeginStep, step("end", true)),
        },
        Row {
            head: fixed(r"^at the beginning of combat on your turn"),
            make: || TriggerSpec::when(EventType::BeginStep, step("combat_begin", true)),
        },
        Row {
            head: fixed(r"^when(?:ever)? another creature you control dies"),
            make: || TriggerSpec::when(EventType::Dies, dies(true, true, false)),
        },
        Row {
            head: fixed(r"^when(?:ever)? another creature dies"),
            make: || TriggerSpec::when(EventType::Dies, dies(false, true, false)),
        },
        Row {
            head: fixed(r"^when(?:ever)? a creature you control dies"),
            make: || TriggerSpec::when(EventType::Dies, dies(true, false, false)),
        },
        Row {
            head: fixed(r"^when(?:ever)? a creature dies"),
            make: || TriggerSpec::when(EventType::Dies, dies(false, false, false)),
        },
        Row {
            head: fixed(r"^when(?:ever)? a creature an opponent controls dies"),
            make: || TriggerSpec::when(EventType::Dies, dies(false, false, true)),
        },
        Row {
            head: fixed(
                r"^when(?:ever)? a creature an opponent controls with (?:a|one or more) -1/-1 counters? on it dies",
            ),
            make: || TriggerSpec::when(EventType::Dies, dies_with_counter(false, true, "-1/-1")),
        },
        Row {
            head: fixed(
                r"^when(?:ever)? a creature an opponent controls with (?:a|one or more) counters? on it dies",
            ),
            make: || TriggerSpec::when(EventType::Dies, dies_with_counter(false, true, "")),
        },
        Row {
            head: fixed(
                r"^when(?:ever)? a creature you control with (?:a|one or more) counters? on it dies",
            ),
            make: || TriggerSpec::when(EventType::Dies, dies_with_counter(true, false, "")),
        },
        Row {
            head: fixed(
                r"^when(?:ever)? a creature with (?:a|one or more) -1/-1 counters? on it dies",
            ),
            make: || TriggerSpec::when(EventType::Dies, dies_with_counter(false, false, "-1/-1")),
        },
        Row {
            head: fixed(
                r"^landfall - when(?:ever)? a land you control enters|^when(?:ever)? a land you control enters",
            ),
            make: || TriggerSpec::when(EventType::LandPlayed, own_event()),
        },
        Row {
            head: fixed(r"^when(?:ever)? you create (?:a|one or more) tokens?"),
            make: || TriggerSpec::when(EventType::EntersBattlefield, token_etb()),
        },
    ]
});

/// Condition: a `BeginStep` event for `step` (optionally our own).
fn step(step: &'static str, mine: bool) -> TriggerCondition {
    Rc::new(move |game: &Game, source: ObjId, event: &Event| {
        if event.data.step.as_deref() != Some(step) {
            return false;
        }
        !mine || event.data.player == Some(game.obj(source).controller)
    })
}

/// Condition: a creature died (optionally ours / another's / theirs).
fn dies(own: bool, other: bool, opponent: bool) -> TriggerCondition {
    Rc::new(move |game: &Game, source: ObjId, event: &Event| {
        let Some(obj) = event.data.obj else {
            return false;
        };
        if !game.obj(obj).base.types.contains("Creature") {
            return false;
        }
        if other && obj == source {
            return false;
        }
        let mine = game.obj(obj).controller == game.obj(source).controller;
        if own && !mine {
            return false;
        }
        !(opponent && mine)
    })
}

/// Condition: a countered creature died (rule 603.10a look-back).
///
/// Checks the dead creature's last-known counters; `lki_counters` is
/// captured on leaving the battlefield.
fn dies_with_counter(own: bool, opponent: bool, kind: &'static str) -> TriggerCondition {
    Rc::new(move |game: &Game, source: ObjId, event: &Event| {
        let Some(obj) = event.data.obj else {
            return false;
        };
        let o = game.obj(obj);
        if !o.base.types.contains("Creature") {
            return false;
        }
        let mine = o.controller == game.obj(source).controller;
        if own && !mine {
            return false;
        }
        if opponent && mine {
            return false;
        }
        if kind.is_empty() {
            o.lki_counters.values().any(|n| *n > 0)
        } else {
            o.lki_counters.get(kind).copied().unwrap_or(0) > 0
        }
    })
}

/// Condition: the event's player is the ability's controller.
fn own_event() -> TriggerCondition {
    Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.player == Some(game.obj(source).controller)
    })
}

/// Condition: one of the controller's tokens entered.
fn token_etb() -> TriggerCondition {
    Rc::new(|game: &Game, source: ObjId, event: &Event| {
        event.data.obj.is_some_and(|obj| {
            let o = game.obj(obj);
            o.is_token && o.controller == game.obj(source).controller
        })
    })
}

static ONCE_RE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)do this only once each turn\.?\s*$"));

/// Parse one `When/Whenever/At ..., <effect>` oracle line.
#[must_use]
pub fn parse_trigger_line(line: &str, name: &str) -> Option<TriggeredAbility> {
    let low = line.to_lowercase();
    let short = short_name(name);
    let comma = low.find(", ")?;
    let (head, body) = (&low[..comma], &line[comma + 2..]);
    let once = ONCE_RE.is_match(body);
    let row = TRIGGER_TABLE
        .iter()
        .find(|row| row.head.matches(head, &short))?;
    let (effect, targets) = parse_effect_text(body, name);
    let mut ability = TriggeredAbility::new((row.make)(), effect);
    ability.targets = targets;
    line.clone_into(&mut ability.text);
    ability.optional = body.to_lowercase().starts_with("you may");
    ability.once_each_turn = once;
    Some(ability)
}
