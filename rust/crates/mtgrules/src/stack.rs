//! Stack records: spells and abilities awaiting resolution (CR 405).
//!
//! Split out of the game module so that lower-level modules (effects,
//! abilities, policy) can name stack entries without the game machinery.

use std::rc::Rc;

use crate::abilities::{ActivatedAbility, SpellAbility, TargetSpec, TriggeredAbility};
use crate::effects::Effect;
use crate::events::Event;
use crate::objects::{ObjId, PlayerId};
use crate::rule;

/// Handle of a [`StackItem`] (identity for `target in game.stack`
/// membership and spell targets), numbered from 1 per game.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct StackId(pub u32);

/// Anything a spell or ability can target (rule 115.1): a permanent, a
/// player, or a spell on the stack.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Target {
    /// a game object
    Object(ObjId),
    /// a player
    Player(PlayerId),
    /// a spell or ability on the stack
    Spell(StackId),
}

impl Target {
    /// The object, when the target is one.
    #[must_use]
    pub fn object(self) -> Option<ObjId> {
        match self {
            Target::Object(id) => Some(id),
            _ => None,
        }
    }

    /// The player, when the target is one.
    #[must_use]
    pub fn player(self) -> Option<PlayerId> {
        match self {
            Target::Player(id) => Some(id),
            _ => None,
        }
    }

    /// The stack item, when the target is one.
    #[must_use]
    pub fn spell(self) -> Option<StackId> {
        match self {
            Target::Spell(id) => Some(id),
            _ => None,
        }
    }
}

impl From<ObjId> for Target {
    fn from(id: ObjId) -> Self {
        Target::Object(id)
    }
}

impl From<PlayerId> for Target {
    fn from(id: PlayerId) -> Self {
        Target::Player(id)
    }
}

/// A triggered ability waiting to be put on the stack (rule 603.3).
#[derive(Clone, Debug)]
pub struct PendingTrigger {
    /// the ability
    pub ability: Rc<TriggeredAbility>,
    /// its source object
    pub source: ObjId,
    /// its controller
    pub controller: PlayerId,
    /// the triggering event (rule 603.10a look-back reads its object)
    pub event: Event,
}

/// What a stack item is: the spell card, the pending trigger, or the
/// activated ability itself.
#[derive(Clone, Debug)]
pub enum StackObject {
    /// a spell: the card (or copy) on the stack
    Spell(ObjId),
    /// a triggered ability
    Trigger(Box<PendingTrigger>),
    /// an activated ability
    Activated(Rc<ActivatedAbility>),
}

/// The ability a stack item resolves: the spell's, the activated or the
/// triggered one.
#[derive(Clone, Debug)]
pub enum StackAbility {
    /// an instant/sorcery's spell ability (rule 113.3a)
    Spell(Rc<SpellAbility>),
    /// an activated ability (rule 113.3b)
    Activated(Rc<ActivatedAbility>),
    /// a triggered ability (rule 113.3c)
    Triggered(Rc<TriggeredAbility>),
}

impl StackAbility {
    /// The target requirements of the ability.
    #[must_use]
    pub fn targets(&self) -> &[TargetSpec] {
        match self {
            StackAbility::Spell(a) => &a.targets,
            StackAbility::Activated(a) => &a.targets,
            StackAbility::Triggered(a) => &a.targets,
        }
    }

    /// The effect the ability resolves (`None` for a cost-only activated
    /// ability).
    #[must_use]
    pub fn effect(&self) -> Option<&Effect> {
        match self {
            StackAbility::Spell(a) => Some(&a.effect),
            StackAbility::Activated(a) => a.effect.as_ref(),
            StackAbility::Triggered(a) => Some(&a.effect),
        }
    }
}

/// A spell or ability on the stack (rules 405.1-405.2).
#[rule("405.1", "405.2")]
#[derive(Clone, Debug)]
pub struct StackItem {
    /// identity of this entry
    pub id: StackId,
    /// the spell card, the pending trigger, or the activated ability
    pub obj: StackObject,
    /// for abilities: their source object; for spells: the card itself
    pub source: ObjId,
    /// the controller
    pub controller: PlayerId,
    /// the ability being resolved (`None` for a permanent spell)
    pub ability: Option<StackAbility>,
    /// every chosen target, in choice order (the flattening of
    /// `target_groups`; kept for ward checks, Aura attachment, logging)
    pub targets: Vec<Target>,
    /// the chosen targets per `TargetSpec` of the ability (rule 601.2c);
    /// empty when the ability has no targets
    pub target_groups: Vec<Vec<Target>>,
    /// rule 601.2d: the announced division of a "divided as you choose"
    /// amount, aligned with the targets of the dividing effect's spec
    pub division: Vec<i64>,
    /// the chosen X
    pub x: i64,
    /// whether the item is a spell (else an ability)
    pub is_spell: bool,
}

impl StackItem {
    /// The pending trigger, for a triggered ability on the stack.
    #[must_use]
    pub fn trigger(&self) -> Option<&PendingTrigger> {
        match &self.obj {
            StackObject::Trigger(t) => Some(t),
            _ => None,
        }
    }
}
