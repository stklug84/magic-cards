//! Mana abilities and cost payment (rules 601.2g-h, 605): the greedy
//! scarcity-first planner that assigns untapped sources to the pips of a
//! cost, on top of whatever floats in the pool.

use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;

use super::Game;
use crate::abilities::ActivatedAbility;
use crate::effects::{AddMana, Effect};
use crate::manasys::{Cost, mana_order};
use crate::objects::{ObjId, PlayerId};
use crate::rule;

/// One untapped mana source as the planner sees it.
///
/// `fixed` is the exact multiset of symbols a non-choosing source makes
/// ("Add {C}{C}", "Add {U}{B}"); `None` for a source whose type is chosen
/// on production ("Add {B} or {R}", "any color", the commander's
/// identity).
#[derive(Clone, Debug)]
pub struct ManaSource {
    /// the permanent
    pub obj: ObjId,
    /// its mana ability
    pub ability: Rc<ActivatedAbility>,
    /// the producible types
    pub colors: BTreeSet<String>,
    /// how much mana one activation makes
    pub amount: i64,
    /// the exact symbols a non-choosing source makes
    pub fixed: Option<Vec<String>>,
}

/// Working state of one greedy mana-payment search (rule 601.2g): the
/// Python `_ManaPlan`.
struct ManaPlan {
    pool: IndexMap<String, i64>,
    avail: Vec<ManaSource>,
    /// sources committed so far, with the mana type each will produce
    used: Vec<(ObjId, Rc<ActivatedAbility>, String)>,
}

impl ManaPlan {
    /// Pop the matching source with the fewest producible colours (the
    /// first such, on ties).
    fn take(&mut self, pred: impl Fn(&BTreeSet<String>) -> bool) -> Option<ManaSource> {
        let mut best: Option<usize> = None;
        for (i, src) in self.avail.iter().enumerate() {
            if pred(&src.colors)
                && best.is_none_or(|b| src.colors.len() < self.avail[b].colors.len())
            {
                best = Some(i);
            }
        }
        best.map(|i| self.avail.remove(i))
    }

    /// Commit `got` to produce `color`; its surplus mana floats.
    ///
    /// A fixed-production source makes everything it lists (Sol Ring's
    /// second {C}, the {B} of "Add {U}{B}"); a choosing source with
    /// `amount` > 1 makes that many of the chosen colour ("three mana of
    /// any one color"). Either way the rest is available to later pips
    /// of the same payment.
    fn commit(&mut self, got: ManaSource, color: &str) {
        self.used.push((got.obj, got.ability, color.to_owned()));
        if let Some(fixed) = got.fixed {
            let mut rest = fixed;
            if let Some(pos) = rest.iter().position(|t| t == color) {
                rest.remove(pos);
            }
            for t in rest {
                *self.pool.entry(t).or_insert(0) += 1;
            }
        } else if got.amount > 1 {
            *self.pool.entry(color.to_owned()).or_insert(0) += got.amount - 1;
        }
    }

    /// Spend one floating mana of `color`, if any.
    fn spend_floating(&mut self, color: &str) -> bool {
        match self.pool.get_mut(color) {
            Some(amount) if *amount > 0 => {
                *amount -= 1;
                true
            }
            _ => false,
        }
    }

    /// Cover the coloured and {C} pips of `cost` (rule 601.2g).
    fn cover_pips(&mut self, cost: &Cost) -> bool {
        let mut need: Vec<String> = Vec::new();
        for (color, n) in &cost.pips {
            for _ in 0..*n {
                need.push(color.clone());
            }
        }
        for _ in 0..cost.colorless {
            need.push("C".to_owned());
        }
        for color in need {
            if self.spend_floating(&color) {
                continue;
            }
            let Some(got) = self.take(|cs| cs.contains(&color)) else {
                return false;
            };
            self.commit(got, &color);
        }
        true
    }

    /// Cover the hybrid pips of `cost`, scarcest option first.
    fn cover_hybrid(&mut self, cost: &Cost) -> bool {
        let mut hybrid: Vec<&BTreeSet<String>> = cost.hybrid.iter().collect();
        hybrid.sort_by_key(|opts| opts.len());
        for opts in hybrid {
            if mana_order(opts.iter())
                .iter()
                .any(|c| self.spend_floating(c))
            {
                continue;
            }
            let Some(got) = self.take(|cs| !cs.is_disjoint(opts)) else {
                return false;
            };
            let color = mana_order(got.colors.intersection(opts))[0].clone();
            self.commit(got, &color);
        }
        true
    }

    /// Cover the generic part from floating mana, then sources.
    fn cover_generic(&mut self, cost: &Cost) -> bool {
        let mut need = cost.generic;
        let mut order: Vec<String> = self.pool.keys().cloned().collect();
        order.sort_by_key(|t| (t != "C", -self.pool[t]));
        for t in order {
            let amount = self.pool.get_mut(&t).expect("a key of the pool");
            let take_n = need.min(*amount);
            *amount -= take_n;
            need -= take_n;
        }
        self.avail.sort_by_key(|e| (-e.amount, e.colors.len()));
        while need > 0 && !self.avail.is_empty() {
            let src = self.avail.remove(0);
            let color = if src.colors.contains("C") {
                "C".to_owned()
            } else {
                mana_order(src.colors.iter())[0].clone()
            };
            self.used.push((src.obj, src.ability, color));
            need -= src.amount;
        }
        need <= 0
    }
}

impl Game {
    /// Untapped permanents with activatable mana abilities (rules 605.1a,
    /// 605.3): one ability per permanent.
    #[rule("605.1a", "605.3")]
    #[must_use]
    pub fn mana_sources(&self, player: PlayerId) -> Vec<(ObjId, Rc<ActivatedAbility>)> {
        let mut out = Vec::new();
        for obj in &self.player(player).battlefield {
            let o = self.obj(*obj);
            if o.tapped {
                continue;
            }
            let ch = self.chars(*obj);
            let summoning_sick = ch.types.contains("Creature")
                && o.entered_this_turn
                && !ch.keywords.contains("haste");
            for ab in &ch.abilities {
                if let Some(a) = ab.as_activated()
                    && a.is_mana_ability
                {
                    if a.tap_cost && summoning_sick {
                        continue; // rule 302.6
                    }
                    out.push((*obj, Rc::clone(a)));
                    break;
                }
            }
        }
        out
    }

    /// The mana types this source's ability can produce.
    #[must_use]
    pub fn mana_colors_of(&self, obj: ObjId, ab: &ActivatedAbility) -> BTreeSet<String> {
        let adds: Vec<&AddMana> = match &ab.effect {
            Some(Effect::AddMana(a)) => vec![a],
            Some(Effect::Sequence(parts)) => parts
                .iter()
                .filter_map(|e| {
                    if let Effect::AddMana(a) = e {
                        Some(a)
                    } else {
                        None
                    }
                })
                .collect(),
            _ => Vec::new(),
        };
        let mut colors: BTreeSet<String> = BTreeSet::new();
        for a in adds {
            // identity first: "any color in your commander's color
            // identity" is restricted to the identity (fix F6)
            if a.commander_identity {
                let identity = self.commander_identity(self.obj(obj).controller);
                if identity.is_empty() {
                    colors.insert("C".to_owned());
                } else {
                    colors.extend(identity);
                }
            } else if a.any_color {
                colors.extend("WUBRG".chars().map(|c| c.to_string()));
            }
            colors.extend(a.types.iter().cloned());
        }
        colors.remove("ANY");
        if colors.is_empty() {
            colors.insert("C".to_owned());
        }
        colors
    }

    /// Whether pool plus untapped sources can cover `cost` (rules 601.2g,
    /// 601.2h).
    #[rule("601.2g", "601.2h")]
    #[must_use]
    pub fn can_pay_mana(&self, player: PlayerId, cost: &Cost) -> bool {
        self.solve_mana(player, cost).is_some()
    }

    /// Pay `cost`, activating mana abilities as needed (rule 601.2h).
    pub fn pay_mana(&mut self, player: PlayerId, cost: &Cost) -> bool {
        let Some(plan) = self.solve_mana(player, cost) else {
            return false;
        };
        for (obj, ability, color) in plan.used {
            self.activate_mana_ability(obj, &ability, &color);
        }
        self.player_mut(player).mana_pool.pay(cost);
        true
    }

    /// Greedy scarcity-first assignment of mana sources to pips, on top
    /// of whatever is already floating in the pool: the plan when the
    /// cost is coverable.
    fn solve_mana(&self, player: PlayerId, cost: &Cost) -> Option<ManaPlan> {
        let avail: Vec<ManaSource> = self
            .mana_sources(player)
            .into_iter()
            .map(|(obj, ability)| ManaSource {
                obj,
                colors: self.mana_colors_of(obj, &ability),
                amount: Self::mana_amount(&ability),
                fixed: Self::fixed_production(&ability),
                ability,
            })
            .collect();
        let mut plan = ManaPlan {
            pool: self.player(player).mana_pool.mana.clone(),
            avail,
            used: Vec::new(),
        };
        (plan.cover_pips(cost) && plan.cover_hybrid(cost) && plan.cover_generic(cost))
            .then_some(plan)
    }

    /// How much mana one activation of the ability produces: a source
    /// whose type is chosen on production makes `amount` mana of that one
    /// type; "Add {C}{C}" makes one per listed symbol (fix F1).
    #[must_use]
    pub fn mana_amount(ab: &ActivatedAbility) -> i64 {
        match &ab.effect {
            Some(Effect::AddMana(a)) => {
                if a.chooses() {
                    a.amount
                } else {
                    i64::try_from(a.types.len()).unwrap_or(1).max(1)
                }
            }
            _ => 1,
        }
    }

    /// The exact symbols a non-choosing mana ability produces, if any.
    fn fixed_production(ab: &ActivatedAbility) -> Option<Vec<String>> {
        match &ab.effect {
            Some(Effect::AddMana(a)) if !a.chooses() && !a.types.is_empty() => {
                Some(a.types.clone())
            }
            _ => None,
        }
    }

    /// Mana abilities resolve immediately, no stack (rule 605.3b).
    #[rule("605.3b")]
    fn activate_mana_ability(&mut self, obj: ObjId, ab: &ActivatedAbility, color: &str) {
        let player = self.obj(obj).controller;
        if ab.tap_cost {
            self.tap(obj);
        }
        if ab.sac_cost == "self" {
            self.sacrifice(player, obj);
        }
        let pool = &mut self.player_mut(player).mana_pool;
        match &ab.effect {
            Some(Effect::AddMana(a)) if !a.chooses() && !a.types.is_empty() => {
                for t in &a.types {
                    pool.add(t, 1);
                }
            }
            Some(Effect::AddMana(a)) => pool.add(color, a.amount), // the planner's choice
            _ => pool.add(color, 1),
        }
    }
}
