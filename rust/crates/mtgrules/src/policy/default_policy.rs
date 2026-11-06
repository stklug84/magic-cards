//! The built-in decision policy over engine-legal actions (the Python
//! `DefaultPolicy`).
//!
//! The rules engine enumerates *legal* options; the policy chooses among
//! them. The AI can only do what the CR machinery permits (priority
//! windows, timing, costs).

use std::rc::Rc;

use super::{Action, Policy, PolicyProfile, first_max, first_min, target_threat, threat};
use crate::Game;
use crate::abilities::{ActivatedAbility, SpellAbility, TargetSpec};
use crate::combat::can_block;
use crate::effects::{Ctx, Effect, count_value};
use crate::manasys::{Cost, parse_cost};
use crate::objects::{Defender, ObjId, PlayerId, Zone};
use crate::rule;
use crate::stack::{StackAbility, StackItem, StackObject, Target};

/// X spells are held until X is at least this large.
const MIN_WORTHWHILE_X: i64 = 2;
/// Land count where excess lands become cycling fodder.
const FLOODED_LANDS: usize = 6;
/// Land count where anything spare becomes cycling fodder.
const VERY_FLOODED_LANDS: usize = 8;
/// Hold reactive counterspell mana from this turn on.
const HOLD_REACTIVE_FROM_TURN: u32 = 4;
/// Creatures at least this big attack regardless of blockers.
const BIG_ATTACKER_POWER: i64 = 5;
/// Rule 702.111b menace: can't be blocked by exactly one creature.
const MENACE_MIN_BLOCKERS: usize = 2;
/// The largest X the policy tries.
const MAX_X: i64 = 12;

/// The built-in decision policy over engine-legal actions.
#[derive(Clone, Debug, Default)]
pub struct DefaultPolicy {
    /// the knob profile
    pub profile: PolicyProfile,
}

/// A castable hand spell: `(value, card, x)`.
type Candidate = (f64, ObjId, i64);

/// A minimal resolution context for legality probing.
fn probe_ctx(player: PlayerId, source: ObjId) -> Ctx {
    Ctx::new(player, Some(source))
}

/// The parts of an effect (a `Sequence` flattened one level).
fn parts(effect: Option<&Effect>) -> Vec<&Effect> {
    match effect {
        Some(Effect::Sequence(items)) => items.iter().collect(),
        Some(other) => vec![other],
        None => Vec::new(),
    }
}

/// The spell ability of a card's base characteristics, if any.
fn spell_ability(game: &Game, card: ObjId) -> Option<Rc<SpellAbility>> {
    game.obj(card)
        .base
        .abilities
        .iter()
        .find_map(|a| a.as_spell().cloned())
}

/// Whether a targeted effect helps its target: pump, blink and untap
/// point at our own permanents, harmful effects at the opponent's.
#[must_use]
pub fn is_beneficial(effect: Option<&Effect>) -> bool {
    parts(effect).iter().any(|p| match p {
        Effect::Blink { .. } => true,
        Effect::Untap { on } => on != "self",
        Effect::PutCounters { kind, .. } => kind == "+1/+1",
        _ => false,
    })
}

/// The total of a "divided as you choose" damage effect, if any.
fn divided_amount(game: &Game, ctx: &Ctx, ability: &StackAbility) -> Option<i64> {
    parts(ability.effect()).into_iter().find_map(|p| match p {
        Effect::DealDamage { amount, to, .. } if to == "divided" => {
            Some(count_value(amount, game, ctx))
        }
        _ => None,
    })
}

/// Rate a stack spell by mana value including X.
fn spell_value(game: &Game, item: &StackItem) -> i64 {
    match item.obj {
        StackObject::Spell(obj) => parse_cost(&game.obj(obj).base.mana_cost).mv() + item.x,
        _ => item.x,
    }
}

/// The key weight of a card's graph annotation.
fn key_of(game: &Game, obj: ObjId) -> i64 {
    game.obj(obj)
        .card_ref
        .as_ref()
        .map_or(0, |r| r.behavior.key())
}

fn is_land(game: &Game, obj: ObjId) -> bool {
    game.obj(obj).base.types.contains("Land")
}

fn power(game: &Game, obj: ObjId) -> i64 {
    game.chars(obj).power.unwrap_or(0)
}

fn toughness(game: &Game, obj: ObjId) -> i64 {
    game.chars(obj).toughness.unwrap_or(0)
}

fn has_keyword(game: &Game, obj: ObjId, keyword: &str) -> bool {
    game.chars(obj).keywords.contains(keyword)
}

/// The untapped creatures of a player (potential blockers).
fn untapped_creatures(game: &Game, player: PlayerId) -> Vec<ObjId> {
    game.player(player)
        .battlefield
        .iter()
        .copied()
        .filter(|o| game.chars(*o).types.contains("Creature") && !game.obj(*o).tapped)
        .collect()
}

#[expect(
    clippy::cast_precision_loss,
    reason = "the Python heuristics mix ints into floats"
)]
fn float(n: i64) -> f64 {
    n as f64
}

impl DefaultPolicy {
    /// A policy with the default profile.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// A policy with `profile`.
    #[must_use]
    pub fn with_profile(profile: PolicyProfile) -> Self {
        DefaultPolicy { profile }
    }

    // ------------------------------------------------------- mulligans

    /// Keep priority of one card during mulligan bottoming.
    fn keep_rank(game: &Game, card: ObjId) -> f64 {
        let base = &game.obj(card).base;
        if base.types.contains("Land") {
            return 10.0;
        }
        let mv = parse_cost(&base.mana_cost).mv();
        -float((mv - 3).abs())
    }

    // ------------------------------------------------------- priority

    /// Pick the sorcery-speed play: land, commander, best spell, ability.
    fn main_phase_action(&self, game: &Game, player: PlayerId) -> Option<Action> {
        let p = game.player(player);
        // 1. land drop
        if p.lands_played < 1 {
            let lands: Vec<ObjId> = p
                .hand
                .iter()
                .copied()
                .filter(|c| is_land(game, *c))
                .collect();
            if !lands.is_empty() {
                return Some(Action::Land(self.best_land(game, player, &lands)?));
            }
        }
        // 2. commander
        if let Some(cmd) = p.commander_obj
            && game.obj(cmd).zone == Zone::Command
        {
            let cost =
                parse_cost(&game.obj(cmd).base.mana_cost).with_extra_generic(2 * p.commander_casts);
            if game.can_cast_now(player, cmd) && game.can_pay_mana(player, &cost) {
                return Some(Action::Cast {
                    card: cmd,
                    x: 0,
                    from_command: true,
                });
            }
        }
        // 3. best castable spell (sorcery speed)
        if let Some((value, card, x)) =
            Self::best_candidate(self.castable_spells(game, player, false))
            && value > 0.0
        {
            return Some(Action::Cast {
                card,
                x,
                from_command: false,
            });
        }
        // 4. useful activated ability; else 5. cycle dead cards (rule
        // 702.29): excess lands, or anything when flooded and out of plays
        Self::find_activation(game, player).or_else(|| Self::find_cycling(game, player))
    }

    /// The best candidate: a stable sort by descending value, the first.
    fn best_candidate(mut castable: Vec<Candidate>) -> Option<Candidate> {
        castable.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        castable.into_iter().next()
    }

    /// Instant-speed response: counter a worthwhile opposing spell.
    fn respond_to_stack(&self, game: &Game, player: PlayerId) -> Option<Action> {
        let top = game.stack.last()?;
        if top.controller != player && top.is_spell {
            let counter = Self::find_counterspell(game, player);
            if counter.is_some() && spell_value(game, top) >= self.profile.counter_value_threshold {
                return counter;
            }
        }
        None
    }

    /// P4: cast the best flash creature / non-counter instant right now.
    ///
    /// Used at the end of an opponent's turn: whatever mana is not spent
    /// here is lost at cleanup, so the reaction reserve is ignored.
    #[rule("702.8a")]
    fn instant_speed_play(&self, game: &Game, player: PlayerId) -> Option<Action> {
        let (value, card, x) = Self::best_candidate(self.castable_spells(game, player, true))?;
        if value <= 0.0 {
            return None;
        }
        Some(Action::Cast {
            card,
            x,
            from_command: false,
        })
    }

    /// All castable hand spells as `(value, card, x)` candidates.
    ///
    /// Every candidate passes the engine's own checks (P1): timing, the
    /// additional cost, mana, and a legal target for every required
    /// target. With `instant_speed` only cards castable right now
    /// (instants, flash) are considered and no reaction mana is reserved.
    fn castable_spells(
        &self,
        game: &Game,
        player: PlayerId,
        instant_speed: bool,
    ) -> Vec<Candidate> {
        let reserve = if instant_speed {
            0
        } else {
            self.reaction_reserve(game, player)
        };
        let mut castable = Vec::new();
        for &card in &game.player(player).hand {
            let ch = Rc::clone(&game.obj(card).base);
            if ch.types.contains("Land") || !game.can_cast_now(player, card) {
                continue;
            }
            if !Self::additional_cost_payable(game, player, card) {
                continue;
            }
            let cost = parse_cost(&ch.mana_cost);
            let mut x = 0;
            if cost.x_count > 0 {
                x = Self::max_x(game, player, &cost);
                if x < MIN_WORTHWHILE_X {
                    continue;
                }
            }
            if !game.can_pay_mana(player, &cost.with_x(x)) {
                continue;
            }
            if let Some(sa) = spell_ability(game, card)
                && Self::required_target_missing(game, player, card, &sa.targets)
            {
                continue; // rule 601.2c: a required target is unavailable
            }
            if Self::is_counterspell(game, card) {
                continue; // hold for responses
            }
            if reserve > 0
                && !game.can_pay_mana(player, &cost.with_x(x).with_extra_generic(reserve))
            {
                continue; // keep reaction mana up
            }
            castable.push((self.cast_value(game, player, card, x), card, x));
        }
        castable
    }

    /// Whether a required target of `specs` has no legal choice.
    fn required_target_missing(
        game: &Game,
        player: PlayerId,
        source: ObjId,
        specs: &[TargetSpec],
    ) -> bool {
        specs.iter().any(|s| {
            s.minimum() > 0 && game.legal_targets(s, &probe_ctx(player, source)).is_empty()
        })
    }

    /// Whether the card's rule 601.2b additional cost can be paid now.
    fn additional_cost_payable(game: &Game, player: PlayerId, card: ObjId) -> bool {
        let p = game.player(player);
        match game.obj(card).base.additional_cost.as_str() {
            "sacrifice_creature" => p
                .battlefield
                .iter()
                .any(|o| game.obj(*o).base.types.contains("Creature")),
            "discard_card" => p.hand.iter().any(|c| *c != card),
            _ => true,
        }
    }

    /// Cycle a dead card when flooded (rule 702.29).
    fn find_cycling(game: &Game, player: PlayerId) -> Option<Action> {
        let p = game.player(player);
        let cyclers: Vec<(ObjId, Rc<ActivatedAbility>)> = p
            .hand
            .iter()
            .flat_map(|c| {
                game.obj(*c)
                    .base
                    .abilities
                    .iter()
                    .filter_map(|a| a.as_activated().filter(|ab| ab.from_hand).cloned())
                    .map(move |ab| (*c, ab))
                    .collect::<Vec<_>>()
            })
            .collect();
        if cyclers.is_empty() {
            return None;
        }
        let lands_bf = p.battlefield.iter().filter(|o| is_land(game, **o)).count();
        for (card, ab) in cyclers {
            if !game.can_pay_mana(player, &ab.cost()) {
                continue;
            }
            let land = is_land(game, card);
            let action = Action::Activate {
                obj: card,
                ability: Rc::clone(&ab),
                x: 0,
            };
            if land && lands_bf >= FLOODED_LANDS && p.lands_played >= 1 {
                return Some(action);
            }
            if !land && lands_bf >= VERY_FLOODED_LANDS {
                return Some(action);
            }
        }
        None
    }

    /// Mana value to keep available for a held counterspell.
    ///
    /// Controlled by `profile.hold_reactive_mana`; the heuristic engine
    /// reserved from turn 4 onward.
    fn reaction_reserve(&self, game: &Game, player: PlayerId) -> i64 {
        if !self.profile.hold_reactive_mana || game.turn < HOLD_REACTIVE_FROM_TURN {
            return 0;
        }
        let mut best = 0;
        for &card in &game.player(player).hand {
            let base = &game.obj(card).base;
            if base.types.contains("Instant") && Self::is_counterspell(game, card) {
                let mv = parse_cost(&base.mana_cost).mv();
                if best == 0 || mv < best {
                    best = mv;
                }
            }
        }
        best
    }

    /// Wipe-relevant board value: creature power plus token count and
    /// annotated key-permanent weights (politics beyond raw power).
    fn board_threat(game: &Game, player: PlayerId) -> f64 {
        let (mut power_sum, mut tokens, mut keys) = (0_i64, 0_i64, 0_i64);
        for &o in &game.player(player).battlefield {
            keys += key_of(game, o);
            if !game.obj(o).base.types.contains("Creature") {
                continue;
            }
            power_sum += power(game, o);
            if game.obj(o).is_token {
                tokens += 1;
            }
        }
        float(power_sum) + float(tokens) * 0.5 + float(keys) * 1.5
    }

    /// Archenemy assessment of one opponent: board value, life lead, and
    /// how close the player is to a commander-damage kill.
    #[must_use]
    pub fn player_threat(game: &Game, opp: PlayerId) -> f64 {
        let mut t = Self::board_threat(game, opp);
        t += float((game.player(opp).life - 30).max(0)) * 0.15;
        if let Some(cmd) = game.player(opp).commander_obj {
            let best = game
                .players
                .iter()
                .filter(|p| p.id != opp)
                .map(|p| p.commander_damage.get(&cmd).copied().unwrap_or(0))
                .max()
                .unwrap_or(0);
            t += float(best) * 0.3; // 21-damage clock
        }
        t
    }

    /// Whether a board wipe is worth casting now: only when this far
    /// behind on board value (`profile.wipe_board_deficit`); token swarms
    /// and key permanents count, not just raw power.
    fn should_wipe(&self, game: &Game, player: PlayerId) -> bool {
        let worst = game
            .opponents(player)
            .into_iter()
            .map(|o| Self::board_threat(game, o))
            .fold(0.0_f64, f64::max);
        let deficit = worst - Self::board_threat(game, player);
        deficit >= float(self.profile.wipe_board_deficit)
    }

    /// Find a castable counterspell in hand, as a cast action.
    fn find_counterspell(game: &Game, player: PlayerId) -> Option<Action> {
        for &card in &game.player(player).hand {
            let base = &game.obj(card).base;
            if !base.types.contains("Instant") {
                continue;
            }
            if spell_ability(game, card).is_none() || !Self::is_counterspell(game, card) {
                continue;
            }
            if game.can_pay_mana(player, &parse_cost(&base.mana_cost)) {
                return Some(Action::Cast {
                    card,
                    x: 0,
                    from_command: false,
                });
            }
        }
        None
    }

    /// Whether the card's spell ability counters a spell.
    fn is_counterspell(game: &Game, card: ObjId) -> bool {
        spell_ability(game, card).is_some_and(|sa| {
            parts(Some(&sa.effect))
                .iter()
                .any(|p| matches!(p, Effect::CounterSpell))
        })
    }

    /// The largest payable X (searched from 12 down).
    fn max_x(game: &Game, player: PlayerId, cost: &Cost) -> i64 {
        (1..=MAX_X)
            .rev()
            .find(|x| game.can_pay_mana(player, &cost.with_x(*x)))
            .unwrap_or(0)
    }

    /// Heuristic value of casting `card` now; negative = hold it.
    fn cast_value(&self, game: &Game, player: PlayerId, card: ObjId, x: i64) -> f64 {
        let o = game.obj(card);
        let ch = Rc::clone(&o.base);
        let mv = parse_cost(&ch.mana_cost).mv() + x;
        let mut v = 1.0 + float(mv) * 0.3;
        if let Some(card_ref) = &o.card_ref {
            v += float(card_ref.behavior.key()) * 0.5;
            if card_ref.behavior.wipe() {
                if !self.should_wipe(game, player) {
                    return -1.0; // hold the wipe
                }
                v += 3.0;
            }
        }
        if ch.types.contains("Creature") || ch.types.contains("Planeswalker") {
            v += 1.5;
        }
        // removal/wipes only when there are worthwhile targets
        if let Some(sa) = spell_ability(game, card)
            && !sa.targets.is_empty()
        {
            let mut best = 0.0_f64;
            for spec in &sa.targets {
                let legal = game.legal_targets(spec, &probe_ctx(player, card));
                let enemy = legal.iter().filter_map(|t| match t {
                    Target::Object(obj) if game.obj(*obj).controller != player => Some(*obj),
                    _ => None,
                });
                for e in enemy {
                    best = best.max(threat(game, e));
                }
            }
            if best < self.profile.removal_key_threshold {
                return -1.0;
            }
            v += best * 0.4;
        }
        v
    }

    /// Find a useful non-mana activated ability to activate.
    fn find_activation(game: &Game, player: PlayerId) -> Option<Action> {
        for &obj in &game.player(player).battlefield {
            let ch = game.chars(obj);
            for ab in &ch.abilities {
                let Some(ab) = ab.as_activated() else {
                    continue;
                };
                if ab.is_mana_ability || ab.effect.is_none() {
                    continue;
                }
                if let Some(action) = Self::activation_action(game, player, obj, ab) {
                    return Some(action);
                }
            }
        }
        None
    }

    /// Preference (not legality): never pay loyalty down to zero.
    fn keeps_walker_alive(game: &Game, obj: ObjId, ab: &ActivatedAbility) -> bool {
        match ab.loyalty_cost {
            Some(cost) if cost < 0 => game.obj(obj).counter("loyalty") + cost > 0,
            _ => true,
        }
    }

    /// Whether one ability is worth (and legal) activating now.
    ///
    /// Legality (timing, once per turn, tap, summoning sickness, loyalty,
    /// life) is the engine's verdict (P1/P2); the policy adds its own
    /// preferences on top: no sacrifice costs without intent, keep
    /// planeswalkers alive, spend mana on abilities only in main2, and a
    /// legal target for every required target.
    fn activation_action(
        game: &Game,
        player: PlayerId,
        obj: ObjId,
        ab: &Rc<ActivatedAbility>,
    ) -> Option<Action> {
        if !game.activation_legal(player, obj, ab) {
            return None;
        }
        let worth = if ab.loyalty_cost.is_some() {
            Self::keeps_walker_alive(game, obj, ab)
        } else {
            let cost = ab.cost();
            let mv = cost.mv();
            ab.sac_cost.is_empty()
                && (mv > 0 || ab.tap_cost)
                && (game.phase == "main2" || mv == 0)
                && (mv == 0 || game.can_pay_mana(player, &cost))
                && !Self::required_target_missing(game, player, obj, &ab.targets)
        };
        worth.then(|| Action::Activate {
            obj,
            ability: Rc::clone(ab),
            x: 0,
        })
    }
}

// ------------------------------------------------------------ choices

impl DefaultPolicy {
    /// Targets of a "divided" damage effect: kills first, then the face.
    ///
    /// Enemy creatures by threat, each only if the damage left after the
    /// earlier picks is still lethal to it; whatever is left goes to the
    /// lowest-life opponent. Never more targets than the spec allows.
    fn choose_divided_targets(
        &self,
        game: &Game,
        spec: &TargetSpec,
        legal: &[Target],
        ctx: &Ctx,
        ability: &StackAbility,
        total: i64,
    ) -> Vec<Target> {
        let me = ctx.controller;
        let count = usize::try_from(spec.count).unwrap_or(usize::MAX);
        let mut picks: Vec<Target> = Vec::new();
        let mut left = total;
        let mut enemies: Vec<ObjId> = legal
            .iter()
            .filter_map(|t| match t {
                Target::Object(o) if game.obj(*o).controller != me => Some(*o),
                _ => None,
            })
            .collect();
        enemies.sort_by(|a, b| {
            threat(game, *b)
                .partial_cmp(&threat(game, *a))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for t in enemies {
            if picks.len() >= count || left <= 0 {
                break;
            }
            let tough = game.chars(t).toughness.filter(|n| *n != 0).unwrap_or(1);
            let need = (tough - game.obj(t).damage).max(1);
            if need <= left {
                picks.push(Target::Object(t));
                left -= need;
            }
        }
        let opps: Vec<PlayerId> = legal
            .iter()
            .filter_map(|t| match t {
                Target::Player(p) if *p != me => Some(*p),
                _ => None,
            })
            .collect();
        if left > 0 && picks.len() < count && !opps.is_empty() {
            let face = first_min(&opps, |p| game.player(p).life).expect("a non-empty list");
            picks.push(Target::Player(face));
        }
        if picks.is_empty()
            && spec.minimum() > 0
            && let Some(pick) = self.choose_target(game, spec, legal, ctx, ability, true)
        {
            picks.push(pick);
        }
        picks
    }

    /// Pick one target: harm the enemy's best, help our own best.
    ///
    /// With `required` a target must be returned if any is legal; the
    /// fallbacks then aim a harmful effect at our *least* valuable
    /// permanent and a beneficial one at the enemy's least valuable (fix
    /// P3). Without it only a worthwhile target is returned.
    #[must_use]
    pub fn choose_target(
        &self,
        game: &Game,
        spec: &TargetSpec,
        legal: &[Target],
        ctx: &Ctx,
        ability: &StackAbility,
        required: bool,
    ) -> Option<Target> {
        if legal.is_empty() {
            return None;
        }
        if spec.what == "spell" {
            return legal.last().copied();
        }
        let (preferred, fallback) = Self::target_preferences(game, spec, legal, ctx, ability);
        if preferred.is_some() {
            return preferred;
        }
        if !required {
            return None;
        }
        fallback.or_else(|| legal.first().copied())
    }

    /// `(the target worth choosing, the least bad forced one)`.
    fn target_preferences(
        game: &Game,
        spec: &TargetSpec,
        legal: &[Target],
        ctx: &Ctx,
        ability: &StackAbility,
    ) -> (Option<Target>, Option<Target>) {
        let me = ctx.controller;
        let opps: Vec<PlayerId> = legal
            .iter()
            .filter_map(|t| match t {
                Target::Player(p) if *p != me => Some(*p),
                _ => None,
            })
            .collect();
        let face = first_min(&opps, |p| game.player(p).life).map(Target::Player);
        if spec.what == "player" || spec.what == "opponent" {
            return (face, None);
        }
        let objects = |mine: bool| -> Vec<Target> {
            legal
                .iter()
                .copied()
                .filter(|t| match t {
                    Target::Object(o) => (game.obj(*o).controller == me) == mine,
                    _ => false,
                })
                .collect()
        };
        let enemy = objects(false);
        let own = objects(true);
        let best_enemy = first_max(&enemy, |t| target_threat(game, t));
        let worst_enemy = first_min(&enemy, |t| target_threat(game, t));
        let best_own = first_max(&own, |t| target_threat(game, t));
        let worst_own = first_min(&own, |t| target_threat(game, t));
        if is_beneficial(ability.effect()) {
            // help our best; forced: the enemy's least valuable (P3)
            return (best_own, worst_enemy);
        }
        // harm the enemy's best, else the face; forced: our least valuable (P3)
        (best_enemy.or(face), worst_own)
    }

    // ------------------------------------------------------- combat

    /// 1-ply lookahead over the declared attack: simulate each defender's
    /// blocks (using that defender's own policy) and drop attackers whose
    /// expected trade is bad for this profile's aggression.
    fn lookahead_filter(
        &self,
        game: &Game,
        picks: Vec<(ObjId, Defender)>,
    ) -> Vec<(ObjId, Defender)> {
        if picks.is_empty() {
            return picks;
        }
        let mut by_def: Vec<(PlayerId, Vec<(ObjId, Defender)>)> = Vec::new();
        for (obj, target) in picks {
            let dfn = match target {
                Defender::Player(p) => p,
                Defender::Object(o) => game.obj(o).controller,
            };
            match by_def.iter_mut().find(|(d, _)| *d == dfn) {
                Some((_, mine)) => mine.push((obj, target)),
                None => by_def.push((dfn, vec![(obj, target)])),
            }
        }
        let mut kept = Vec::new();
        for (dfn, mine) in by_def {
            let attackers: Vec<ObjId> = mine.iter().map(|(a, _)| *a).collect();
            // lethal alpha strike: send everything, no second thoughts
            let incoming: i64 = attackers.iter().map(|a| power(game, *a)).sum();
            if incoming >= game.player(dfn).life {
                kept.extend(mine);
                continue;
            }
            let blockers = untapped_creatures(game, dfn);
            let predicted = game
                .policy(dfn)
                .declare_blockers(game, dfn, &attackers, &blockers);
            let tolerance = (self.profile.aggression - 1.0) * 3.0;
            for (obj, target) in mine {
                let blocked_by: Vec<ObjId> = predicted
                    .iter()
                    .filter(|(_, attacker)| *attacker == obj)
                    .map(|(blocker, _)| *blocker)
                    .collect();
                let score = Self::attack_ev(game, obj, &blocked_by);
                if score >= -tolerance {
                    kept.push((obj, target));
                }
            }
        }
        kept
    }

    /// Estimate the expected value of one attack against a predicted block.
    fn attack_ev(game: &Game, attacker: ObjId, blockers: &[ObjId]) -> f64 {
        let ach = game.chars(attacker);
        let (att_power, att_tough) = (ach.power.unwrap_or(0), ach.toughness.unwrap_or(0));
        if blockers.is_empty() {
            return float(att_power)
                + if game.obj(attacker).commander {
                    2.0
                } else {
                    0.0
                };
        }
        let incoming: i64 = blockers.iter().map(|b| power(game, *b)).sum();
        let dies = (incoming >= att_tough && !ach.keywords.contains("indestructible"))
            || blockers.iter().any(|b| has_keyword(game, *b, "deathtouch"));
        let mut kills = 0.0;
        let mut remaining = att_power;
        let mut by_toughness: Vec<ObjId> = blockers.to_vec();
        by_toughness.sort_by_key(|b| toughness(game, *b));
        for b in by_toughness {
            let btough = toughness(game, b);
            if remaining >= btough || ach.keywords.contains("deathtouch") {
                kills += threat(game, b);
                remaining -= btough;
            }
        }
        let overflow = if ach.keywords.contains("trample") {
            remaining.max(0)
        } else {
            0
        };
        kills + float(overflow) - if dies { threat(game, attacker) } else { 0.0 }
    }

    /// Pick which opponent this creature attacks, or `None` to hold.
    #[must_use]
    pub fn attack_target(
        &self,
        game: &Game,
        player: PlayerId,
        attacker: ObjId,
    ) -> Option<PlayerId> {
        let ch = game.chars(attacker);
        let opps = game.opponents(player);
        let threats: Vec<(PlayerId, f64)> = opps
            .iter()
            .map(|o| (*o, Self::player_threat(game, *o)))
            .collect();
        let top_threat = threats.iter().map(|(_, t)| *t).fold(0.0_f64, f64::max);
        let mut best: Option<(PlayerId, f64)> = None;
        let att_power = ch.power.unwrap_or(0);
        let tough = ch.toughness.unwrap_or(0);
        for &opp in &opps {
            let can_be_blocked: Vec<ObjId> = untapped_creatures(game, opp)
                .into_iter()
                .filter(|b| can_block(game, *b, attacker))
                .collect();
            let danger = can_be_blocked
                .iter()
                .map(|b| power(game, *b))
                .max()
                .unwrap_or(0);
            let evasive = can_be_blocked.is_empty();
            let life = game.player(opp).life;
            let lethal_range = life <= att_power * 3;
            let profitable = evasive
                || float(danger) < float(tough) * self.profile.aggression
                || (att_power >= BIG_ATTACKER_POWER && self.profile.aggression >= 1.0)
                || game.obj(attacker).commander;
            if !profitable {
                continue;
            }
            let opp_threat = threats
                .iter()
                .find(|(p, _)| *p == opp)
                .map_or(0.0, |(_, t)| *t);
            // kingmaker avoidance (pods): don't farm the weakest player
            // while a real archenemy is developing
            if opps.len() > 1 && !lethal_range && opp_threat < 0.4 * top_threat {
                continue;
            }
            let grudge = game
                .player(player)
                .grudges
                .get(&game.player(opp).name)
                .copied()
                .unwrap_or(0.0);
            let score =
                if evasive { 100.0 } else { 0.0 } + opp_threat * 0.8 + grudge.min(10.0) * 0.5
                    - float(life) * 0.2
                    - float(i64::try_from(can_be_blocked.len()).unwrap_or(i64::MAX)) * 3.0;
            if best.is_none_or(|(_, bs)| score > bs) {
                best = Some((opp, score));
            }
        }
        best.map(|(opp, _)| opp)
    }
}

// ------------------------------------------------------------ the trait

impl Policy for DefaultPolicy {
    /// Keep hands whose land count is inside the profile's window.
    #[rule("103.5")]
    fn keep_hand(&self, game: &Game, _player: PlayerId, hand: &[ObjId], mulls: usize) -> bool {
        let lands = hand.iter().filter(|c| is_land(game, **c)).count();
        if mulls >= self.profile.max_mulligans {
            return true;
        }
        (self.profile.mulligan_min_lands..=self.profile.mulligan_max_lands).contains(&lands)
    }

    /// London mulligan (rule 103.5c): put `n` cards on the bottom.
    fn bottom_cards(&self, game: &Game, _player: PlayerId, hand: &[ObjId], n: usize) -> Vec<ObjId> {
        let mut ranked = hand.to_vec();
        ranked.sort_by(|a, b| {
            Self::keep_rank(game, *a)
                .partial_cmp(&Self::keep_rank(game, *b))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        ranked.truncate(n);
        ranked
    }

    /// Choose one legal action, or `None` to pass priority.
    fn choose_action(&self, game: &Game, player: PlayerId) -> Option<Action> {
        // respond to opposing spells with counterspells
        if !game.stack.is_empty() {
            return self.respond_to_stack(game, player);
        }
        let active = player == game.active_player();
        if !active || !(game.phase == "main1" || game.phase == "main2") {
            // P4: the end of an opponent's turn is the window for flash
            // creatures and non-counter instants (mana would be lost)
            if !active && game.phase == "end" {
                return self.instant_speed_play(game, player);
            }
            return None;
        }
        self.main_phase_action(game, player)
    }

    /// Pick the targets of one `TargetSpec`: its minimum, then good ones.
    ///
    /// Each pick is distinct (rule 115.3). Slots beyond the minimum are
    /// filled only with targets worth choosing (an enemy for a harmful
    /// effect, our own for a beneficial one); divided damage never takes
    /// more targets than it has points of damage.
    #[rule("115.3", "601.2c")]
    fn choose_targets(
        &self,
        game: &Game,
        spec: &TargetSpec,
        legal: &[Target],
        ctx: &Ctx,
        ability: &StackAbility,
    ) -> Vec<Target> {
        if let Some(total) = divided_amount(game, ctx, ability) {
            return self.choose_divided_targets(game, spec, legal, ctx, ability, total);
        }
        let count = usize::try_from(spec.count).unwrap_or(usize::MAX);
        let minimum = usize::try_from(spec.minimum()).unwrap_or(0);
        let mut picks: Vec<Target> = Vec::new();
        let mut remaining: Vec<Target> = legal.to_vec();
        while !remaining.is_empty() && picks.len() < count {
            let required = picks.len() < minimum;
            let Some(pick) = self.choose_target(game, spec, &remaining, ctx, ability, required)
            else {
                break;
            };
            picks.push(pick);
            remaining.retain(|t| *t != pick);
        }
        picks
    }

    /// Pick the best land drop: untapped and color-rich first.
    fn best_land(&self, game: &Game, _player: PlayerId, lands: &[ObjId]) -> Option<ObjId> {
        let score = |card: ObjId| -> i64 {
            let o = game.obj(card);
            let (colors, tapped) = o.card_ref.as_ref().map_or((0, false), |r| {
                (
                    r.behavior.land_colors().map_or(0, |c| c.len()),
                    r.behavior.enters_tapped(),
                )
            });
            (if tapped { 0 } else { 2 }) + i64::try_from(colors).unwrap_or(0)
        };
        first_max(lands, score)
    }

    /// Choose the attack: per-creature targets plus a lookahead.
    #[rule("508.1")]
    fn declare_attackers(
        &self,
        game: &Game,
        player: PlayerId,
        candidates: &[ObjId],
    ) -> Vec<(ObjId, Defender)> {
        let mut picks = Vec::new();
        for &obj in candidates {
            if power(game, obj) <= 0 {
                continue;
            }
            let Some(target) = self.attack_target(game, player, obj) else {
                continue;
            };
            picks.push((obj, Defender::Player(target)));
        }
        self.lookahead_filter(game, picks)
    }

    /// Choose blocks: trades, safe blocks, and racing chumps.
    #[rule("509.1")]
    fn declare_blockers(
        &self,
        game: &Game,
        player: PlayerId,
        attackers: &[ObjId],
        blockers: &[ObjId],
    ) -> Vec<(ObjId, ObjId)> {
        let mut assignment: Vec<(ObjId, ObjId)> = Vec::new();
        let mut free: Vec<ObjId> = blockers.to_vec();
        let mut threat_order: Vec<ObjId> = attackers.to_vec();
        threat_order.sort_by_key(|a| -power(game, *a));
        let incoming: i64 = attackers.iter().map(|a| power(game, *a)).sum();
        let life = game.player(player).life;
        let must_chump = incoming >= life || life <= self.profile.race_life;
        for a in threat_order {
            let ach = game.chars(a);
            let mut cands: Vec<ObjId> = free
                .iter()
                .copied()
                .filter(|b| can_block(game, *b, a))
                .collect();
            let menace = ach.keywords.contains("menace");
            if menace && cands.len() < MENACE_MIN_BLOCKERS {
                continue;
            }
            let mut by_power = cands.clone();
            by_power.sort_by_key(|b| -power(game, *b));
            let pick = by_power.into_iter().find(|b| {
                let bch = game.chars(*b);
                let kills = bch.power.unwrap_or(0) >= ach.toughness.unwrap_or(0)
                    || bch.keywords.contains("deathtouch");
                let survives = ach.power.unwrap_or(0) < bch.toughness.unwrap_or(0)
                    && !ach.keywords.contains("deathtouch");
                kills || survives || must_chump
            });
            let Some(pick) = pick else {
                continue;
            };
            if menace {
                cands.retain(|b| *b != pick);
                let Some(&other) = cands.first() else {
                    continue;
                };
                assignment.push((pick, a));
                assignment.push((other, a));
                free.retain(|b| *b != pick && *b != other);
            } else {
                assignment.push((pick, a));
                free.retain(|b| *b != pick);
            }
        }
        assignment
    }
}
