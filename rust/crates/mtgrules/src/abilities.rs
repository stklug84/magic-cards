//! Abilities (CR 112, 113, 602, 603, 604, 605) and targeting (CR 115).
//!
//! Four ability kinds (rule 113.3): spell abilities, activated abilities,
//! triggered abilities, and static abilities. Static abilities generate
//! continuous effects (rule 604.1) and/or replacement effects (rule 614).
//!
//! Every ability carries an [`AbilityId`], the identity the Python
//! engine gets from `id(ability)`: the per-permanent caches of the layer
//! and replacement systems are keyed by it, so two abilities with the
//! same text are still two abilities, and the copies of a
//! characteristics set share the same ones.

use std::collections::BTreeSet;
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::effects::{Ctx, Effect};
use crate::events::Event;
use crate::game::Game;
use crate::manasys::{Cost, parse_cost};
use crate::objects::{ObjId, Zone};
use crate::protocols::{ContinuousFactory, InterveningIf, ReplacementFactory, TriggerCondition};
use crate::rule;
use crate::stack::Target;

/// Identity of one ability instance (the Python `id(ability)`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct AbilityId(pub u64);

static NEXT_ABILITY_ID: AtomicU64 = AtomicU64::new(1);

impl AbilityId {
    /// A fresh identity.
    #[must_use]
    pub fn next() -> Self {
        AbilityId(NEXT_ABILITY_ID.fetch_add(1, Ordering::Relaxed))
    }
}

// ---------------------------------------------------------------- targets

/// `count` of a spec that allows "any number of" targets.
pub const ANY_NUMBER: i64 = 99;

/// One target requirement of a spell/ability (rules 115.1, 115.3).
///
/// One instance of the word "target" may ask for several objects ("up to
/// two target creatures", "any number of target ...", "one or two target
/// ..."): `count` is the maximum, [`TargetSpec::minimum`] how many must be
/// chosen. The same object can be chosen only once per spec (rule 115.3).
#[rule("115.1", "115.3")]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TargetSpec {
    /// what may be targeted: creature, permanent, artifact, enchantment,
    /// `art_ench`, `cre_ench`, nonland, player, opponent, spell,
    /// `creature_or_planeswalker`, land, or any (creature, planeswalker
    /// or player)
    pub what: String,
    /// `any` | `opponent` | `you`
    pub controller: String,
    /// the maximum number of targets
    pub count: i64,
    /// "up to N": the minimum is zero
    pub optional: bool,
    /// "another target ..."
    pub other: bool,
    /// how many targets must be chosen; `None` = 0 if optional else
    /// `count`
    pub min_count: Option<i64>,
}

impl TargetSpec {
    /// A single mandatory target of `what`, any controller.
    #[must_use]
    pub fn new(what: &str) -> Self {
        TargetSpec {
            what: what.to_owned(),
            controller: "any".to_owned(),
            count: 1,
            optional: false,
            other: false,
            min_count: None,
        }
    }

    /// The number of targets that must be chosen.
    #[must_use]
    pub fn minimum(&self) -> i64 {
        match self.min_count {
            Some(n) => n,
            None if self.optional => 0,
            None => self.count,
        }
    }

    /// Whether `target` is a legal target right now (rule 115.4).
    #[rule("115.4")]
    #[must_use]
    pub fn legal(&self, game: &Game, ctx: &Ctx, target: Target) -> bool {
        if self.what == "player" || self.what == "opponent" {
            let Target::Player(pid) = target else {
                return false;
            };
            if game.player(pid).lost {
                return false;
            }
            return !(self.what == "opponent" && pid == ctx.controller);
        }
        if self.what == "any"
            && let Target::Player(pid) = target
        {
            return !game.player(pid).lost;
        }
        if self.what == "spell" {
            return match target {
                Target::Spell(id) => game.stack_item(id).is_some_and(|item| item.is_spell),
                _ => false,
            };
        }
        let Target::Object(obj) = target else {
            return false;
        };
        self.legal_object(game, ctx, obj)
    }

    /// Rule 115.4 legality of a battlefield-object target.
    fn legal_object(&self, game: &Game, ctx: &Ctx, target: ObjId) -> bool {
        let obj = game.obj(target);
        if obj.zone != Zone::Battlefield {
            return false;
        }
        if self.other && ctx.source == Some(target) {
            return false;
        }
        let ch = game.chars(target);
        // rule 702.16 protection / hexproof-style grants
        if game.cant_be_targeted(target, ctx) {
            return false;
        }
        if self.controller == "opponent" && obj.controller == ctx.controller {
            return false;
        }
        if self.controller == "you" && obj.controller != ctx.controller {
            return false;
        }
        let has = |kind: &str| ch.types.contains(kind);
        match self.what.as_str() {
            "creature" => has("Creature"),
            "nonland" => !has("Land"),
            "artifact" => has("Artifact"),
            "enchantment" => has("Enchantment"),
            "art_ench" => has("Artifact") || has("Enchantment"),
            "cre_ench" => has("Creature") || has("Enchantment"),
            "creature_or_planeswalker" | "any" => has("Creature") || has("Planeswalker"),
            "land" => has("Land"),
            // "permanent", and anything the compiler may emit that is not
            // narrowed here (the Python dict lookup's default)
            _ => true,
        }
    }
}

// ---------------------------------------------------------------- triggers

/// When/Whenever/At condition of a triggered ability (rule 603.1).
#[rule("603.1")]
#[derive(Clone)]
pub struct TriggerSpec {
    /// the event type that may trigger the ability
    pub event: crate::events::EventType,
    /// `predicate(game, source, event)`; `None` = the default, "the event
    /// is about the ability's source itself"
    pub condition: Option<TriggerCondition>,
    /// whose step for begin-step triggers: `upkeep` | `end` | `combat_begin` | ...
    pub step: String,
}

impl std::fmt::Debug for TriggerSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TriggerSpec")
            .field("event", &self.event)
            .field("condition", &self.condition.as_ref().map(|_| "<fn>"))
            .field("step", &self.step)
            .finish()
    }
}

impl TriggerSpec {
    /// A trigger on `event` with the default "about the source" condition.
    #[must_use]
    pub fn on(event: crate::events::EventType) -> Self {
        TriggerSpec {
            event,
            condition: None,
            step: String::new(),
        }
    }

    /// A trigger on `event` with a condition.
    #[must_use]
    pub fn when(event: crate::events::EventType, condition: TriggerCondition) -> Self {
        TriggerSpec {
            event,
            condition: Some(condition),
            step: String::new(),
        }
    }

    /// Whether `event` makes an ability of `source` with this spec trigger.
    #[must_use]
    pub fn matches(&self, game: &Game, source: ObjId, event: &Event) -> bool {
        if event.kind != self.event {
            return false;
        }
        match &self.condition {
            Some(condition) => condition(game, source, event),
            // default: the event is about the ability's source itself
            None => event.data.obj == Some(source),
        }
    }
}

/// `[Trigger condition], [effect]` (rules 113.3c, 603.1).
#[rule("113.3c", "603.1")]
#[derive(Clone)]
pub struct TriggeredAbility {
    /// identity
    pub id: AbilityId,
    /// the trigger condition
    pub trigger: TriggerSpec,
    /// the effect
    pub effect: Effect,
    /// the targets
    pub targets: Vec<TargetSpec>,
    /// rule 603.4 intervening "if" clause: `pred(game, source)` checked
    /// both at trigger time and on resolution
    pub intervening_if: Option<InterveningIf>,
    /// the oracle text
    pub text: String,
    /// "you may ..."
    pub optional: bool,
    /// "Do this only once each turn."
    pub once_each_turn: bool,
}

impl std::fmt::Debug for TriggeredAbility {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TriggeredAbility")
            .field("id", &self.id)
            .field("trigger", &self.trigger)
            .field("effect", &self.effect)
            .field("targets", &self.targets)
            .field("intervening_if", &self.intervening_if.is_some())
            .field("text", &self.text)
            .field("optional", &self.optional)
            .field("once_each_turn", &self.once_each_turn)
            .finish()
    }
}

impl TriggeredAbility {
    /// A triggered ability with no targets and no clauses.
    #[must_use]
    pub fn new(trigger: TriggerSpec, effect: Effect) -> Self {
        TriggeredAbility {
            id: AbilityId::next(),
            trigger,
            effect,
            targets: Vec::new(),
            intervening_if: None,
            text: String::new(),
            optional: false,
            once_each_turn: false,
        }
    }
}

/// `[Cost]: [Effect]` (rules 113.3b, 602.1).
#[rule("113.3b", "602.1")]
#[derive(Clone, Debug)]
#[expect(
    clippy::struct_excessive_bools,
    reason = "the flags mirror the Python dataclass field for field; each is an independent printed property"
)]
pub struct ActivatedAbility {
    /// identity
    pub id: AbilityId,
    /// the mana cost, e.g. `{2}`
    pub mana_cost: String,
    /// `{T}`, rules 602.5a and 107.5
    pub tap_cost: bool,
    /// a sacrifice cost: "a creature", "another artifact", `~`
    pub sac_cost: String,
    /// a life cost
    pub life_cost: i64,
    /// rule 606; planeswalkers
    pub loyalty_cost: Option<i64>,
    /// the effect (`None` for a cost-only ability)
    pub effect: Option<Effect>,
    /// the targets
    pub targets: Vec<TargetSpec>,
    /// rule 605.1a
    pub is_mana_ability: bool,
    /// "Activate only as a sorcery" (rule 602.5d)
    pub sorcery_only: bool,
    /// once per turn
    pub once_per_turn: bool,
    /// rule 702.29a cycling-style: activated from the hand; the card itself
    /// is discarded as part of the cost
    pub from_hand: bool,
    /// activated while the card is in the graveyard (Reassembling Skeleton)
    pub from_graveyard: bool,
    /// the oracle text
    pub text: String,
}

impl Default for ActivatedAbility {
    fn default() -> Self {
        ActivatedAbility {
            id: AbilityId::next(),
            mana_cost: String::new(),
            tap_cost: false,
            sac_cost: String::new(),
            life_cost: 0,
            loyalty_cost: None,
            effect: None,
            targets: Vec::new(),
            is_mana_ability: false,
            sorcery_only: false,
            once_per_turn: false,
            from_hand: false,
            from_graveyard: false,
            text: String::new(),
        }
    }
}

impl ActivatedAbility {
    /// The parsed mana cost (rule 202.1).
    #[must_use]
    pub fn cost(&self) -> Cost {
        parse_cost(&self.mana_cost)
    }
}

/// The instructions of an instant/sorcery spell (rules 113.3a, 112.1).
#[rule("113.3a", "112.1")]
#[derive(Clone, Debug)]
pub struct SpellAbility {
    /// identity
    pub id: AbilityId,
    /// the effect
    pub effect: Effect,
    /// the targets
    pub targets: Vec<TargetSpec>,
    /// the oracle text
    pub text: String,
}

impl SpellAbility {
    /// A spell ability without targets.
    #[must_use]
    pub fn new(effect: Effect) -> Self {
        SpellAbility {
            id: AbilityId::next(),
            effect,
            targets: Vec::new(),
            text: String::new(),
        }
    }
}

/// A static ability (rules 604.1-604.2): generates continuous and/or
/// replacement effects while its source is on the battlefield.
#[rule("113.3d", "604.1")]
#[derive(Clone)]
pub struct StaticAbility {
    /// identity
    pub id: AbilityId,
    /// `factory(game, source)` -> continuous effects
    pub continuous: Option<ContinuousFactory>,
    /// `factory(game, source)` -> replacement effects
    pub replacement: Option<ReplacementFactory>,
    /// the oracle text
    pub text: String,
    /// "spells you control can't be countered" (Chimil style)
    pub uncounterable_spells: bool,
    /// marker consumed by `Game::play_land`: the land enters tapped
    pub enters_tapped: bool,
}

impl Default for StaticAbility {
    fn default() -> Self {
        StaticAbility {
            id: AbilityId::next(),
            continuous: None,
            replacement: None,
            text: String::new(),
            uncounterable_spells: false,
            enters_tapped: false,
        }
    }
}

impl std::fmt::Debug for StaticAbility {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StaticAbility")
            .field("id", &self.id)
            .field("continuous", &self.continuous.is_some())
            .field("replacement", &self.replacement.is_some())
            .field("text", &self.text)
            .field("uncounterable_spells", &self.uncounterable_spells)
            .field("enters_tapped", &self.enters_tapped)
            .finish()
    }
}

impl StaticAbility {
    /// A static ability generating continuous effects.
    #[must_use]
    pub fn continuous(factory: ContinuousFactory, text: &str) -> Self {
        StaticAbility {
            continuous: Some(factory),
            text: text.to_owned(),
            ..StaticAbility::default()
        }
    }

    /// A static ability generating replacement effects.
    #[must_use]
    pub fn replacement(factory: ReplacementFactory, text: &str) -> Self {
        StaticAbility {
            replacement: Some(factory),
            text: text.to_owned(),
            ..StaticAbility::default()
        }
    }
}

/// Any of the four ability kinds carried by `Characteristics::abilities`.
#[derive(Clone, Debug)]
pub enum Ability {
    /// rule 113.3b
    Activated(ActivatedAbility),
    /// rule 113.3a
    Spell(SpellAbility),
    /// rule 113.3d
    Static(StaticAbility),
    /// rule 113.3c
    Triggered(TriggeredAbility),
}

impl Ability {
    /// The ability's identity.
    #[must_use]
    pub fn id(&self) -> AbilityId {
        match self {
            Ability::Activated(a) => a.id,
            Ability::Spell(a) => a.id,
            Ability::Static(a) => a.id,
            Ability::Triggered(a) => a.id,
        }
    }

    /// The Python `kind` string: `activated` | `spell` | `static` |
    /// `triggered`.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Ability::Activated(_) => "activated",
            Ability::Spell(_) => "spell",
            Ability::Static(_) => "static",
            Ability::Triggered(_) => "triggered",
        }
    }

    /// The oracle text.
    #[must_use]
    pub fn text(&self) -> &str {
        match self {
            Ability::Activated(a) => &a.text,
            Ability::Spell(a) => &a.text,
            Ability::Static(a) => &a.text,
            Ability::Triggered(a) => &a.text,
        }
    }

    /// The static ability, when it is one.
    #[must_use]
    pub fn as_static(&self) -> Option<&StaticAbility> {
        match self {
            Ability::Static(a) => Some(a),
            _ => None,
        }
    }

    /// The activated ability, when it is one.
    #[must_use]
    pub fn as_activated(&self) -> Option<&ActivatedAbility> {
        match self {
            Ability::Activated(a) => Some(a),
            _ => None,
        }
    }

    /// The triggered ability, when it is one.
    #[must_use]
    pub fn as_triggered(&self) -> Option<&TriggeredAbility> {
        match self {
            Ability::Triggered(a) => Some(a),
            _ => None,
        }
    }

    /// The spell ability, when it is one.
    #[must_use]
    pub fn as_spell(&self) -> Option<&SpellAbility> {
        match self {
            Ability::Spell(a) => Some(a),
            _ => None,
        }
    }
}

impl From<ActivatedAbility> for Ability {
    fn from(a: ActivatedAbility) -> Self {
        Ability::Activated(a)
    }
}

impl From<SpellAbility> for Ability {
    fn from(a: SpellAbility) -> Self {
        Ability::Spell(a)
    }
}

impl From<StaticAbility> for Ability {
    fn from(a: StaticAbility) -> Self {
        Ability::Static(a)
    }
}

impl From<TriggeredAbility> for Ability {
    fn from(a: TriggeredAbility) -> Self {
        Ability::Triggered(a)
    }
}

// ---------------------------------------------------------------- tokens

/// A factory of an ability attached to created tokens.
pub type AbilityFactory = Rc<dyn Fn() -> Ability>;

/// The characteristics a token is created with (rules 111.1, 111.4).
#[rule("111.1", "111.4")]
#[derive(Clone)]
pub struct TokenSpec {
    /// the name
    pub name: String,
    /// power
    pub power: Option<i64>,
    /// toughness
    pub toughness: Option<i64>,
    /// colours
    pub colors: BTreeSet<String>,
    /// card types (default: Creature)
    pub types: BTreeSet<String>,
    /// subtypes
    pub subtypes: BTreeSet<String>,
    /// keywords
    pub keywords: BTreeSet<String>,
    /// enters tapped
    pub tapped: bool,
    /// predefined token abilities, e.g. Treasure's sacrifice mana ability:
    /// treasure|clue|food|blood|map
    pub predefined: String,
    /// extra ability factories attached to created tokens
    pub abilities: Vec<AbilityFactory>,
}

impl std::fmt::Debug for TokenSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TokenSpec")
            .field("name", &self.name)
            .field("power", &self.power)
            .field("toughness", &self.toughness)
            .field("colors", &self.colors)
            .field("types", &self.types)
            .field("subtypes", &self.subtypes)
            .field("keywords", &self.keywords)
            .field("tapped", &self.tapped)
            .field("predefined", &self.predefined)
            .field("abilities", &self.abilities.len())
            .finish()
    }
}

impl Default for TokenSpec {
    fn default() -> Self {
        TokenSpec {
            name: String::new(),
            power: None,
            toughness: None,
            colors: BTreeSet::new(),
            types: BTreeSet::from(["Creature".to_owned()]),
            subtypes: BTreeSet::new(),
            keywords: BTreeSet::new(),
            tapped: false,
            predefined: String::new(),
            abilities: Vec::new(),
        }
    }
}

impl TokenSpec {
    /// A creature token with power and toughness.
    #[must_use]
    pub fn creature(name: &str, power: i64, toughness: i64) -> Self {
        TokenSpec {
            name: name.to_owned(),
            power: Some(power),
            toughness: Some(toughness),
            ..TokenSpec::default()
        }
    }

    /// A predefined artifact token (Treasure, Clue, Food).
    fn artifact(name: &str, predefined: &str) -> Self {
        TokenSpec {
            name: name.to_owned(),
            types: BTreeSet::from(["Artifact".to_owned()]),
            subtypes: BTreeSet::from([name.to_owned()]),
            predefined: predefined.to_owned(),
            ..TokenSpec::default()
        }
    }

    /// The Treasure token.
    #[must_use]
    pub fn treasure() -> Self {
        TokenSpec::artifact("Treasure", "treasure")
    }

    /// The Clue token.
    #[must_use]
    pub fn clue() -> Self {
        TokenSpec::artifact("Clue", "clue")
    }

    /// The Food token.
    #[must_use]
    pub fn food() -> Self {
        TokenSpec::artifact("Food", "food")
    }

    /// Whether the token is a creature token.
    #[must_use]
    pub fn is_creature(&self) -> bool {
        self.types.contains("Creature")
    }
}

#[cfg(test)]
mod tests {
    use super::{ANY_NUMBER, Ability, ActivatedAbility, StaticAbility, TargetSpec, TokenSpec};

    #[test]
    fn target_spec_minimum() {
        let mut spec = TargetSpec::new("creature");
        assert_eq!(spec.minimum(), 1);
        spec.optional = true;
        assert_eq!(spec.minimum(), 0);
        spec.count = ANY_NUMBER;
        spec.min_count = Some(2);
        assert_eq!(spec.minimum(), 2);
    }

    #[test]
    fn abilities_have_distinct_identities() {
        let a = Ability::from(ActivatedAbility {
            mana_cost: "{2}".into(),
            ..ActivatedAbility::default()
        });
        let b = Ability::from(StaticAbility::default());
        assert_ne!(a.id(), b.id());
        assert_eq!((a.kind(), b.kind()), ("activated", "static"));
        assert_eq!(a.as_activated().map(|ab| ab.cost().generic), Some(2));
        let copy = a.clone();
        assert_eq!(copy.id(), a.id());
    }

    #[test]
    fn predefined_tokens() {
        let t = TokenSpec::treasure();
        assert_eq!(
            (t.name.as_str(), t.predefined.as_str()),
            ("Treasure", "treasure")
        );
        assert!(!t.is_creature() && t.subtypes.contains("Treasure"));
        assert!(TokenSpec::creature("Soldier", 1, 1).is_creature());
        assert_eq!(
            TokenSpec::food().subtypes.iter().collect::<Vec<_>>(),
            ["Food"]
        );
    }
}
