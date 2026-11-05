//! The game: zones, events, the stack, priority, and state-based actions.
//!
//! Implements the CR machinery the heuristic simulator lacked: a real
//! stack with priority passing (rules 117, 405, 601-608), state-based
//! actions (rule 704), the trigger queue (rule 603), and the Commander
//! variant rules (rule 903). Turn structure and combat live in
//! [`crate::turns`] / [`crate::combat`].
//!
//! The Python `game.py` is one class; here its sections are submodules,
//! each an `impl Game` block: `zones` (zone changes), `triggers`
//! (the trigger queue and target choice), `actions` (tokens, draws,
//! damage, life, counters, the keyword actions), `mana` (mana abilities
//! and the greedy payment planner), `casting` (casting, activation,
//! playing lands), `resolution` (resolving the stack and the one-shot
//! effects) and `sba` (state-based actions, priority).
//!
//! Counters that the Python engine keeps process-wide (object ids,
//! timestamps, effect and replacement ids) are per game here: they are
//! never observable outside a game, and per-game counters keep parallel
//! games deterministic.

use std::cell::Cell;
use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;
use mtgcards::CardData;
use pycompat::random::PyRandom;

use crate::abilities::{AbilityId, TargetSpec};
use crate::effects::Ctx;
use crate::layers::LayerState;
use crate::objects::{Characteristics, CustomValue, GameObject, ObjId, Player, PlayerId, Zone};
use crate::policy::Policy;
use crate::protocols::{LogFields, LogFn, LogValue};
use crate::replacements::ReplacementState;
use crate::stack::{PendingTrigger, StackId, StackItem, StackObject, Target};
use crate::{rule, unsupported};

mod actions;
mod casting;
mod mana;
mod resolution;
mod sba;
mod triggers;
mod zones;

pub use mana::ManaSource;
pub use zones::{LibraryPosition, MoveOptions};

unsupported!(
    "903.4",
    "deck color-identity legality is validated by the knowledge-graph tooling, not at runtime"
);

/// Rule 704.5c: a player with ten or more poison counters loses.
pub const POISON_LOSS_THRESHOLD: i64 = 10;
/// Rules 903.10a / 704.6c: 21+ combat damage from one commander loses.
pub const COMMANDER_DAMAGE_LOSS: i64 = 21;

/// A key of `activated_this_turn` (rule 606.3 and once-per-turn
/// tracking).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ActivationKey {
    /// a loyalty ability of the planeswalker was activated
    Loyalty(ObjId),
    /// the once-per-turn activated ability was activated
    Ability(ObjId, AbilityId),
    /// the "only once each turn" triggered ability triggered
    Trigger(ObjId, AbilityId),
}

/// One game: players, zones, the stack, and the CR machinery.
pub struct Game {
    /// the players in seat order
    pub players: Vec<Player>,
    /// the object arena; `ObjId(n)` is index `n - 1`
    objects: Vec<GameObject>,
    /// the seeded random stream (CPython-compatible)
    pub rng: PyRandom,
    /// one policy per seat
    policies: Vec<Rc<dyn Policy>>,
    /// the turn cap
    pub turn_cap: u32,
    /// the current turn number (0 before the first)
    pub turn: u32,
    /// the active player's seat
    pub active_idx: usize,
    /// the current phase/step name
    pub phase: String,
    /// once-per-turn and loyalty tracking (rule 606.3)
    pub activated_this_turn: BTreeSet<ActivationKey>,
    /// the stack, bottom first
    pub stack: Vec<StackItem>,
    /// triggered abilities waiting to be put on the stack
    pub pending_triggers: Vec<PendingTrigger>,
    /// the state tick; bumped on every change that can affect
    /// characteristics
    pub tick: u64,
    /// the layer system
    pub layers: LayerState,
    /// the replacement machinery
    pub replacements: ReplacementState,
    /// the log sink
    log: LogFn,
    /// whether the game has ended
    pub game_over: bool,
    /// the winner, when the game ended with one
    pub winner: Option<PlayerId>,
    /// card name -> oracle clauses the compiler could not model
    pub unknown_clauses: IndexMap<String, BTreeSet<String>>,
    /// scratch state owned by hand-written card implementations
    pub custom: IndexMap<String, CustomValue>,
    next_stack_id: u32,
    timestamps: Cell<u64>,
    effect_ids: Cell<u64>,
    replacement_ids: Cell<u64>,
}

impl std::fmt::Debug for Game {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Game")
            .field("players", &self.players.len())
            .field("objects", &self.objects.len())
            .field("turn", &self.turn)
            .field("phase", &self.phase)
            .field("stack", &self.stack.len())
            .field("tick", &self.tick)
            .finish_non_exhaustive()
    }
}

/// One log field (`key=value` of the Python `log(event, **fields)`).
pub(crate) fn field(key: &'static str, value: impl Into<LogValue>) -> (&'static str, LogValue) {
    (key, value.into())
}

impl Game {
    /// A game of `names` (one seat per name) with `policies` (one per
    /// seat, cycled when fewer), a seeded random stream and empty zones.
    ///
    /// # Panics
    ///
    /// Panics when `policies` is empty.
    #[must_use]
    pub fn new(names: &[&str], rng: PyRandom, policies: &[Rc<dyn Policy>], turn_cap: u32) -> Self {
        let players = names
            .iter()
            .enumerate()
            .map(|(i, name)| Player::new(PlayerId(i), name, ""))
            .collect();
        Self::with_players(players, rng, policies, turn_cap)
    }

    /// A game of prepared players (the adapter's, with deck names); their
    /// seats are renumbered in order and their mana pools start fresh.
    ///
    /// # Panics
    ///
    /// Panics when `policies` is empty.
    #[must_use]
    pub fn with_players(
        mut players: Vec<Player>,
        rng: PyRandom,
        policies: &[Rc<dyn Policy>],
        turn_cap: u32,
    ) -> Self {
        assert!(!policies.is_empty(), "a game needs at least one policy");
        for (i, p) in players.iter_mut().enumerate() {
            p.id = PlayerId(i);
            p.mana_pool = crate::manasys::ManaPool::new();
        }
        let policies: Vec<Rc<dyn Policy>> = (0..players.len())
            .map(|i| Rc::clone(&policies[i % policies.len()]))
            .collect();
        Game {
            players,
            objects: Vec::new(),
            rng,
            policies,
            turn_cap,
            turn: 0,
            active_idx: 0,
            phase: "main1".to_owned(),
            activated_this_turn: BTreeSet::new(),
            stack: Vec::new(),
            pending_triggers: Vec::new(),
            tick: 0,
            layers: LayerState::default(),
            replacements: ReplacementState::default(),
            log: Box::new(|_, _, _| {}),
            game_over: false,
            winner: None,
            unknown_clauses: IndexMap::new(),
            custom: IndexMap::new(),
            next_stack_id: 0,
            timestamps: Cell::new(0),
            effect_ids: Cell::new(0),
            replacement_ids: Cell::new(0),
        }
    }

    /// Install the log sink (`log(event, **fields)`).
    pub fn set_log(&mut self, log: LogFn) {
        self.log = log;
    }

    /// Write one log event.
    pub fn log(&mut self, event: &str, fields: LogFields) {
        // the sink borrows the game, so it is taken out for the call
        let mut sink = std::mem::replace(&mut self.log, Box::new(|_, _, _| {}));
        sink(self, event, fields);
        self.log = sink;
    }

    // ------------------------------------------------------------ helpers

    /// Invalidate the layer-system cache (state changed).
    pub fn bump(&mut self) {
        self.tick += 1;
    }

    /// Rule 613.7c-e: timestamps are totally ordered by creation.
    #[must_use]
    pub fn next_timestamp(&self) -> u64 {
        self.timestamps.set(self.timestamps.get() + 1);
        self.timestamps.get()
    }

    pub(crate) fn next_effect_id(&self) -> u64 {
        self.effect_ids.set(self.effect_ids.get() + 1);
        self.effect_ids.get()
    }

    pub(crate) fn next_replacement_id(&self) -> u64 {
        self.replacement_ids.set(self.replacement_ids.get() + 1);
        self.replacement_ids.get()
    }

    /// A fresh stack-item identity.
    #[must_use]
    pub fn next_stack_id(&mut self) -> StackId {
        self.next_stack_id += 1;
        StackId(self.next_stack_id)
    }

    /// The player whose turn it is (rule 102.1).
    #[must_use]
    pub fn active_player(&self) -> PlayerId {
        PlayerId(self.active_idx)
    }

    /// The surviving opponents of `player` (rule 102.2).
    #[rule("102.2", "102.3")]
    #[must_use]
    pub fn opponents(&self, player: PlayerId) -> Vec<PlayerId> {
        self.players
            .iter()
            .filter(|p| p.id != player && !p.lost)
            .map(|p| p.id)
            .collect()
    }

    /// All players still in the game.
    #[must_use]
    pub fn alive(&self) -> Vec<PlayerId> {
        self.players
            .iter()
            .filter(|p| !p.lost)
            .map(|p| p.id)
            .collect()
    }

    /// Active player, nonactive players in turn order (rule 101.4).
    #[rule("101.4")]
    #[must_use]
    pub fn players_apnap(&self) -> Vec<PlayerId> {
        let n = self.players.len();
        (0..n)
            .map(|i| PlayerId((self.active_idx + i) % n))
            .filter(|p| !self.player(*p).lost)
            .collect()
    }

    /// The decision policy that plays for `player`.
    #[must_use]
    pub fn policy(&self, player: PlayerId) -> Rc<dyn Policy> {
        Rc::clone(&self.policies[player.0])
    }

    /// Replace a player's decision policy (the Python
    /// `game.policies[name] = ...`).
    pub fn set_policy(&mut self, player: PlayerId, policy: Rc<dyn Policy>) {
        self.policies[player.0] = policy;
    }

    /// Every permanent on any player's battlefield, players in seat
    /// order, each battlefield in order of entry.
    #[must_use]
    pub fn battlefield_objects(&self) -> Vec<ObjId> {
        self.players
            .iter()
            .flat_map(|p| p.battlefield.iter().copied())
            .collect()
    }

    /// The commander's colour identity (rule 903.4); all five colours
    /// without a commander card.
    #[must_use]
    pub fn commander_identity(&self, player: PlayerId) -> BTreeSet<String> {
        let card = self
            .player(player)
            .commander_obj
            .and_then(|id| self.obj(id).card_ref.clone());
        match card {
            Some(card) => card.color_identity.clone(),
            None => crate::objects::COLORS
                .iter()
                .map(|c| (*c).to_owned())
                .collect(),
        }
    }

    /// Hexproof (rule 702.11): can't be targeted by opponents.
    #[rule("702.11")]
    #[must_use]
    pub fn cant_be_targeted(&self, obj: ObjId, ctx: &Ctx) -> bool {
        let ch = self.chars(obj);
        ch.keywords.contains("hexproof") && ctx.controller != self.obj(obj).controller
    }

    // ------------------------------------------------------------ names

    /// The name of a player (for logs).
    #[must_use]
    pub fn name_of(&self, player: PlayerId) -> String {
        self.player(player).name.clone()
    }

    /// Loggable name of an object: its printed name, or `(unnamed)`.
    #[must_use]
    pub fn lname_obj(&self, obj: ObjId) -> String {
        let name = &self.obj(obj).base.name;
        if name.is_empty() {
            "(unnamed)".to_owned()
        } else {
            name.clone()
        }
    }

    /// Loggable name of a stack item: the spell's name or
    /// `ability of <source>`.
    #[must_use]
    pub fn lname_item(&self, item: &StackItem) -> String {
        if item.is_spell
            && let StackObject::Spell(card) = item.obj
        {
            return self.obj(card).base.name.clone();
        }
        format!("ability of {}", self.obj(item.source).base.name)
    }

    /// Loggable name of a target.
    #[must_use]
    pub fn lname_target(&self, target: Target) -> String {
        match target {
            Target::Object(o) => self.lname_obj(o),
            Target::Player(p) => self.name_of(p),
            Target::Spell(id) => self
                .stack_item(id)
                .map_or_else(|| "?".to_owned(), |i| self.lname_item(i)),
        }
    }

    /// Loggable name of a damage source (`None` for no source, like
    /// Python's `str(None)`).
    #[must_use]
    pub fn lname_source(&self, source: Option<ObjId>) -> String {
        source.map_or_else(|| "None".to_owned(), |o| self.lname_obj(o))
    }

    // ------------------------------------------------------------ arena

    /// The object behind a handle.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another game.
    #[must_use]
    pub fn obj(&self, id: ObjId) -> &GameObject {
        &self.objects[id.0 as usize - 1]
    }

    /// The object behind a handle, mutably.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another game.
    pub fn obj_mut(&mut self, id: ObjId) -> &mut GameObject {
        &mut self.objects[id.0 as usize - 1]
    }

    /// Every object ever created, in creation order.
    #[must_use]
    pub fn objects(&self) -> &[GameObject] {
        &self.objects
    }

    /// The player behind a handle.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another game.
    #[must_use]
    pub fn player(&self, id: PlayerId) -> &Player {
        &self.players[id.0]
    }

    /// The player behind a handle, mutably.
    ///
    /// # Panics
    ///
    /// Panics on a handle from another game.
    pub fn player_mut(&mut self, id: PlayerId) -> &mut Player {
        &mut self.players[id.0]
    }

    /// The stack item with `id`, while it is on the stack.
    #[must_use]
    pub fn stack_item(&self, id: StackId) -> Option<&StackItem> {
        self.stack.iter().find(|item| item.id == id)
    }

    /// The position of the stack item with `id` (`item in self.stack`).
    #[must_use]
    pub fn stack_index(&self, id: StackId) -> Option<usize> {
        self.stack.iter().position(|item| item.id == id)
    }

    /// Create an object (rule 109.1) in its owner's library zone without
    /// placing it anywhere; the caller moves it (the Python
    /// `GameObject(...)` constructor).
    ///
    /// # Panics
    ///
    /// Panics after four billion objects.
    pub fn create_object(
        &mut self,
        base: Characteristics,
        owner: PlayerId,
        is_token: bool,
        card_ref: Option<Rc<CardData>>,
    ) -> ObjId {
        let id =
            ObjId(u32::try_from(self.objects.len() + 1).expect("fewer than 4 billion objects"));
        let timestamp = self.next_timestamp();
        self.objects.push(GameObject {
            id,
            base: Rc::new(base),
            owner,
            controller: owner,
            zone: Zone::Library,
            is_token,
            card_ref,
            is_copy: false,
            timestamp,
            tapped: false,
            flipped: false,
            face_down: false,
            phased_out: false,
            counters: IndexMap::new(),
            lki_counters: IndexMap::new(),
            damage: 0,
            deathtouch_damage: false,
            attached_to: None,
            attachments: Vec::new(),
            attacking: None,
            blocking: Vec::new(),
            blocked_by: Vec::new(),
            entered_this_turn: false,
            commander: false,
            custom: IndexMap::new(),
        });
        id
    }

    /// Place an object into a player's zone list directly, setting its
    /// zone (a fixture helper and the primitive of zone bookkeeping; the
    /// rules-aware `move_zone` is in the zones module).
    pub fn place(&mut self, id: ObjId, holder: PlayerId, zone: Zone) {
        self.obj_mut(id).zone = zone;
        self.player_mut(holder).zone_list_mut(zone).push(id);
        self.bump();
    }

    // ------------------------------------------------------------ targets

    /// All currently legal targets for one `TargetSpec` (rule 115.4).
    #[must_use]
    pub fn legal_targets(&self, spec: &TargetSpec, ctx: &Ctx) -> Vec<Target> {
        if spec.what == "player" || spec.what == "opponent" {
            return self
                .alive()
                .into_iter()
                .map(Target::Player)
                .filter(|t| spec.legal(self, ctx, *t))
                .collect();
        }
        if spec.what == "spell" {
            return self
                .stack
                .iter()
                .filter(|i| i.is_spell)
                .map(|i| Target::Spell(i.id))
                .collect();
        }
        let mut objs: Vec<Target> = self
            .battlefield_objects()
            .into_iter()
            .map(Target::Object)
            .filter(|t| spec.legal(self, ctx, *t))
            .collect();
        if spec.what == "any" {
            objs.extend(
                self.alive()
                    .into_iter()
                    .map(Target::Player)
                    .filter(|t| spec.legal(self, ctx, *t)),
            );
        }
        objs
    }

    /// Rule 608.2b: re-check a target's legality on resolution. The full
    /// check of the spec it was chosen for runs again (fix F7); without a
    /// spec only the zone is known.
    #[rule("608.2b")]
    #[must_use]
    pub fn still_legal_target(&self, target: Target, ctx: &Ctx, index: usize) -> bool {
        match ctx.specs.get(index) {
            Some(spec) => spec.legal(self, ctx, target),
            None => match target {
                Target::Object(id) => self.obj(id).zone == Zone::Battlefield,
                _ => true,
            },
        }
    }
}
