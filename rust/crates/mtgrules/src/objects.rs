//! Game objects, characteristics, and zones (CR 1xx, 2xx, 4xx).
//!
//! Every in-game entity (card, token, copy of a spell, ability on the
//! stack) is a [`GameObject`] (rule 109.1). Characteristics (rule 109.3)
//! are computed by the layer system (rule 613) from the printed/base
//! values stored here.
//!
//! Where the Python engine passes object and player references around,
//! the Rust engine uses handles: [`ObjId`] into the game's object arena
//! and [`PlayerId`] into its player list. Objects are never removed from
//! the arena (a token that ceased to exist keeps its record, like the
//! Python object that nothing references any more), so a handle stays
//! valid for the whole game.

use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;
use mtgcards::CardData;

use crate::abilities::Ability;
use crate::manasys::ManaPool;
use crate::rule;

/// Zone names (rule 400.1).
#[rule("400.1")]
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Zone {
    /// the library (hidden)
    Library,
    /// the hand (hidden)
    Hand,
    /// the battlefield
    Battlefield,
    /// the graveyard
    Graveyard,
    /// the stack
    Stack,
    /// exile
    Exile,
    /// the command zone
    Command,
}

impl Zone {
    /// The zone's name as the Python engine spells it (logs, records).
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Zone::Library => "library",
            Zone::Hand => "hand",
            Zone::Battlefield => "battlefield",
            Zone::Graveyard => "graveyard",
            Zone::Stack => "stack",
            Zone::Exile => "exile",
            Zone::Command => "command",
        }
    }

    /// The zone of a Python zone name.
    #[must_use]
    pub fn parse(name: &str) -> Option<Zone> {
        Some(match name {
            "library" => Zone::Library,
            "hand" => Zone::Hand,
            "battlefield" => Zone::Battlefield,
            "graveyard" => Zone::Graveyard,
            "stack" => Zone::Stack,
            "exile" => Zone::Exile,
            "command" => Zone::Command,
            _ => return None,
        })
    }

    /// Rule 400.2: whether the zone is hidden (library, hand).
    #[must_use]
    pub const fn is_hidden(self) -> bool {
        matches!(self, Zone::Library | Zone::Hand)
    }
}

impl std::fmt::Display for Zone {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// The five colours in WUBRG order.
pub const COLORS: [&str; 5] = ["W", "U", "B", "R", "G"];

/// Handle of a [`GameObject`] in the game's arena (the Python `obj.id`,
/// numbered from 1 in creation order).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ObjId(pub u32);

impl std::fmt::Display for ObjId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Handle of a [`Player`]: its seat index.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct PlayerId(pub usize);

/// Something a creature attacks: a player or a planeswalker (rule 508.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Defender {
    /// a player
    Player(PlayerId),
    /// a permanent (planeswalker)
    Object(ObjId),
}

/// A value in the scratch state of hand-written card implementations.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CustomValue {
    /// an integer (a turn number, a count)
    Int(i64),
    /// an object handle
    Object(ObjId),
    /// a player handle
    Player(PlayerId),
}

/// The characteristic set of an object (rules 109.3, 613.1).
///
/// A fresh copy of the *base* (printed / token-defined) characteristics
/// is the input to each layer-system evaluation (rule 613.1: start with
/// the actual object, then apply continuous effects in layer order).
#[rule("109.3", "613.1")]
#[derive(Clone, Debug, Default)]
pub struct Characteristics {
    /// the name
    pub name: String,
    /// the mana cost, e.g. `{2}{G}{G}`
    pub mana_cost: String,
    /// colours (rules 105 / 202.2)
    pub colors: BTreeSet<String>,
    /// supertypes (rule 205.4)
    pub supertypes: BTreeSet<String>,
    /// card types (rule 205.2)
    pub types: BTreeSet<String>,
    /// subtypes (rule 205.3)
    pub subtypes: BTreeSet<String>,
    /// power (rule 208)
    pub power: Option<i64>,
    /// toughness (rule 208)
    pub toughness: Option<i64>,
    /// loyalty (rule 209)
    pub loyalty: Option<i64>,
    /// abilities (rule 113), shared between copies of the set
    pub abilities: Vec<Rc<Ability>>,
    /// keyword abilities (rule 702)
    pub keywords: BTreeSet<String>,
    /// rule 601.2f: generic cost reduction per creature you control; set
    /// at compile time on the base characteristics and deliberately not
    /// part of [`Characteristics::copy`] (layer copies never carried it;
    /// consumers read it from the base only)
    pub cost_less_per_creature: i64,
    /// rule 601.2b: an additional cost ("sacrifice a creature"); base only
    pub additional_cost: String,
    /// counter kind placed for the chosen X when the permanent enters
    /// (Hangarback Walker style); base only, like the cost modifiers
    pub etb_x_counters: String,
}

impl Characteristics {
    /// A fresh working copy for one layer-system evaluation: everything
    /// but the base-only cost modifiers.
    #[must_use]
    pub fn copy(&self) -> Characteristics {
        Characteristics {
            name: self.name.clone(),
            mana_cost: self.mana_cost.clone(),
            colors: self.colors.clone(),
            supertypes: self.supertypes.clone(),
            types: self.types.clone(),
            subtypes: self.subtypes.clone(),
            power: self.power,
            toughness: self.toughness,
            loyalty: self.loyalty,
            abilities: self.abilities.clone(),
            keywords: self.keywords.clone(),
            cost_less_per_creature: 0,
            additional_cost: String::new(),
            etb_x_counters: String::new(),
        }
    }

    /// A set of strings from string slices (a fixture convenience).
    #[must_use]
    pub fn set(items: &[&str]) -> BTreeSet<String> {
        items.iter().map(|s| (*s).to_owned()).collect()
    }

    /// Whether the type line has `kind` ("Creature", ...).
    #[must_use]
    pub fn has_type(&self, kind: &str) -> bool {
        self.types.contains(kind)
    }

    /// Whether the object has the keyword ability (`"flying"`).
    #[must_use]
    pub fn has_keyword(&self, keyword: &str) -> bool {
        self.keywords.contains(keyword)
    }
}

/// A card, token, or spell/ability on the stack (rules 109.1, 110.1,
/// 111.1).
#[rule("109.1", "110.1", "111.1")]
#[derive(Clone, Debug)]
#[expect(
    clippy::struct_excessive_bools,
    reason = "the status flags of rule 110.5 and the object kinds mirror the Python attributes one for one"
)]
pub struct GameObject {
    /// the handle (rule 109.1), numbered from 1 in creation order
    pub id: ObjId,
    /// printed / token characteristics; shared with any characteristics
    /// snapshot the layer system hands out for objects off the battlefield
    pub base: Rc<Characteristics>,
    /// the owner (rule 108.3)
    pub owner: PlayerId,
    /// the controller (rule 109.4)
    pub controller: PlayerId,
    /// the zone the object is in
    pub zone: Zone,
    /// rule 111
    pub is_token: bool,
    /// the knowledge-graph card (data provenance), when any
    pub card_ref: Option<Rc<CardData>>,
    /// rule 707.10 spell copies
    pub is_copy: bool,
    /// rule 613.7: creation / battlefield-entry timestamp
    pub timestamp: u64,
    /// battlefield status, rule 110.5
    pub tapped: bool,
    /// rule 110.5
    pub flipped: bool,
    /// rule 110.5
    pub face_down: bool,
    /// rule 110.5
    pub phased_out: bool,
    /// rule 122 counters: kind -> count, in order of first placement
    pub counters: IndexMap<String, i64>,
    /// snapshot for leave-the-battlefield triggers (rule 603.10a)
    pub lki_counters: IndexMap<String, i64>,
    /// rule 120.6 damage marked
    pub damage: i64,
    /// rule 704.5h
    pub deathtouch_damage: bool,
    /// auras/equipment, rules 303.4/301.5
    pub attached_to: Option<ObjId>,
    /// the objects attached to this one
    pub attachments: Vec<ObjId>,
    /// combat state: the defender attacked
    pub attacking: Option<Defender>,
    /// the creatures this one blocks
    pub blocking: Vec<ObjId>,
    /// the creatures blocking this one
    pub blocked_by: Vec<ObjId>,
    /// summoning sickness, rule 302.6
    pub entered_this_turn: bool,
    /// rule 903.3
    pub commander: bool,
    /// scratch state owned by hand-written card implementations; survives
    /// zone changes like any object attribute
    pub custom: IndexMap<String, CustomValue>,
}

impl GameObject {
    /// Mutable access to the base characteristics (copy-on-write when a
    /// snapshot of them is still shared).
    pub fn base_mut(&mut self) -> &mut Characteristics {
        Rc::make_mut(&mut self.base)
    }

    /// The printed name (`"object"` when unnamed), for logs.
    #[must_use]
    pub fn name(&self) -> &str {
        if self.base.name.is_empty() {
            "object"
        } else {
            &self.base.name
        }
    }

    /// The count of `kind` counters on the object.
    #[must_use]
    pub fn counter(&self, kind: &str) -> i64 {
        self.counters.get(kind).copied().unwrap_or(0)
    }

    /// Reset state that does not survive a zone change (rule 400.7).
    ///
    /// An object that changes zones becomes a new object: status,
    /// counters, damage, and attachments do not carry over. The caller
    /// supplies the new timestamp (rule 613.7).
    #[rule("110.5", "400.7")]
    pub fn reset_battlefield_state(&mut self, timestamp: u64) {
        self.tapped = false;
        self.flipped = false;
        self.face_down = false;
        self.phased_out = false;
        self.counters = IndexMap::new();
        self.damage = 0;
        self.deathtouch_damage = false;
        self.attached_to = None;
        self.attachments = Vec::new();
        self.attacking = None;
        self.blocking = Vec::new();
        self.blocked_by = Vec::new();
        self.entered_this_turn = false;
        self.timestamp = timestamp;
    }
}

impl std::fmt::Display for GameObject {
    /// `<Name#id zone>`, the Python `repr`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<{}#{} {}>", self.name(), self.id, self.zone)
    }
}

/// A player (rule 102). Life total starts at 40 in Commander (rule 903.7).
#[rule("102.1", "119.1")]
#[derive(Clone, Debug)]
pub struct Player {
    /// the seat
    pub id: PlayerId,
    /// the player name (the deck's seat name)
    pub name: String,
    /// the deck name
    pub deck_name: String,
    /// rule 903.7
    pub life: i64,
    /// the library; index 0 is the top card (draws `pop(0)`, the bottom
    /// is the end)
    pub library: Vec<ObjId>,
    /// the hand
    pub hand: Vec<ObjId>,
    /// the graveyard, newest card last
    pub graveyard: Vec<ObjId>,
    /// exile
    pub exile: Vec<ObjId>,
    /// the command zone
    pub command: Vec<ObjId>,
    /// the battlefield, in order of entry
    pub battlefield: Vec<ObjId>,
    /// the mana pool (rule 106.4)
    pub mana_pool: ManaPool,
    /// whether the player has lost
    pub lost: bool,
    /// why the player lost
    pub lose_reason: String,
    /// the commander (rule 903.3)
    pub commander_obj: Option<ObjId>,
    /// rule 903.8 commander tax
    pub commander_casts: i64,
    /// rule 903.10a: damage per commander, by its object
    pub commander_damage: IndexMap<ObjId, i64>,
    /// rule 305.2
    pub lands_played: i64,
    /// rule 402.2
    pub max_hand_size: i64,
    /// energy counters (rule 122.1)
    pub energy: i64,
    /// experience counters
    pub experience: i64,
    /// poison counters
    pub poison: i64,
    /// rule 704.5c flag
    pub drew_from_empty: bool,
    /// attraction deck, when any
    pub attractions: Option<Vec<ObjId>>,
    /// per-game statistics, in order of first increment
    pub stats: IndexMap<String, i64>,
    /// cards cast, for the per-card win-rate lift
    pub cards_cast: Vec<String>,
    /// pod politics: attacker name -> cumulative power sent at us
    pub grudges: IndexMap<String, f64>,
}

impl Player {
    /// A player with empty zones and the Commander life total.
    #[must_use]
    pub fn new(id: PlayerId, name: &str, deck_name: &str) -> Self {
        Player {
            id,
            name: name.to_owned(),
            deck_name: deck_name.to_owned(),
            life: 40,
            library: Vec::new(),
            hand: Vec::new(),
            graveyard: Vec::new(),
            exile: Vec::new(),
            command: Vec::new(),
            battlefield: Vec::new(),
            mana_pool: ManaPool::new(),
            lost: false,
            lose_reason: String::new(),
            commander_obj: None,
            commander_casts: 0,
            commander_damage: IndexMap::new(),
            lands_played: 0,
            max_hand_size: 7,
            energy: 0,
            experience: 0,
            poison: 0,
            drew_from_empty: false,
            attractions: None,
            stats: IndexMap::new(),
            cards_cast: Vec::new(),
            grudges: IndexMap::new(),
        }
    }

    /// The player's card list for a (non-stack) zone.
    ///
    /// # Panics
    ///
    /// Panics for [`Zone::Stack`], which no player owns (the Python
    /// `KeyError`).
    #[must_use]
    pub fn zone_list(&self, zone: Zone) -> &Vec<ObjId> {
        match zone {
            Zone::Library => &self.library,
            Zone::Hand => &self.hand,
            Zone::Graveyard => &self.graveyard,
            Zone::Exile => &self.exile,
            Zone::Command => &self.command,
            Zone::Battlefield => &self.battlefield,
            Zone::Stack => panic!("the stack is not a player's zone"),
        }
    }

    /// Mutable [`Player::zone_list`].
    ///
    /// # Panics
    ///
    /// Panics for [`Zone::Stack`].
    pub fn zone_list_mut(&mut self, zone: Zone) -> &mut Vec<ObjId> {
        match zone {
            Zone::Library => &mut self.library,
            Zone::Hand => &mut self.hand,
            Zone::Graveyard => &mut self.graveyard,
            Zone::Exile => &mut self.exile,
            Zone::Command => &mut self.command,
            Zone::Battlefield => &mut self.battlefield,
            Zone::Stack => panic!("the stack is not a player's zone"),
        }
    }

    /// Add `n` to the per-game statistic `key`.
    pub fn stat(&mut self, key: &str, n: i64) {
        *self.stats.entry(key.to_owned()).or_insert(0) += n;
    }
}

impl std::fmt::Display for Player {
    /// `<Player name life=N>`, the Python `repr`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "<Player {} life={}>", self.name, self.life)
    }
}

#[cfg(test)]
mod tests {
    use super::{Characteristics, Player, PlayerId, Zone};

    #[test]
    fn zones_round_trip_and_hidden() {
        for zone in [
            Zone::Library,
            Zone::Hand,
            Zone::Battlefield,
            Zone::Graveyard,
            Zone::Stack,
            Zone::Exile,
            Zone::Command,
        ] {
            assert_eq!(Zone::parse(zone.as_str()), Some(zone));
        }
        assert!(Zone::Hand.is_hidden() && !Zone::Exile.is_hidden());
        assert_eq!(Zone::parse("nowhere"), None);
    }

    #[test]
    fn copy_drops_the_base_only_fields() {
        let base = Characteristics {
            name: "X".into(),
            cost_less_per_creature: 2,
            additional_cost: "sac".into(),
            etb_x_counters: "+1/+1".into(),
            power: Some(1),
            ..Characteristics::default()
        };
        let copy = base.copy();
        assert_eq!((copy.name.as_str(), copy.power), ("X", Some(1)));
        assert_eq!(copy.cost_less_per_creature, 0);
        assert!(copy.additional_cost.is_empty() && copy.etb_x_counters.is_empty());
    }

    #[test]
    fn player_defaults_and_stats() {
        let mut p = Player::new(PlayerId(0), "p0", "");
        assert_eq!((p.life, p.max_hand_size), (40, 7));
        p.stat("drain", 2);
        p.stat("drain", 3);
        p.stat("attacks", 1);
        assert_eq!(
            p.stats.iter().collect::<Vec<_>>(),
            [(&"drain".to_owned(), &5), (&"attacks".to_owned(), &1)]
        );
        assert_eq!(p.to_string(), "<Player p0 life=40>");
    }
}
