//! Continuous effects and the layer system - full CR 613 implementation.
//!
//! Characteristics are computed by starting from each object's base
//! (printed or token-defined) values and applying every applicable
//! continuous effect in layer order (rule 613.1):
//!
//! 1 copy, 2 control, 3 text, 4 type, 5 color, 6 abilities, 7 power/
//! toughness (7a characteristic-defining, 7b setting, 7c modifying incl.
//! counters, 7d switching) - rules 613.1-613.4.
//!
//! Within a layer/sublayer, effects apply in timestamp order (rule 613.7)
//! unless one is dependent on another (rule 613.8): a dependent effect
//! waits for the effect it depends on; dependency loops fall back to
//! timestamp order (rule 613.8c). Dependency is detected by probing
//! (613.8a): B is applied to a scratch copy of the state and we test
//! whether that changes (i) whether A applies at all / what A applies to,
//! or (ii) what A does.
//!
//! Characteristic-defining abilities (rules 604.3, 613.2) are ordinary
//! effects of layer 7a here; ability-adding effects of layer 6 feed a
//! re-collection pass so statics granted in layer 6 contribute their
//! layer-7 effects (rule 613.6).
//!
//! The evaluator is a set of [`Game`] methods over [`LayerState`]: the
//! caches are interior-mutable so that `Game::chars` works through a
//! shared reference, which is what every predicate and policy holds.

use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use indexmap::IndexMap;

use crate::abilities::AbilityId;
use crate::game::Game;
use crate::objects::{Characteristics, ObjId};
use crate::protocols::{CharMutator, CharPredicate};
use crate::rule;

/// Identity of a continuous effect (rule 613.7 tie-break after the
/// timestamp), numbered from 1 per game; 0 = not yet registered.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct EffectId(pub u64);

/// How long a continuous effect lasts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Duration {
    /// while the source's static ability is active (rule 611.3)
    #[default]
    Static,
    /// until cleanup (rules 611.2a-b, 514.2)
    EndOfTurn,
}

/// Layer order including the layer-7 sublayers.
pub const LAYER_ORDER: [(u8, &str); 10] = [
    (1, ""),
    (2, ""),
    (3, ""),
    (4, ""),
    (5, ""),
    (6, ""),
    (7, "a"),
    (7, "b"),
    (7, "c"),
    (7, "d"),
];

/// One continuous effect: a layer slot, a predicate, and a mutator
/// (rules 611.1, 613.1).
#[rule("611.1", "613.1")]
#[derive(Clone)]
pub struct ContinuousEffect {
    /// the layer (1-7)
    pub layer: u8,
    /// the layer-7 sublayer (`a`-`d`), else empty
    pub sublayer: String,
    /// `applies_to(game, obj, chars)`
    pub applies_to: CharPredicate,
    /// `apply(game, obj, chars)` mutates the working characteristics
    pub apply: CharMutator,
    /// the source permanent
    pub source: Option<ObjId>,
    /// the duration
    pub duration: Duration,
    /// rules 604.3 / 613.2
    pub is_cda: bool,
    /// rule 613.7 timestamp; 0 = assigned on registration (the source's
    /// timestamp for a static effect, rule 613.7b, else a fresh one)
    pub timestamp: u64,
    /// identity; 0 = assigned on registration
    pub id: EffectId,
}

impl std::fmt::Debug for ContinuousEffect {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ContinuousEffect")
            .field("layer", &self.layer)
            .field("sublayer", &self.sublayer)
            .field("source", &self.source)
            .field("duration", &self.duration)
            .field("is_cda", &self.is_cda)
            .field("timestamp", &self.timestamp)
            .field("id", &self.id)
            .finish_non_exhaustive()
    }
}

impl ContinuousEffect {
    /// An effect in `layer`/`sublayer` with no source, static duration.
    #[must_use]
    pub fn new(
        layer: u8,
        sublayer: &str,
        applies_to: impl Fn(&Game, ObjId, &Characteristics) -> bool + 'static,
        apply: impl Fn(&Game, ObjId, &mut Characteristics) + 'static,
    ) -> Self {
        ContinuousEffect {
            layer,
            sublayer: sublayer.to_owned(),
            applies_to: Rc::new(applies_to),
            apply: Rc::new(apply),
            source: None,
            duration: Duration::Static,
            is_cda: false,
            timestamp: 0,
            id: EffectId(0),
        }
    }

    /// With a source permanent.
    #[must_use]
    pub fn with_source(mut self, source: Option<ObjId>) -> Self {
        self.source = source;
        self
    }

    /// Lasting until cleanup.
    #[must_use]
    pub fn until_end_of_turn(mut self) -> Self {
        self.duration = Duration::EndOfTurn;
        self
    }

    /// With an explicit timestamp.
    #[must_use]
    pub fn with_timestamp(mut self, timestamp: u64) -> Self {
        self.timestamp = timestamp;
        self
    }

    /// A characteristic-defining ability's effect.
    #[must_use]
    pub fn cda(mut self) -> Self {
        self.is_cda = true;
        self
    }
}

/// The cache key of a static ability's effects: the source, its
/// battlefield-entry timestamp, the ability.
pub type StaticKey = (ObjId, u64, AbilityId);

/// Computed characteristics per battlefield object, with the game tick
/// they were computed at.
#[derive(Default)]
struct CharsCache {
    tick: Option<u64>,
    chars: HashMap<ObjId, Rc<Characteristics>>,
}

/// The per-game state of the layer system.
#[derive(Default)]
pub struct LayerState {
    /// floating (non-static) continuous effects
    pub floating: Vec<Rc<ContinuousEffect>>,
    /// the computed characteristics
    cache: RefCell<CharsCache>,
    /// stable effect instances per (source id, the source's
    /// battlefield-entry timestamp, ability id). The timestamp makes a
    /// permanent that left and returned (rule 400.7: a new object) get
    /// fresh effects with its new timestamp (rule 613.7a, fix F13).
    static_cache: RefCell<HashMap<StaticKey, Vec<Rc<ContinuousEffect>>>>,
    /// re-entrancy guard of the evaluation
    computing: Cell<bool>,
}

impl std::fmt::Debug for LayerState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("LayerState")
            .field("floating", &self.floating.len())
            .finish_non_exhaustive()
    }
}

/// Fingerprint of what one application changed, comparably (rule
/// 613.8a dependency probing).
#[derive(Debug, PartialEq, Eq)]
struct Delta {
    /// the new name when it changed (`Some("")` is a change to no name)
    name: Option<String>,
    colors_added: Vec<String>,
    colors_removed: Vec<String>,
    types_changed: Vec<String>,
    subtypes_changed: Vec<String>,
    supertypes_changed: Vec<String>,
    power: i64,
    toughness: i64,
    keywords_changed: Vec<String>,
    abilities: i64,
}

fn sym_diff(a: &BTreeSet<String>, b: &BTreeSet<String>) -> Vec<String> {
    a.symmetric_difference(b).cloned().collect()
}

fn delta(before: &Characteristics, after: &Characteristics) -> Delta {
    Delta {
        name: (after.name != before.name).then(|| after.name.clone()),
        colors_added: after.colors.difference(&before.colors).cloned().collect(),
        colors_removed: before.colors.difference(&after.colors).cloned().collect(),
        types_changed: sym_diff(&after.types, &before.types),
        subtypes_changed: sym_diff(&after.subtypes, &before.subtypes),
        supertypes_changed: sym_diff(&after.supertypes, &before.supertypes),
        power: after.power.unwrap_or(0) - before.power.unwrap_or(0),
        toughness: after.toughness.unwrap_or(0) - before.toughness.unwrap_or(0),
        keywords_changed: sym_diff(&after.keywords, &before.keywords),
        abilities: i64::try_from(after.abilities.len()).unwrap_or(i64::MAX)
            - i64::try_from(before.abilities.len()).unwrap_or(i64::MAX),
    }
}

impl Game {
    // -- public API ------------------------------------------------------

    /// The object's current characteristics (rule 613.1): the layer
    /// system's result for a permanent, the base characteristics
    /// elsewhere (copy effects on the stack are out of scope).
    #[must_use]
    pub fn chars(&self, obj: ObjId) -> Rc<Characteristics> {
        let object = self.obj(obj);
        if object.zone != crate::objects::Zone::Battlefield {
            return Rc::clone(&object.base);
        }
        if self.layers.computing.get() {
            // a predicate asked for computed characteristics while they
            // are being computed (the Python evaluator would recurse
            // without end); the base is the best available answer
            return Rc::clone(&object.base);
        }
        let stale = self.layers.cache.borrow().tick != Some(self.tick);
        if stale {
            self.recompute_characteristics();
        }
        self.layers
            .cache
            .borrow()
            .chars
            .get(&obj)
            .map_or_else(|| Rc::clone(&object.base), Rc::clone)
    }

    /// Register a floating (non-static) continuous effect.
    pub fn add_floating_effect(&mut self, mut effect: ContinuousEffect) {
        self.register_effect(&mut effect);
        self.layers.floating.push(Rc::new(effect));
        self.bump();
    }

    /// "Until end of turn" effects end during cleanup (rule 514.2).
    #[rule("514.2")]
    pub fn end_of_turn_cleanup(&mut self) {
        self.layers
            .floating
            .retain(|e| e.duration != Duration::EndOfTurn);
        self.bump();
    }

    /// Drop the cached per-ability effect instances (fixtures use this
    /// after grafting abilities onto an object already on the battlefield).
    pub fn clear_layer_cache(&self) {
        self.layers.static_cache.borrow_mut().clear();
        self.layers.cache.borrow_mut().tick = None;
    }

    /// Assign the effect id and its rule 613.7 timestamp: an effect from
    /// a static ability shares its object's timestamp (rule 613.7b),
    /// floating effects get their own.
    fn register_effect(&self, effect: &mut ContinuousEffect) {
        if effect.id == EffectId(0) {
            effect.id = EffectId(self.next_effect_id());
        }
        if effect.timestamp == 0 {
            effect.timestamp = match (effect.source, effect.duration) {
                (Some(source), Duration::Static) => self.obj(source).timestamp,
                _ => self.next_timestamp(),
            };
        }
    }

    // -- collection ------------------------------------------------------

    /// Continuous effects from static abilities of permanents (rules
    /// 611.3, 604.2). `use_chars` selects which ability set to read:
    /// `None` -> base abilities; otherwise the partially-computed
    /// characteristics (the post-layer-6 re-collection pass, rule 613.6).
    #[rule("611.3", "604.2")]
    fn collect_static(
        &self,
        use_chars: Option<&IndexMap<ObjId, Characteristics>>,
    ) -> Vec<Rc<ContinuousEffect>> {
        let mut out = Vec::new();
        for id in self.battlefield_objects() {
            let object = self.obj(id);
            let abilities = match use_chars {
                Some(chars) => chars[&id].abilities.clone(),
                None => object.base.abilities.clone(),
            };
            for ab in &abilities {
                let Some(stat) = ab.as_static() else { continue };
                let Some(factory) = &stat.continuous else {
                    continue;
                };
                let key = (id, object.timestamp, stat.id);
                if !self.layers.static_cache.borrow().contains_key(&key) {
                    let effects: Vec<Rc<ContinuousEffect>> = factory(self, id)
                        .into_iter()
                        .map(|mut e| {
                            self.register_effect(&mut e);
                            Rc::new(e)
                        })
                        .collect();
                    self.layers.static_cache.borrow_mut().insert(key, effects);
                }
                out.extend(self.layers.static_cache.borrow()[&key].iter().cloned());
            }
        }
        out
    }

    // -- computation -----------------------------------------------------

    /// Recompute all battlefield characteristics in layer order (rules
    /// 613.1, 613.5).
    #[rule("613.1", "613.5")]
    fn recompute_characteristics(&self) {
        self.layers.computing.set(true);
        let objs = self.battlefield_objects();
        let mut chars: IndexMap<ObjId, Characteristics> = objs
            .iter()
            .map(|o| (*o, self.obj(*o).base.copy()))
            .collect();

        let mut effects = self.collect_static(None);
        effects.extend(self.layers.floating.iter().cloned());

        for (layer, sub) in LAYER_ORDER {
            if (layer, sub) == (7, "a") {
                // rule 613.6: abilities added in layer 6 generate effects
                // in later layers; re-collect statics from computed chars
                let seen: HashSet<EffectId> = effects.iter().map(|e| e.id).collect();
                effects.extend(
                    self.collect_static(Some(&chars))
                        .into_iter()
                        .filter(|e| !seen.contains(&e.id)),
                );
            }
            let group: Vec<Rc<ContinuousEffect>> = effects
                .iter()
                .filter(|e| e.layer == layer && e.sublayer == sub)
                .cloned()
                .collect();
            self.apply_group(&group, &objs, &mut chars);
            if (layer, sub) == (7, "c") {
                self.apply_pt_counters(&objs, &mut chars);
            }
        }

        let computed = chars
            .into_iter()
            .map(|(id, ch)| (id, Rc::new(ch)))
            .collect();
        *self.layers.cache.borrow_mut() = CharsCache {
            tick: Some(self.tick),
            chars: computed,
        };
        self.layers.computing.set(false);
    }

    /// Apply one layer's effects (rules 613.7-613.8): timestamp order,
    /// with dependency handling - a dependent effect waits for the effect
    /// it depends on.
    #[rule("613.7", "613.8")]
    fn apply_group(
        &self,
        group: &[Rc<ContinuousEffect>],
        objs: &[ObjId],
        chars: &mut IndexMap<ObjId, Characteristics>,
    ) {
        let mut remaining: Vec<Rc<ContinuousEffect>> = group.to_vec();
        remaining.sort_by_key(|e| (e.timestamp, e.id));
        while !remaining.is_empty() {
            let independent = remaining.iter().position(|cand| {
                !remaining
                    .iter()
                    .filter(|other| !Rc::ptr_eq(other, cand))
                    .any(|other| self.depends(cand, other, objs, chars))
            });
            // rule 613.8c: dependency loop -> timestamp order
            let pick = remaining.remove(independent.unwrap_or(0));
            for o in objs {
                let ch = chars
                    .get_mut(o)
                    .expect("every battlefield object has working chars");
                if (pick.applies_to)(self, *o, ch) {
                    (pick.apply)(self, *o, ch);
                }
            }
        }
    }

    /// Whether applying `b` first changes what `a` does (rule 613.8a).
    #[rule("613.8a")]
    fn depends(
        &self,
        a: &ContinuousEffect,
        b: &ContinuousEffect,
        objs: &[ObjId],
        chars: &IndexMap<ObjId, Characteristics>,
    ) -> bool {
        let snapshot = || -> IndexMap<ObjId, Characteristics> {
            objs.iter().map(|o| (*o, chars[o].copy())).collect()
        };
        let base = snapshot();
        let mut after_b = snapshot();
        for o in objs {
            let ch = after_b.get_mut(o).expect("snapshot");
            if (b.applies_to)(self, *o, ch) {
                (b.apply)(self, *o, ch);
            }
        }
        let set1: Vec<ObjId> = objs
            .iter()
            .copied()
            .filter(|o| (a.applies_to)(self, *o, &base[o]))
            .collect();
        let set2: Vec<ObjId> = objs
            .iter()
            .copied()
            .filter(|o| (a.applies_to)(self, *o, &after_b[o]))
            .collect();
        if set1 != set2 {
            return true;
        }
        // "what the effect does": compare a's delta with and without b,
        // relative to the respective pre-states
        for oid in set1 {
            let mut probe1 = base[&oid].copy();
            let mut probe2 = after_b[&oid].copy();
            (a.apply)(self, oid, &mut probe1);
            (a.apply)(self, oid, &mut probe2);
            if delta(&base[&oid], &probe1) != delta(&after_b[&oid], &probe2) {
                return true;
            }
        }
        false
    }

    /// Apply +1/+1 and -1/-1 counters in layer 7c (rules 613.4, 122.1a).
    /// Rule 613.4c order does not matter: all are additive.
    #[rule("613.4", "122.1a")]
    fn apply_pt_counters(&self, objs: &[ObjId], chars: &mut IndexMap<ObjId, Characteristics>) {
        for o in objs {
            let ch = chars.get_mut(o).expect("working chars");
            if ch.power.is_none() && ch.toughness.is_none() {
                continue;
            }
            let object = self.obj(*o);
            let plus = object.counter("+1/+1");
            let minus = object.counter("-1/-1");
            if plus != 0 || minus != 0 {
                ch.power = Some(ch.power.unwrap_or(0) + plus - minus);
                ch.toughness = Some(ch.toughness.unwrap_or(0) + plus - minus);
            }
        }
    }
}
