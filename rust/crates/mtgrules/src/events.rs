//! Game events.
//!
//! Events are the unit that replacement effects (rule 614) intercept and
//! that triggered abilities (rule 603) watch. Every game-state mutation
//! of interest flows through `Game::emit`, which runs the replacement
//! machinery and then notifies trigger watchers.
//!
//! The Python event carries a heterogeneous `data` dict keyed by role
//! name (`obj`, `player`, `amount`, ...); [`EventData`] is that dict as a
//! struct of optionals, one field per role the engine ever writes.

use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;

use crate::abilities::TokenSpec;
use crate::objects::{Defender, ObjId, PlayerId, Zone};
use crate::replacements::ReplacementId;
use crate::stack::Target;

/// Event-type tags (the values of the Python `Event.type`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum EventType {
    /// obj, from, to (rule 400)
    ZoneChange,
    /// obj (rule 603.6a)
    EntersBattlefield,
    /// obj (rule 700.4)
    Dies,
    /// spec, count, controller (rule 111.2)
    CreateToken,
    /// obj, kind, count (rule 122)
    PutCounters,
    /// player (rule 121)
    Draw,
    /// source, target, amount (rule 120)
    Damage,
    /// player, amount (rule 119.3)
    GainLife,
    /// player, amount
    LoseLife,
    /// obj (spell), player (rule 601.2i)
    Cast,
    /// obj, defender (rule 508.1)
    Attacks,
    /// obj
    Tap,
    /// obj
    Untap,
    /// obj (rule 701.22)
    Sacrifice,
    /// obj (rule 701.7)
    Destroy,
    /// obj (rule 701.9)
    ExileObj,
    /// step, player
    BeginStep,
    /// step, player
    EndStep,
    /// player (rule 702.87)
    Proliferate,
    /// player (rule 701.24)
    Shuffle,
    /// obj, player (rule 305)
    LandPlayed,
}

impl EventType {
    /// The Python tag string.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            EventType::ZoneChange => "zone_change",
            EventType::EntersBattlefield => "etb",
            EventType::Dies => "dies",
            EventType::CreateToken => "create_token",
            EventType::PutCounters => "put_counters",
            EventType::Draw => "draw",
            EventType::Damage => "damage",
            EventType::GainLife => "gain_life",
            EventType::LoseLife => "lose_life",
            EventType::Cast => "cast",
            EventType::Attacks => "attacks",
            EventType::Tap => "tap",
            EventType::Untap => "untap",
            EventType::Sacrifice => "sacrifice",
            EventType::Destroy => "destroy",
            EventType::ExileObj => "exile_obj",
            EventType::BeginStep => "begin_step",
            EventType::EndStep => "end_step",
            EventType::Proliferate => "proliferate",
            EventType::Shuffle => "shuffle",
            EventType::LandPlayed => "land_played",
        }
    }

    /// The tag of a Python tag string.
    #[must_use]
    pub fn parse(tag: &str) -> Option<EventType> {
        [
            EventType::ZoneChange,
            EventType::EntersBattlefield,
            EventType::Dies,
            EventType::CreateToken,
            EventType::PutCounters,
            EventType::Draw,
            EventType::Damage,
            EventType::GainLife,
            EventType::LoseLife,
            EventType::Cast,
            EventType::Attacks,
            EventType::Tap,
            EventType::Untap,
            EventType::Sacrifice,
            EventType::Destroy,
            EventType::ExileObj,
            EventType::BeginStep,
            EventType::EndStep,
            EventType::Proliferate,
            EventType::Shuffle,
            EventType::LandPlayed,
        ]
        .into_iter()
        .find(|t| t.as_str() == tag)
    }
}

impl std::fmt::Display for EventType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The payload of an event: the Python `data` dict, one optional field
/// per role name (see the per-type comments on [`EventType`]).
#[derive(Clone, Debug, Default)]
pub struct EventData {
    /// the object the event is about
    pub obj: Option<ObjId>,
    /// the player the event is about
    pub player: Option<PlayerId>,
    /// the controller (token creation, counters)
    pub controller: Option<PlayerId>,
    /// the source object (damage, token creation)
    pub source: Option<ObjId>,
    /// the target of damage
    pub target: Option<Target>,
    /// an amount (damage, life)
    pub amount: Option<i64>,
    /// a count (tokens, counters)
    pub count: Option<i64>,
    /// a counter kind
    pub kind: Option<String>,
    /// the token specification
    pub spec: Option<Rc<TokenSpec>>,
    /// further token specifications a replacement adds (Academy
    /// Manufactor style)
    pub extra_specs: Vec<Rc<TokenSpec>>,
    /// zone change: from
    pub from: Option<Zone>,
    /// zone change: to
    pub to: Option<Zone>,
    /// zone change: enters tapped (`None` = not specified)
    pub tapped: Option<bool>,
    /// zone change: counters the permanent enters with
    pub counters: IndexMap<String, i64>,
    /// damage: combat damage
    pub combat: Option<bool>,
    /// damage / counters: the event already happened (the post-event
    /// notification of trigger watchers)
    pub resolved: bool,
    /// life loss: caused by damage
    pub damage: bool,
    /// attacks: the defender
    pub defender: Option<Defender>,
    /// attacks: the creature attacks alone (exalted)
    pub alone: Option<bool>,
    /// step name of begin/end-step events
    pub step: Option<String>,
}

/// One game event: a type tag plus its payload (rules 603.2/614.1).
#[derive(Clone, Debug)]
pub struct Event {
    /// the type tag
    pub kind: EventType,
    /// the payload
    pub data: EventData,
    /// replacement effects that already applied to this event (rule
    /// 616.2: each replacement effect applies to a given event only once)
    pub applied: BTreeSet<ReplacementId>,
    /// set by a replacement/prevention effect that removes the event
    pub prevented: bool,
}

impl Event {
    /// An event with a payload.
    #[must_use]
    pub fn new(kind: EventType, data: EventData) -> Self {
        Event {
            kind,
            data,
            applied: BTreeSet::new(),
            prevented: false,
        }
    }

    /// An event about one object (`{"obj": obj}`).
    #[must_use]
    pub fn about(kind: EventType, obj: ObjId) -> Self {
        Event::new(
            kind,
            EventData {
                obj: Some(obj),
                ..EventData::default()
            },
        )
    }

    /// An event about one player (`{"player": player}`).
    #[must_use]
    pub fn for_player(kind: EventType, player: PlayerId) -> Self {
        Event::new(
            kind,
            EventData {
                player: Some(player),
                ..EventData::default()
            },
        )
    }
}

impl std::fmt::Display for Event {
    /// `<Event type ...>` (the payload is summarised, not Python's repr).
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<Event {}", self.kind)?;
        if let Some(obj) = self.data.obj {
            write!(f, " obj=#{obj}")?;
        }
        if let Some(player) = self.data.player {
            write!(f, " player={}", player.0)?;
        }
        if let Some(amount) = self.data.amount {
            write!(f, " amount={amount}")?;
        }
        if let Some(count) = self.data.count {
            write!(f, " count={count}")?;
        }
        f.write_str(">")
    }
}

#[cfg(test)]
mod tests {
    use super::{Event, EventType};
    use crate::objects::ObjId;

    #[test]
    fn tags_round_trip() {
        for tag in [
            "zone_change",
            "etb",
            "dies",
            "create_token",
            "put_counters",
            "land_played",
            "end_step",
        ] {
            assert_eq!(EventType::parse(tag).map(EventType::as_str), Some(tag));
        }
        assert_eq!(EventType::parse("nope"), None);
        let event = Event::about(EventType::Dies, ObjId(3));
        assert_eq!(event.to_string(), "<Event dies obj=#3>");
        assert!(!event.prevented && event.applied.is_empty());
    }
}
