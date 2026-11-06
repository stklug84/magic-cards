//! The decision interface the engine asks a player's policy through.
//!
//! The Python engine calls methods of a `DefaultPolicy` instance wherever
//! the rules leave a choice to a player. [`Policy`] is that interface as
//! a trait. Its default method bodies are the Python `DefaultPolicy`'s
//! own choices wherever those are plain rules (the legend rule keeps the
//! permanent with the most counters, scry bottoms lands past six, ...),
//! so a game played with [`TrivialPolicy`] decides those exactly like
//! Python. The choices that are board-reading heuristics - what to cast,
//! which targets, attackers and blockers, the land to drop - default to
//! "do nothing" / "the first option" here and are overridden by
//! [`DefaultPolicy`], the port of the Python AI.

mod default_policy;
mod profile;

pub use default_policy::{DefaultPolicy, is_beneficial};
pub use profile::{PROFILE_NAMES, PolicyProfile, get_profile};

use std::rc::Rc;

use crate::abilities::{ActivatedAbility, TargetSpec};
use crate::effects::Ctx;
use crate::events::Event;
use crate::game::Game;
use crate::manasys::parse_cost;
use crate::objects::{Defender, ObjId, PlayerId, Zone};
use crate::replacements::Replacement;
use crate::stack::{PendingTrigger, StackAbility, StackId, Target};

/// A player action during priority (the Python `Action` tuples).
#[derive(Clone, Debug)]
pub enum Action {
    /// `("cast", card, {x, from_command})`
    Cast {
        /// the card
        card: ObjId,
        /// the chosen X
        x: i64,
        /// cast from the command zone (commander tax applies)
        from_command: bool,
    },
    /// `("activate", obj, ability, {x})`
    Activate {
        /// the permanent (or card) whose ability it is
        obj: ObjId,
        /// the ability
        ability: Rc<ActivatedAbility>,
        /// the chosen X
        x: i64,
    },
    /// `("land", card)`
    Land(ObjId),
}

/// scry: a player with this many lands or more bottoms further lands
pub const SCRY_ENOUGH_LANDS: usize = 6;
/// scry: a player with fewer lands than this bottoms non-lands
pub const SCRY_NEED_LANDS: usize = 3;

/// Threat assessment of a permanent: the graph `:threatWeight` hook plus
/// board impact (the Python `_threat`).
#[must_use]
#[expect(
    clippy::cast_precision_loss,
    reason = "the Python formula mixes ints into a float"
)]
pub fn threat(game: &Game, obj: ObjId) -> f64 {
    let ch = game.chars(obj);
    let o = game.obj(obj);
    let key = o.card_ref.as_ref().map_or(0, |r| r.behavior.key());
    let pt = ch.power.unwrap_or(0) + ch.toughness.unwrap_or(0);
    (key * 2) as f64 + pt as f64 * 0.3 + if o.commander { 3.0 } else { 0.0 }
}

/// Threat of a target for ranking (players and spells rank zero): the
/// Python `_obj_threat`.
#[must_use]
pub fn target_threat(game: &Game, target: Target) -> f64 {
    match target {
        Target::Object(o) => threat(game, o),
        _ => 0.0,
    }
}

/// The first element maximising `key` (Python's `max()` tie-break).
pub(crate) fn first_max<T: Copy, K: PartialOrd>(items: &[T], key: impl Fn(T) -> K) -> Option<T> {
    let mut best: Option<(T, K)> = None;
    for &item in items {
        let k = key(item);
        if best.as_ref().is_none_or(|(_, bk)| k > *bk) {
            best = Some((item, k));
        }
    }
    best.map(|(item, _)| item)
}

/// The first element minimising `key` (Python's `min()` tie-break).
pub(crate) fn first_min<T: Copy, K: PartialOrd>(items: &[T], key: impl Fn(T) -> K) -> Option<T> {
    let mut best: Option<(T, K)> = None;
    for &item in items {
        let k = key(item);
        if best.as_ref().is_none_or(|(_, bk)| k < *bk) {
            best = Some((item, k));
        }
    }
    best.map(|(item, _)| item)
}

/// A player's decision policy.
pub trait Policy {
    // ---- the board-reading heuristics (the default policy overrides these)

    /// Rule 103.5: keep the seven-card `hand` after `mulls` counted
    /// mulligans? Default: always.
    fn keep_hand(&self, _game: &Game, _player: PlayerId, _hand: &[ObjId], _mulls: usize) -> bool {
        true
    }

    /// London mulligan (rule 103.5c): the `n` cards of `hand` to put on
    /// the bottom. Default: the first `n`.
    fn bottom_cards(
        &self,
        _game: &Game,
        _player: PlayerId,
        hand: &[ObjId],
        n: usize,
    ) -> Vec<ObjId> {
        hand.iter().take(n).copied().collect()
    }

    /// The action to take with priority, or `None` to pass. Default:
    /// pass.
    fn choose_action(&self, _game: &Game, _player: PlayerId) -> Option<Action> {
        None
    }

    /// Choose the targets of one spec among `legal` (rule 601.2c).
    /// Default: the first `spec.count` legal targets.
    fn choose_targets(
        &self,
        _game: &Game,
        spec: &TargetSpec,
        legal: &[Target],
        _ctx: &Ctx,
        _ability: &StackAbility,
    ) -> Vec<Target> {
        legal
            .iter()
            .take(usize::try_from(spec.count).unwrap_or(usize::MAX))
            .copied()
            .collect()
    }

    /// Declare attackers: `(creature, defender)` pairs (rule 508.1).
    /// Default: no attack.
    fn declare_attackers(
        &self,
        _game: &Game,
        _player: PlayerId,
        _candidates: &[ObjId],
    ) -> Vec<(ObjId, Defender)> {
        Vec::new()
    }

    /// Declare blockers: `(blocker, attacker)` pairs (rule 509.1).
    /// Default: no blocks.
    fn declare_blockers(
        &self,
        _game: &Game,
        _player: PlayerId,
        _attackers: &[ObjId],
        _blockers: &[ObjId],
    ) -> Vec<(ObjId, ObjId)> {
        Vec::new()
    }

    /// The land to put onto the battlefield from a hand. Default: the
    /// first.
    fn best_land(&self, _game: &Game, _player: PlayerId, lands: &[ObjId]) -> Option<ObjId> {
        lands.first().copied()
    }

    /// The land a bounce land returns to its controller's hand. Default:
    /// the first.
    fn choose_bounce_land(&self, _game: &Game, lands: &[ObjId]) -> Option<ObjId> {
        lands.first().copied()
    }

    // ---- the rule-based choices (the Python DefaultPolicy's, exactly)

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

    /// Order applicable replacement effects (rule 616.1): the first.
    fn choose_replacement(
        &self,
        _game: &Game,
        _event: &Event,
        candidates: &[Rc<Replacement>],
    ) -> Rc<Replacement> {
        Rc::clone(&candidates[0])
    }

    /// The colour an any-colour source produces: the first allowed, `C`
    /// when none is.
    fn choose_mana_color(&self, _game: &Game, _ctx: &Ctx, allowed: &str) -> String {
        allowed
            .chars()
            .next()
            .map_or_else(|| "C".to_owned(), |c| c.to_string())
    }

    /// The cleanup-step discard: the card with the highest mana value.
    fn choose_discard(&self, game: &Game, player: PlayerId) -> Option<ObjId> {
        first_max(&game.player(player).hand, |c| {
            parse_cost(&game.obj(c).base.mana_cost).mv()
        })
    }

    /// The cheapest (least threatening) permanent matching a sacrifice
    /// selector (`"a creature"`, `"another artifact"`, ...).
    fn choose_sacrifice(
        &self,
        game: &Game,
        player: PlayerId,
        selector: &str,
        exclude: Option<ObjId>,
    ) -> Option<ObjId> {
        let pool: Vec<ObjId> = game
            .player(player)
            .battlefield
            .iter()
            .copied()
            .filter(|o| Some(*o) != exclude)
            .filter(|o| {
                let ch = game.chars(*o);
                (!selector.contains("creature") || ch.types.contains("Creature"))
                    && (!selector.contains("artifact") || ch.types.contains("Artifact"))
            })
            .collect();
        first_min(&pool, |o| threat(game, o))
    }

    /// Legend rule keep choice: the one with the most counters survives
    /// (rule 704.5j).
    fn choose_legend(&self, game: &Game, objs: &[ObjId]) -> ObjId {
        first_max(objs, |o| game.obj(o).counters.values().sum::<i64>())
            .expect("at least two legends")
    }

    /// Send a dying commander to the command zone (rule 903.9): yes.
    fn commander_to_command_zone(&self, _game: &Game, _obj: ObjId, _to_zone: Zone) -> bool {
        true
    }

    /// Pay ward taxes whenever the mana is available (rule 702.21): yes.
    fn pay_ward(&self, _game: &Game, _item: StackId, _n: i64) -> bool {
        true
    }

    /// Divide `total` damage among `targets` (rule 601.2d): at least 1
    /// each, then lethal to the biggest threats first; whatever is left
    /// goes to the last target in threat order (a player takes it all).
    fn divide_damage(&self, game: &Game, _ctx: &Ctx, total: i64, targets: &[Target]) -> Vec<i64> {
        if targets.is_empty() {
            return Vec::new();
        }
        let n = i64::try_from(targets.len()).unwrap_or(i64::MAX);
        let mut shares = vec![1_i64; targets.len()];
        let mut left = total - n;
        let mut order: Vec<usize> = (0..targets.len()).collect();
        // stable sort by -(threat), players ranking as -(-1.0)
        order.sort_by(|a, b| {
            let key = |i: usize| match targets[i] {
                Target::Object(o) => -threat(game, o),
                _ => 1.0,
            };
            key(*a)
                .partial_cmp(&key(*b))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for &i in &order {
            if left <= 0 {
                break;
            }
            let add = match targets[i] {
                Target::Object(o) => {
                    let toughness = game.chars(o).toughness.filter(|t| *t != 0).unwrap_or(1);
                    let need = (toughness - game.obj(o).damage - shares[i]).max(0);
                    left.min(need)
                }
                _ => left,
            };
            shares[i] += add;
            left -= add;
        }
        if left > 0 {
            shares[*order.last().expect("non-empty")] += left;
        }
        shares
    }

    /// Proliferate recipients that help us / hurt opponents (rule 702.87).
    fn choose_proliferate(&self, game: &Game, player: PlayerId) -> Vec<ObjId> {
        game.battlefield_objects()
            .into_iter()
            .filter(|o| {
                let obj = game.obj(*o);
                if obj.counters.is_empty() {
                    return false;
                }
                let good = obj.counter("+1/+1") + obj.counter("loyalty") + obj.counter("charge");
                let bad = obj.counter("-1/-1");
                (obj.controller == player && good > bad) || (obj.controller != player && bad > good)
            })
            .collect()
    }

    /// The biggest creature token to copy (rule 701.34).
    fn choose_populate(&self, game: &Game, tokens: &[ObjId]) -> Option<ObjId> {
        first_max(tokens, |o| game.obj(o).base.power.unwrap_or(0))
    }

    /// Scry choice: `(keep on top, bottom)` based on land needs.
    fn scry(&self, game: &Game, player: PlayerId, top: &[ObjId]) -> (Vec<ObjId>, Vec<ObjId>) {
        let lands_bf = game
            .player(player)
            .battlefield
            .iter()
            .filter(|o| game.obj(**o).base.types.contains("Land"))
            .count();
        let (mut keep, mut bottom) = (Vec::new(), Vec::new());
        for &card in top {
            let is_land = game.obj(card).base.types.contains("Land");
            if (is_land && lands_bf >= SCRY_ENOUGH_LANDS)
                || (!is_land && lands_bf < SCRY_NEED_LANDS)
            {
                bottom.push(card);
            } else {
                keep.push(card);
            }
        }
        (keep, bottom)
    }

    /// Tutor pick: the highest key-weight card in the library.
    fn choose_tutor_card(&self, game: &Game, player: PlayerId) -> Option<ObjId> {
        first_max(&game.player(player).library, |c| {
            game.obj(c)
                .card_ref
                .as_ref()
                .map_or(0, |r| r.behavior.key())
        })
    }
}

/// The policy of every default choice: the fixture of the engine tests
/// (whose Python counterparts run the `DefaultPolicy` whose rule-based
/// choices these are).
#[derive(Debug, Default, Clone, Copy)]
pub struct TrivialPolicy;

impl Policy for TrivialPolicy {}
