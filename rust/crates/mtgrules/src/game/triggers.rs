//! The trigger queue (rule 603), target choice (rules 115.3, 601.2c) and
//! the announced division of "divided" damage (rule 601.2d).

use std::collections::BTreeSet;
use std::rc::Rc;

use indexmap::IndexMap;

use super::{ActivationKey, Game, field};
use crate::abilities::{AbilityId, TriggeredAbility};
use crate::effects::{Ctx, Effect, count_value};
use crate::events::Event;
use crate::objects::{ObjId, PlayerId, Zone};
use crate::rule;
use crate::stack::{PendingTrigger, StackAbility, StackItem, StackObject, Target};

impl Game {
    /// Collect triggered abilities that trigger off `event` (rules 603.2,
    /// 603.3).
    #[rule("603.2", "603.3")]
    pub fn queue_triggers(&mut self, event: &Event) {
        let mut seen: BTreeSet<(ObjId, AbilityId)> = BTreeSet::new();
        for (source, ab) in self.trigger_watchers(event) {
            if !seen.insert((source, ab.id)) {
                continue;
            }
            if ab.trigger.matches(self, source, event) {
                if let Some(cond) = &ab.intervening_if
                    && !cond(self, source)
                {
                    continue; // rule 603.4
                }
                let controller = self.obj(source).controller;
                self.pending_triggers.push(PendingTrigger {
                    ability: Rc::clone(&ab),
                    source,
                    controller,
                    event: event.clone(),
                });
            }
        }
    }

    /// All `(source, ability)` pairs that watch for `event` (rule 603.6b):
    /// the triggered abilities of every permanent, plus - for an object
    /// that just left the battlefield - its own printed ones, which look
    /// back in time (rules 603.6b-d).
    #[rule("603.6b")]
    fn trigger_watchers(&self, event: &Event) -> Vec<(ObjId, Rc<TriggeredAbility>)> {
        let mut watchers = Vec::new();
        for obj in self.battlefield_objects() {
            for ab in &self.chars(obj).abilities {
                if let Some(t) = ab.as_triggered() {
                    watchers.push((obj, Rc::clone(t)));
                }
            }
        }
        if let Some(obj) = event.data.obj
            && self.obj(obj).zone != Zone::Battlefield
        {
            for ab in &self.obj(obj).base.abilities {
                if let Some(t) = ab.as_triggered() {
                    watchers.push((obj, Rc::clone(t)));
                }
            }
        }
        watchers
    }

    /// Put the pending triggers on the stack in APNAP order, each player
    /// ordering their own (rule 603.3b). Returns whether any were pending.
    #[rule("603.3b")]
    pub fn put_triggers_on_stack(&mut self) -> bool {
        if self.pending_triggers.is_empty() {
            return false;
        }
        let mut by_player: IndexMap<PlayerId, Vec<PendingTrigger>> = IndexMap::new();
        for t in std::mem::take(&mut self.pending_triggers) {
            by_player.entry(t.controller).or_default().push(t);
        }
        for p in self.players_apnap() {
            let mut mine = by_player.swap_remove(&p).unwrap_or_default();
            if mine.len() > 1 {
                let policy = self.policy(p);
                mine = policy.order_triggers(self, p, mine);
            }
            for t in mine {
                if t.ability.once_each_turn {
                    let key = ActivationKey::Trigger(t.source, t.ability.id);
                    if !self.activated_this_turn.insert(key) {
                        continue; // "only once each turn"
                    }
                }
                if t.ability.optional && !self.policy(p).accept_optional(self, &t) {
                    continue;
                }
                let ability = StackAbility::Triggered(Rc::clone(&t.ability));
                let Some(groups) = self.choose_targets(p, &ability, t.source, 0) else {
                    continue; // rule 603.3d: no legal targets, removed from the stack
                };
                let division =
                    self.announce_division(p, &ability, &groups, &Ctx::new(p, Some(t.source)));
                let source = t.source;
                let what = self.obj(source).base.name.clone();
                let id = self.next_stack_id();
                self.stack.push(StackItem {
                    id,
                    obj: StackObject::Trigger(Box::new(t)),
                    source,
                    controller: p,
                    ability: Some(ability),
                    targets: Self::flatten(&groups),
                    target_groups: groups,
                    division,
                    x: 0,
                    is_spell: false,
                });
                let who = self.name_of(p);
                self.log("trigger", vec![field("who", who), field("what", what)]);
            }
        }
        true
    }

    /// Choose the targets of every `TargetSpec`, or `None` when
    /// impossible (rules 115.3, 601.2c): one group per spec, holding
    /// between its minimum and its count distinct targets.
    #[rule("115.3", "601.2c")]
    pub(crate) fn choose_targets(
        &self,
        player: PlayerId,
        ability: &StackAbility,
        source: ObjId,
        x: i64,
    ) -> Option<Vec<Vec<Target>>> {
        let mut groups = Vec::new();
        let ctx = Ctx {
            x,
            ..Ctx::new(player, Some(source))
        };
        for spec in ability.targets() {
            let legal = self.legal_targets(spec, &ctx);
            let picks = if legal.is_empty() {
                Vec::new()
            } else {
                self.policy(player)
                    .choose_targets(self, spec, &legal, &ctx, ability)
            };
            if i64::try_from(picks.len()).unwrap_or(i64::MAX) < spec.minimum() {
                return None;
            }
            groups.push(picks);
        }
        Some(groups)
    }

    /// Every chosen target of `groups` in choice order.
    pub(crate) fn flatten(groups: &[Vec<Target>]) -> Vec<Target> {
        groups.iter().flatten().copied().collect()
    }

    /// Rule 601.2d: divide a "divided as you choose" amount on casting.
    #[rule("601.2d")]
    pub(crate) fn announce_division(
        &self,
        player: PlayerId,
        ability: &StackAbility,
        groups: &[Vec<Target>],
        ctx: &Ctx,
    ) -> Vec<i64> {
        let Some(effect) = ability.effect() else {
            return Vec::new();
        };
        let parts: Vec<&Effect> = match effect {
            Effect::Sequence(parts) => parts.iter().collect(),
            other => vec![other],
        };
        for part in parts {
            if let Effect::DealDamage { amount, to, index } = part
                && to == "divided"
            {
                let targets = groups.get(*index).cloned().unwrap_or_default();
                if targets.is_empty() {
                    return Vec::new();
                }
                let total = count_value(amount, self, ctx);
                return self
                    .policy(player)
                    .divide_damage(self, ctx, total, &targets);
            }
        }
        Vec::new()
    }
}
