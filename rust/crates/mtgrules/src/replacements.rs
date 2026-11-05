//! Replacement and prevention effects (CR 614, 615, 616).
//!
//! A replacement effect watches for a particular event and completely or
//! partially replaces it (rule 614.1). Replacement effects apply before
//! the event occurs, never to an event that has already happened
//! (614.5), and each effect applies to a given event only once (616.2).
//! If several would apply, the affected object's controller (or the
//! affected player) chooses the order (616.1); self-replacement effects
//! apply first (614.16a).
//!
//! Like the layer system, the engine is a set of [`Game`] methods over a
//! per-game [`ReplacementState`]; `Game::emit` is the Python
//! `ReplacementEngine.process`.

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use crate::events::{Event, EventType};
use crate::game::Game;
use crate::layers::StaticKey;
use crate::objects::{ObjId, PlayerId};
use crate::protocols::{EventMatcher, EventReplacer};
use crate::rule;
use crate::stack::Target;

/// Identity of a replacement effect (rule 616.2 "applies once per event"
/// bookkeeping), numbered from 1 per game; 0 = not yet registered.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ReplacementId(pub u64);

/// How long a replacement effect lasts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ReplacementDuration {
    /// while the source is on the battlefield
    #[default]
    Static,
    /// registered directly on the game, independent of a source
    Floating,
}

/// One replacement/prevention effect (rule 614.1).
#[rule("614.1")]
#[derive(Clone)]
pub struct Replacement {
    /// the event type it watches
    pub event_type: EventType,
    /// `replace(game, event)` -> the event, or `None` (prevented, 615)
    pub replace: EventReplacer,
    /// `matches(game, event)`; `None` matches every event of the type
    pub matches: Option<EventMatcher>,
    /// the source permanent
    pub source: Option<ObjId>,
    /// rule 614.16a: self-replacement effects apply before others
    pub self_replacement: bool,
    /// the duration
    pub duration: ReplacementDuration,
    /// identity; 0 = assigned on registration
    pub id: ReplacementId,
}

impl std::fmt::Debug for Replacement {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Replacement")
            .field("event_type", &self.event_type)
            .field("matches", &self.matches.is_some())
            .field("source", &self.source)
            .field("self_replacement", &self.self_replacement)
            .field("duration", &self.duration)
            .field("id", &self.id)
            .finish_non_exhaustive()
    }
}

impl Replacement {
    /// A replacement of every `event_type` event.
    #[must_use]
    pub fn new(
        event_type: EventType,
        replace: impl Fn(&mut Game, Event) -> Option<Event> + 'static,
    ) -> Self {
        Replacement {
            event_type,
            replace: Rc::new(replace),
            matches: None,
            source: None,
            self_replacement: false,
            duration: ReplacementDuration::Static,
            id: ReplacementId(0),
        }
    }

    /// Restrict to the events `matches` accepts.
    #[must_use]
    pub fn when(mut self, matches: impl Fn(&Game, &Event) -> bool + 'static) -> Self {
        self.matches = Some(Rc::new(matches));
        self
    }

    /// With a source permanent.
    #[must_use]
    pub fn with_source(mut self, source: Option<ObjId>) -> Self {
        self.source = source;
        self
    }

    /// A self-replacement effect (rule 614.16a).
    #[must_use]
    pub fn self_replacing(mut self) -> Self {
        self.self_replacement = true;
        self
    }

    /// A floating effect, independent of a source.
    #[must_use]
    pub fn floating(mut self) -> Self {
        self.duration = ReplacementDuration::Floating;
        self
    }
}

/// The per-game state of the replacement machinery.
#[derive(Default)]
pub struct ReplacementState {
    /// floating replacement effects, independent of a permanent
    pub floating: Vec<Rc<Replacement>>,
    /// stable instances per (source id, battlefield-entry timestamp,
    /// ability id) so that rule 616.2 "applies only once per event"
    /// bookkeeping works; the timestamp gives a permanent that left and
    /// returned fresh instances (rule 400.7, fix F13)
    cache: RefCell<HashMap<StaticKey, Vec<Rc<Replacement>>>>,
}

impl std::fmt::Debug for ReplacementState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ReplacementState")
            .field("floating", &self.floating.len())
            .finish_non_exhaustive()
    }
}

impl Game {
    /// Register a floating replacement effect (the Python
    /// `game.replacements.floating.append(...)`).
    pub fn add_floating_replacement(&mut self, mut replacement: Replacement) {
        self.register_replacement(&mut replacement);
        self.replacements.floating.push(Rc::new(replacement));
    }

    /// Drop the cached per-ability replacement instances (fixtures use
    /// this after grafting abilities onto an object already on the
    /// battlefield).
    pub fn clear_replacement_cache(&self) {
        self.replacements.cache.borrow_mut().clear();
    }

    fn register_replacement(&self, replacement: &mut Replacement) {
        if replacement.id == ReplacementId(0) {
            replacement.id = ReplacementId(self.next_replacement_id());
        }
    }

    /// All currently active replacement effects: from static abilities of
    /// battlefield permanents (their computed characteristics, so granted
    /// abilities count) plus the floating ones (rules 614.7a, 616.1).
    #[rule("614.7a", "616.1")]
    fn active_replacements(&self) -> Vec<Rc<Replacement>> {
        let mut out: Vec<Rc<Replacement>> = self.replacements.floating.clone();
        for id in self.battlefield_objects() {
            let chars = self.chars(id);
            let timestamp = self.obj(id).timestamp;
            for ab in &chars.abilities {
                let Some(stat) = ab.as_static() else { continue };
                let Some(factory) = &stat.replacement else {
                    continue;
                };
                let key = (id, timestamp, stat.id);
                if !self.replacements.cache.borrow().contains_key(&key) {
                    let replacements: Vec<Rc<Replacement>> = factory(self, id)
                        .into_iter()
                        .map(|mut r| {
                            self.register_replacement(&mut r);
                            Rc::new(r)
                        })
                        .collect();
                    self.replacements
                        .cache
                        .borrow_mut()
                        .insert(key, replacements);
                }
                out.extend(self.replacements.cache.borrow()[&key].iter().cloned());
            }
        }
        out
    }

    /// Route an event through the replacement machinery (rules 614.5,
    /// 616.1, 616.2). Returns the (possibly modified) event, or `None` if
    /// a prevention effect removed it entirely.
    #[rule("614.5", "616.1", "616.2")]
    pub fn emit(&mut self, mut event: Event) -> Option<Event> {
        loop {
            let candidates: Vec<Rc<Replacement>> = self
                .active_replacements()
                .into_iter()
                .filter(|r| {
                    r.event_type == event.kind
                        && !event.applied.contains(&r.id)
                        && r.matches.as_ref().is_none_or(|m| m(self, &event))
                })
                .collect();
            if candidates.is_empty() {
                return Some(event);
            }
            // rule 614.16a: self-replacements first; then rule 616.1 - the
            // affected player/controller chooses; the default policy picks
            // the first, which is deterministic and unbiased for the pool
            // (all stacked doublers are multiplicative and commute)
            let selfs: Vec<Rc<Replacement>> = candidates
                .iter()
                .filter(|r| r.self_replacement)
                .cloned()
                .collect();
            let pool = if selfs.is_empty() { candidates } else { selfs };
            let chooser = self.affected_player(&event);
            let picked = match chooser {
                Some(player) if pool.len() > 1 => {
                    let policy = self.policy(player);
                    policy.choose_replacement(self, &event, &pool)
                }
                _ => Rc::clone(&pool[0]),
            };
            event.applied.insert(picked.id);
            match (picked.replace)(self, event) {
                Some(replaced) if !replaced.prevented => event = replaced,
                _ => return None,
            }
        }
    }

    /// The player who orders replacements (rule 616.1): the event's
    /// controller, player or targeted player, else the controller of its
    /// object or targeted object.
    fn affected_player(&self, event: &Event) -> Option<PlayerId> {
        let data = &event.data;
        if let Some(player) = data.controller.or(data.player) {
            return Some(player);
        }
        if let Some(Target::Player(player)) = data.target {
            return Some(player);
        }
        let obj = data.obj.or_else(|| data.target.and_then(Target::object))?;
        Some(self.obj(obj).controller)
    }
}
