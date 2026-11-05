//! Resolving the stack (rule 608) and the one-shot effect nodes (rule
//! 610): the `resolve` methods of every Python effect class.

use std::rc::Rc;

use indexmap::IndexMap;

use super::{Game, MoveOptions, field};
use crate::effects::{Ctx, Effect, count_value};
use crate::layers::ContinuousEffect;
use crate::manasys::parse_cost;
use crate::objects::{ObjId, Zone};
use crate::rule;
use crate::stack::{StackAbility, StackItem, StackObject, Target};

impl Game {
    /// Resolve the top object of the stack (rule 608.1).
    #[rule("608.2", "608.2b", "608.3", "608.2m")]
    pub fn resolve_top(&mut self) {
        let Some(item) = self.stack.pop() else {
            return;
        };
        let what = self.lname_item(&item);
        if self.resolve_item(&item) {
            // logged on completion so observers snapshot the applied state
            self.log("resolve", vec![field("what", what)]);
        }
    }

    /// Resolve one stack item; `false` when it fizzles (rule 608.2b) or an
    /// intervening "if" fails on resolution (rule 603.4).
    fn resolve_item(&mut self, item: &StackItem) -> bool {
        let mut ctx = Ctx {
            controller: item.controller,
            source: Some(item.source),
            targets: item.targets.clone(),
            x: item.x,
            event_obj: None,
            target_groups: item.target_groups.clone(),
            specs: item
                .ability
                .as_ref()
                .map(|a| a.targets().to_vec())
                .unwrap_or_default(),
            division: item.division.clone(),
        };
        if let StackObject::Trigger(t) = &item.obj {
            ctx.event_obj = t.event.data.obj;
        }
        if !item.targets.is_empty() {
            let groups: Vec<Vec<Target>> = if item.target_groups.is_empty() {
                vec![item.targets.clone()]
            } else {
                item.target_groups.clone()
            };
            let any_legal = groups
                .iter()
                .enumerate()
                .any(|(i, group)| group.iter().any(|t| self.still_legal_target(*t, &ctx, i)));
            if !any_legal {
                // rule 608.2b: every target illegal -> doesn't resolve
                if item.is_spell
                    && let StackObject::Spell(card) = item.obj
                {
                    self.move_zone(card, Zone::Graveyard);
                }
                let what = self.lname_item(item);
                self.log("fizzle", vec![field("what", what)]);
                return false;
            }
        }
        if item.is_spell {
            self.resolve_spell(item, &ctx);
        } else if let Some(ability) = &item.ability {
            if let StackAbility::Triggered(t) = ability
                && let Some(cond) = &t.intervening_if
                && !cond(self, item.source)
            {
                return false; // rule 603.4 recheck
            }
            if let Some(effect) = ability.effect() {
                self.resolve_effect(effect, &ctx);
            }
        }
        self.bump();
        true
    }

    /// Resolve a spell: run its instructions or enter the battlefield.
    #[rule("608.2m", "608.3", "303.4", "306.5b")]
    fn resolve_spell(&mut self, item: &StackItem, ctx: &Ctx) {
        let StackObject::Spell(card) = item.obj else {
            return;
        };
        let ch = Rc::clone(&self.obj(card).base);
        if ch.types.contains("Instant") || ch.types.contains("Sorcery") {
            if let Some(ability) = &item.ability
                && let Some(effect) = ability.effect()
            {
                self.resolve_effect(effect, ctx);
            }
            self.move_zone(card, Zone::Graveyard); // rule 608.2m
            return;
        }
        // rule 608.3: permanent spell resolves to the battlefield
        let mut counters: IndexMap<String, i64> = IndexMap::new();
        if ch.types.contains("Planeswalker")
            && let Some(loyalty) = ch.loyalty
            && loyalty != 0
        {
            counters.insert("loyalty".to_owned(), loyalty); // rule 306.5b
        }
        if !ch.etb_x_counters.is_empty() && item.x != 0 {
            counters.insert(ch.etb_x_counters.clone(), item.x);
        }
        let moved = self.move_zone_with(
            card,
            Zone::Battlefield,
            &MoveOptions {
                counters,
                ..MoveOptions::default()
            },
        );
        // rule 303.4: an Aura enters attached to its target
        if let Some(moved) = moved
            && ch.subtypes.contains("Aura")
            && let Some(first) = item.targets.first()
        {
            if let Target::Object(t) = *first
                && self.obj(t).zone == Zone::Battlefield
            {
                self.attach(moved, t);
            } else {
                self.move_zone(moved, Zone::Graveyard);
            }
        }
    }

    /// Perform `effect` through the game API (rule 610.1): the Python
    /// `resolve(game, ctx)` of every node.
    #[rule("610.1", "608.2c")]
    #[expect(
        clippy::too_many_lines,
        reason = "one arm per Python effect class, kept together"
    )]
    pub fn resolve_effect(&mut self, effect: &Effect, ctx: &Ctx) {
        match effect {
            Effect::Sequence(parts) => {
                for part in parts {
                    self.resolve_effect(part, ctx);
                }
            }
            Effect::Custom { body, .. } => (body.0)(self, ctx),
            Effect::CreateTokens {
                count,
                spec,
                tapped,
                ..
            } => {
                let n = count_value(count, self, ctx);
                self.create_tokens(ctx.controller, Rc::clone(spec), n, ctx.source, *tapped);
            }
            Effect::DrawCards { count, who } => {
                let players = if who == "you" {
                    vec![ctx.controller]
                } else {
                    self.players_apnap()
                };
                for p in players {
                    let n = count_value(count, self, ctx);
                    self.draw(p, n);
                }
            }
            Effect::GainLife { amount } => {
                let n = count_value(amount, self, ctx);
                self.gain_life(ctx.controller, n);
            }
            Effect::LoseLife { amount, who } => {
                let n = count_value(amount, self, ctx);
                if who == "target" {
                    for t in ctx.legal_group(self, 0) {
                        if let Target::Player(p) = t {
                            self.lose_life(p, n);
                        }
                    }
                    return;
                }
                let players = if who == "each_opponent" {
                    self.opponents(ctx.controller)
                } else {
                    vec![ctx.controller]
                };
                for p in players {
                    self.lose_life(p, n);
                }
            }
            Effect::Drain { amount } => {
                let n = count_value(amount, self, ctx);
                let mut total = 0;
                for p in self.opponents(ctx.controller) {
                    self.lose_life(p, n);
                    self.player_mut(p).stat("drained_taken", n);
                    total += n;
                }
                self.player_mut(ctx.controller).stat("drain", total);
                self.gain_life(ctx.controller, total);
            }
            Effect::DealDamage { amount, to, index } => {
                let n = count_value(amount, self, ctx);
                match to.as_str() {
                    "each_creature" => {
                        for obj in self.battlefield_objects() {
                            if self.chars(obj).types.contains("Creature") {
                                self.deal_damage(ctx.source, Target::Object(obj), n, false);
                            }
                        }
                    }
                    "divided" => {
                        // the announced shares go to the targets that are
                        // still legal; a vanished target's share is lost
                        for (target, part) in ctx
                            .group(*index)
                            .into_iter()
                            .zip(ctx.division.iter().copied())
                        {
                            if matches!(target, Target::Object(_) | Target::Player(_))
                                && self.still_legal_target(target, ctx, *index)
                            {
                                self.deal_damage(ctx.source, target, part, false);
                            }
                        }
                    }
                    _ => {
                        for t in ctx.legal_group(self, *index) {
                            if matches!(t, Target::Object(_) | Target::Player(_)) {
                                self.deal_damage(ctx.source, t, n, false);
                            }
                        }
                    }
                }
            }
            Effect::Destroy { index, all_of } => {
                if all_of == "creatures" {
                    for obj in self.battlefield_objects() {
                        if self.chars(obj).types.contains("Creature") {
                            self.destroy(obj);
                        }
                    }
                    return;
                }
                for obj in ctx
                    .legal_group(self, *index)
                    .into_iter()
                    .filter_map(Target::object)
                {
                    self.destroy(obj);
                }
            }
            Effect::ExileObj { index } => {
                for obj in ctx
                    .legal_group(self, *index)
                    .into_iter()
                    .filter_map(Target::object)
                {
                    self.exile(obj);
                }
            }
            Effect::SacrificeSelf => {
                if let Some(source) = ctx.source
                    && self.obj(source).zone == Zone::Battlefield
                {
                    self.sacrifice(ctx.controller, source);
                }
            }
            Effect::ReturnToHand { index, self_land } => {
                if *self_land {
                    let lands: Vec<ObjId> = self
                        .player(ctx.controller)
                        .battlefield
                        .iter()
                        .copied()
                        .filter(|o| self.chars(*o).types.contains("Land") && Some(*o) != ctx.source)
                        .collect();
                    if let Some(pick) = self.policy(ctx.controller).choose_bounce_land(self, &lands)
                    {
                        self.move_zone(pick, Zone::Hand);
                    }
                    return;
                }
                for obj in ctx
                    .legal_group(self, *index)
                    .into_iter()
                    .filter_map(Target::object)
                {
                    self.move_zone(obj, Zone::Hand);
                }
            }
            Effect::PutCounters { kind, count, on } => {
                let n = count_value(count, self, ctx);
                for obj in self.counter_recipients(ctx, on) {
                    self.put_counters(obj, kind, n);
                }
            }
            Effect::Proliferate { times } => {
                for _ in 0..*times {
                    self.proliferate(ctx.controller);
                }
            }
            Effect::SearchLands {
                count,
                tapped,
                to_hand,
                basic_only,
            } => {
                let n = count_value(count, self, ctx);
                self.search_lands(ctx.controller, n, *tapped, *to_hand, *basic_only);
            }
            Effect::TargetControllerBasicLand { index } => {
                for obj in ctx.group(*index).into_iter().filter_map(Target::object) {
                    let controller = self.obj(obj).controller;
                    self.search_lands(controller, 1, false, false, true);
                }
            }
            Effect::TargetControllerGainsPower { index } => {
                for obj in ctx.group(*index).into_iter().filter_map(Target::object) {
                    let (controller, power) = (
                        self.obj(obj).controller,
                        self.obj(obj).base.power.unwrap_or(0),
                    );
                    self.gain_life(controller, power);
                }
            }
            Effect::LoseLifeTargetMV { index } => {
                for obj in ctx.group(*index).into_iter().filter_map(Target::object) {
                    let mv = parse_cost(&self.obj(obj).base.mana_cost).mv();
                    self.lose_life(ctx.controller, mv);
                }
            }
            Effect::CopySpell { index } => {
                for id in ctx.group(*index).into_iter().filter_map(Target::spell) {
                    if self.stack_item(id).is_some() {
                        self.copy_spell(id, ctx.controller);
                    }
                }
            }
            Effect::PutLandFromHand { tapped } => {
                let lands: Vec<ObjId> = self
                    .player(ctx.controller)
                    .hand
                    .iter()
                    .copied()
                    .filter(|c| self.obj(*c).base.types.contains("Land"))
                    .collect();
                if lands.is_empty() {
                    return;
                }
                if let Some(pick) =
                    self.policy(ctx.controller)
                        .best_land(self, ctx.controller, &lands)
                {
                    self.move_zone_with(
                        pick,
                        Zone::Battlefield,
                        &MoveOptions {
                            tapped: *tapped,
                            ..MoveOptions::default()
                        },
                    );
                }
            }
            Effect::TakeDeadCreature => {
                let Some(obj) = ctx.event_obj else { return };
                if self.obj(obj).is_token || self.obj(obj).zone != Zone::Graveyard {
                    return;
                }
                self.obj_mut(obj).controller = ctx.controller;
                self.move_zone(obj, Zone::Battlefield);
                self.player_mut(ctx.controller).stat("grave_robs", 1);
            }
            Effect::AddMana(add) => {
                if add.chooses() {
                    let allowed = add.allowed(self, ctx.controller);
                    let pick = self
                        .policy(ctx.controller)
                        .choose_mana_color(self, ctx, &allowed);
                    self.player_mut(ctx.controller)
                        .mana_pool
                        .add(&pick, add.amount);
                    return;
                }
                for t in &add.types {
                    self.player_mut(ctx.controller).mana_pool.add(t, 1);
                }
            }
            Effect::CounterSpell => {
                if let Some(Target::Spell(id)) = ctx.target(0)
                    && self.stack_item(id).is_some()
                {
                    self.player_mut(ctx.controller)
                        .stat("counterspells_used", 1);
                    self.counter_spell(id);
                }
            }
            Effect::PumpAll {
                power,
                toughness,
                tokens_only,
            } => {
                let (me, tokens_only, power, toughness) =
                    (ctx.controller, *tokens_only, *power, *toughness);
                self.add_floating_effect(
                    ContinuousEffect::new(
                        7,
                        "c",
                        move |game, obj, ch| {
                            let o = game.obj(obj);
                            o.controller == me
                                && ch.types.contains("Creature")
                                && (!tokens_only || o.is_token)
                        },
                        move |_game, _obj, ch| {
                            ch.power = Some(ch.power.unwrap_or(0) + power);
                            ch.toughness = Some(ch.toughness.unwrap_or(0) + toughness);
                        },
                    )
                    .with_source(ctx.source)
                    .until_end_of_turn(),
                );
            }
            Effect::ProtectAll => {
                let me = ctx.controller;
                self.add_floating_effect(
                    ContinuousEffect::new(
                        6,
                        "",
                        move |game, obj, _ch| game.obj(obj).controller == me,
                        |_game, _obj, ch| {
                            ch.keywords.insert("hexproof".to_owned());
                            ch.keywords.insert("indestructible".to_owned());
                        },
                    )
                    .with_source(ctx.source)
                    .until_end_of_turn(),
                );
            }
            Effect::Populate { times } => {
                for _ in 0..*times {
                    self.populate(ctx.controller);
                }
            }
            Effect::EnergyGain { count } => {
                let n = count_value(count, self, ctx);
                self.player_mut(ctx.controller).energy += n;
            }
            Effect::Scry { count } => {
                let n = count_value(count, self, ctx);
                self.scry(ctx.controller, n);
            }
            Effect::TutorAny => self.tutor(ctx.controller),
            Effect::Untap { on } => {
                let objs: Vec<Target> = if on == "self" {
                    ctx.source.map(Target::Object).into_iter().collect()
                } else {
                    ctx.legal_group(self, 0)
                };
                for obj in objs.into_iter().filter_map(Target::object) {
                    if self.obj(obj).zone == Zone::Battlefield {
                        self.untap(obj);
                    }
                }
            }
            Effect::TapTarget { index } => {
                for obj in ctx
                    .legal_group(self, *index)
                    .into_iter()
                    .filter_map(Target::object)
                {
                    self.tap(obj);
                }
            }
            Effect::Blink { index } => {
                for obj in ctx
                    .legal_group(self, *index)
                    .into_iter()
                    .filter_map(Target::object)
                {
                    self.blink(obj);
                }
            }
            Effect::Noop { .. } => {}
        }
    }

    /// The permanents a `PutCounters` effect puts counters on.
    fn counter_recipients(&self, ctx: &Ctx, on: &str) -> Vec<ObjId> {
        match on {
            "self" => ctx.source.into_iter().collect(),
            "each_creature" | "each_opponent_creature" => self
                .battlefield_objects()
                .into_iter()
                .filter(|o| {
                    self.chars(*o).types.contains("Creature")
                        && (on == "each_creature" || self.obj(*o).controller != ctx.controller)
                })
                .collect(),
            _ => ctx
                .legal_group(self, 0)
                .into_iter()
                .filter_map(Target::object)
                .collect(),
        }
    }
}
