//! The decision interface the engine asks a player's policy through.
//!
//! The Python engine calls methods of a `DefaultPolicy` instance wherever
//! the rules leave a choice to a player (rule 616.1 replacement order,
//! the colour of "any color" mana, targets, attackers, ...). [`Policy`] is
//! that interface as a trait; the default method bodies are the
//! choices the Python class makes trivially, so a test policy need not
//! implement anything. The full `DefaultPolicy` (the AI) lands in M7 and
//! overrides the methods that reason about the board.

use std::rc::Rc;

use crate::effects::Ctx;
use crate::events::Event;
use crate::game::Game;
use crate::objects::{ObjId, PlayerId};
use crate::replacements::Replacement;
use crate::stack::PendingTrigger;

/// A player's decision policy.
pub trait Policy {
    /// Order applicable replacement effects (rule 616.1): the first.
    fn choose_replacement(
        &self,
        _game: &Game,
        _event: &Event,
        candidates: &[Rc<Replacement>],
    ) -> Rc<Replacement> {
        Rc::clone(&candidates[0])
    }

    /// Pick the colour an any-colour source produces: the first allowed,
    /// `C` when none is.
    fn choose_mana_color(&self, _game: &Game, _ctx: &Ctx, allowed: &str) -> String {
        allowed
            .chars()
            .next()
            .map_or_else(|| "C".to_owned(), |c| c.to_string())
    }

    /// Order the player's own simultaneous triggers (rule 603.3b): as
    /// they came.
    fn order_triggers(
        &self,
        _game: &Game,
        _player: PlayerId,
        triggers: Vec<PendingTrigger>,
    ) -> Vec<PendingTrigger> {
        triggers
    }

    /// Whether to take a "you may" trigger: always.
    fn accept_optional(&self, _game: &Game, _trigger: &PendingTrigger) -> bool {
        true
    }

    /// Pick the land a bounce land returns: the first candidate.
    fn choose_bounce_land(&self, _game: &Game, lands: &[ObjId]) -> Option<ObjId> {
        lands.first().copied()
    }

    /// Pick the land to put onto the battlefield from a hand: the first.
    fn best_land(&self, _game: &Game, _player: PlayerId, lands: &[ObjId]) -> Option<ObjId> {
        lands.first().copied()
    }
}

/// The trivial policy: every default choice (the fixture of the engine
/// tests, where the Python tests use `DefaultPolicy` whose trivial
/// methods it matches).
#[derive(Debug, Default, Clone, Copy)]
pub struct TrivialPolicy;

impl Policy for TrivialPolicy {}
