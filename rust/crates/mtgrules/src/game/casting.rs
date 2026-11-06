//! Casting spells (rule 601), activating abilities (rule 602, 606),
//! cycling (702.29a), ward (702.21) and playing lands (116.2a, 305).

use std::rc::Rc;

use super::{ActivationKey, Game, field};
use crate::abilities::{Ability, ActivatedAbility};
use crate::effects::Ctx;
use crate::events::{Event, EventData, EventType};
use crate::manasys::{Cost, parse_cost};
use crate::objects::{ObjId, PlayerId, Zone};
use crate::protocols::LogValue;
use crate::rule;
use crate::stack::{StackAbility, StackId, StackItem, StackObject, Target};

impl Game {
    /// Whether `player` may cast `card` right now (rules 117.1a, 307.1).
    ///
    /// Instants, and anything with flash (rule 702.8a), may be cast
    /// whenever the player has priority; everything else only during the
    /// player's own main phase while the stack is empty.
    #[rule("117.1a", "307.1", "702.8a")]
    #[must_use]
    pub fn can_cast_now(&self, player: PlayerId, card: ObjId) -> bool {
        let ch = &self.obj(card).base;
        if ch.types.contains("Instant") || ch.keywords.contains("flash") {
            return true;
        }
        player == self.active_player()
            && (self.phase == "main1" || self.phase == "main2")
            && self.stack.is_empty()
    }

    /// Cast a spell through the rule 601.2 process; `false` if illegal.
    #[rule(
        "601.2", "601.2a", "601.2b", "601.2c", "601.2f", "601.2h", "601.2i", "903.8"
    )]
    pub fn cast_spell(
        &mut self,
        player: PlayerId,
        card: ObjId,
        x: i64,
        from_command: bool,
    ) -> bool {
        if !self.can_cast_now(player, card) {
            return false;
        }
        let ch = Rc::clone(&self.obj(card).base);
        let cost = self.spell_cost(player, card, x);
        // rule 601.2b additional costs: verify they can be paid up front
        let extra = ch.additional_cost.as_str();
        let mut extra_sac = None;
        if extra == "sacrifice_creature" {
            extra_sac = self
                .policy(player)
                .choose_sacrifice(self, player, "creature", None);
            if extra_sac.is_none() {
                return false;
            }
        } else if extra == "discard_card" && !self.player(player).hand.iter().any(|c| *c != card) {
            return false;
        }
        let spell_ability = ch.abilities.iter().find_map(Ability::as_spell).cloned();
        let (mut groups, mut division) = (Vec::new(), Vec::new());
        if let Some(sa) = &spell_ability
            && !sa.targets.is_empty()
        {
            let ability = StackAbility::Spell(Rc::clone(sa));
            let Some(chosen) = self.choose_targets(player, &ability, card, x) else {
                return false; // rule 601.2c: no targets
            };
            // rule 601.2d
            division = self.announce_division(
                player,
                &ability,
                &chosen,
                &Ctx {
                    x,
                    ..Ctx::new(player, Some(card))
                },
            );
            groups = chosen;
        }
        if !self.pay_mana(player, &cost) {
            return false; // rule 601.2h
        }
        self.pay_additional_cost(player, card, extra, extra_sac);
        let zone = self.obj(card).zone;
        self.remove_from_zone(card, zone);
        self.obj_mut(card).zone = Zone::Stack;
        let id = self.next_stack_id();
        self.stack.push(StackItem {
            id,
            obj: StackObject::Spell(card),
            source: card,
            controller: player,
            ability: spell_ability.map(StackAbility::Spell),
            targets: Self::flatten(&groups),
            target_groups: groups,
            division,
            x,
            is_spell: true,
        });
        self.record_cast(player, card, x, from_command);
        // rule 702.21 ward: cost tax handled when the spell targets
        self.ward_check(id);
        self.queue_triggers(&Event::new(
            EventType::Cast,
            EventData {
                obj: Some(card),
                player: Some(player),
                ..EventData::default()
            },
        ));
        self.bump();
        true
    }

    /// Total mana cost: X, commander tax, and cost-reduction statics.
    #[rule("601.2f", "903.8")]
    fn spell_cost(&self, player: PlayerId, card: ObjId, x: i64) -> Cost {
        let o = self.obj(card);
        let ch = &o.base;
        let mut cost = parse_cost(&ch.mana_cost).with_x(x);
        if o.commander {
            // rule 903.8: commander tax
            cost = cost.with_extra_generic(2 * self.player(player).commander_casts);
        }
        // rule 601.2f cost-reduction statics on the spell itself (e.g.
        // "costs {1} less to cast for each creature")
        if ch.cost_less_per_creature != 0 {
            let creatures = self
                .battlefield_objects()
                .into_iter()
                .filter(|o| self.chars(*o).types.contains("Creature"))
                .count();
            cost = cost
                .reduced(ch.cost_less_per_creature * i64::try_from(creatures).unwrap_or(i64::MAX));
        }
        cost
    }

    /// Rule 601.2h: pay additional costs along with the mana.
    #[rule("601.2b", "601.2h")]
    fn pay_additional_cost(
        &mut self,
        player: PlayerId,
        card: ObjId,
        extra: &str,
        extra_sac: Option<ObjId>,
    ) {
        if let Some(sac) = extra_sac {
            self.sacrifice(player, sac);
        } else if extra == "discard_card" {
            let pool: Vec<ObjId> = self
                .player(player)
                .hand
                .iter()
                .copied()
                .filter(|c| *c != card)
                .collect();
            // the cheapest card (the first, on ties)
            let mut best: Option<(ObjId, i64)> = None;
            for c in pool {
                let mv = parse_cost(&self.obj(c).base.mana_cost).mv();
                if best.is_none_or(|(_, b)| mv < b) {
                    best = Some((c, mv));
                }
            }
            if let Some((pick, _)) = best {
                self.move_zone(pick, Zone::Graveyard);
                let who = self.name_of(player);
                let name = self.obj(pick).base.name.clone();
                self.log("discard", vec![field("who", who), field("card", name)]);
            }
        }
    }

    /// Casting bookkeeping: tax counter, stats, and the log line.
    fn record_cast(&mut self, player: PlayerId, card: ObjId, x: i64, from_command: bool) {
        if self.obj(card).commander && from_command {
            self.player_mut(player).commander_casts += 1;
        }
        self.player_mut(player).stat("spells_cast", 1);
        let name = self.obj(card).base.name.clone();
        self.player_mut(player).cards_cast.push(name.clone());
        let who = self.name_of(player);
        self.log(
            "cast",
            vec![
                field("who", who),
                field("card", name),
                field("x", LogValue::from((x != 0).then_some(x))),
                field("commander", LogValue::from(from_command.then_some(true))),
            ],
        );
        if let Some(card_ref) = &self.obj(card).card_ref {
            if card_ref.behavior.wipe() {
                self.player_mut(player).stat("wipes_cast", 1);
            } else if card_ref.behavior.removal() {
                self.player_mut(player).stat("removal_used", 1);
            }
        }
    }

    /// Ward N: counter the targeting spell unless its controller pays.
    /// The policy decides; paying requires available mana.
    #[rule("702.21")]
    fn ward_check(&mut self, id: StackId) {
        let Some(pos) = self.stack_index(id) else {
            return;
        };
        let (targets, controller) = (self.stack[pos].targets.clone(), self.stack[pos].controller);
        for t in targets {
            let Target::Object(obj) = t else { continue };
            if self.obj(obj).zone != Zone::Battlefield || self.obj(obj).controller == controller {
                continue;
            }
            let ward = self
                .chars(obj)
                .keywords
                .iter()
                .filter(|k| k.starts_with("ward"))
                .min()
                .cloned();
            let Some(ward) = ward else { continue };
            // "ward:N"; a plain "ward" is ward {2} (a non-numeric N, which
            // the compiler never writes, counts as 2 here where Python
            // would raise)
            let n: i64 = ward
                .split_once(':')
                .map_or(2, |(_, v)| v.parse().unwrap_or(2));
            let cost = parse_cost(&format!("{{{n}}}"));
            if self.can_pay_mana(controller, &cost) && self.policy(controller).pay_ward(self, id, n)
            {
                self.pay_mana(controller, &cost);
            } else {
                self.counter_spell(id);
                return;
            }
        }
    }

    /// Activate an ability (rule 602.2); `false` when illegal/unpaid.
    #[rule("602.2", "602.5a", "606.3")]
    pub fn activate_ability(
        &mut self,
        player: PlayerId,
        obj: ObjId,
        ab: &Rc<ActivatedAbility>,
        x: i64,
    ) -> bool {
        if ab.from_hand {
            return self.activate_from_hand(player, obj, ab);
        }
        if !self.activation_legal(player, obj, ab) {
            return false;
        }
        let cost = ab.cost().with_x(x);
        let mut sac_pick = None;
        if !ab.sac_cost.is_empty() && ab.sac_cost != "self" {
            sac_pick = self
                .policy(player)
                .choose_sacrifice(self, player, &ab.sac_cost, Some(obj));
            if sac_pick.is_none() {
                return false;
            }
        }
        // rule 601.2c (via 602.2b): choose targets before determining and
        // paying costs
        let ability = StackAbility::Activated(Rc::clone(ab));
        let Some(groups) = self.choose_targets(player, &ability, obj, x) else {
            return false;
        };
        // rules 601.2f-h: determine and pay costs
        if !self.pay_mana(player, &cost) {
            return false;
        }
        self.pay_activation_costs(player, obj, ab, sac_pick);
        let division = self.announce_division(
            player,
            &ability,
            &groups,
            &Ctx {
                x,
                ..Ctx::new(player, Some(obj))
            },
        );
        let id = self.next_stack_id();
        self.stack.push(StackItem {
            id,
            obj: StackObject::Activated(Rc::clone(ab)),
            source: obj,
            controller: player,
            ability: Some(ability),
            targets: Self::flatten(&groups),
            target_groups: groups,
            division,
            x,
            is_spell: false,
        });
        self.record_activation(player, obj, ab);
        true
    }

    /// Check timing, once-per-turn, tap, loyalty, and life legality.
    ///
    /// Public so the policy can ask before proposing an activation (P1):
    /// mana and target availability are checked separately.
    #[rule("602.5a", "602.5d", "606.3", "302.6")]
    #[must_use]
    pub fn activation_legal(&self, player: PlayerId, obj: ObjId, ab: &ActivatedAbility) -> bool {
        let ch = self.chars(obj);
        let o = self.obj(obj);
        let sorcery_window = player == self.active_player()
            && self.stack.is_empty()
            && (self.phase == "main1" || self.phase == "main2");
        if let Some(loyalty_cost) = ab.loyalty_cost
            && (
                // rule 606.3: one loyalty ability per permanent per turn,
                // only during a main phase with an empty stack
                self.activated_this_turn
                    .contains(&ActivationKey::Loyalty(obj))
                    || !sorcery_window
                    || (loyalty_cost < 0 && o.counter("loyalty") < -loyalty_cost)
            )
        {
            return false;
        }
        if ab.sorcery_only && !sorcery_window {
            return false; // rule 602.5d
        }
        if ab.once_per_turn
            && self
                .activated_this_turn
                .contains(&ActivationKey::Ability(obj, ab.id))
        {
            return false;
        }
        if ab.tap_cost {
            if o.tapped {
                return false;
            }
            if ch.types.contains("Creature")
                && o.entered_this_turn
                && !ch.keywords.contains("haste")
            {
                return false; // rule 302.6
            }
        }
        !(ab.life_cost != 0 && self.player(player).life <= ab.life_cost)
    }

    /// Pay the non-mana activation costs (tap, life, sac, loyalty).
    #[rule("601.2h", "606.3")]
    fn pay_activation_costs(
        &mut self,
        player: PlayerId,
        obj: ObjId,
        ab: &ActivatedAbility,
        sac_pick: Option<ObjId>,
    ) {
        if ab.tap_cost {
            self.tap(obj);
        }
        if ab.life_cost != 0 {
            self.lose_life(player, ab.life_cost);
        }
        if ab.sac_cost == "self" {
            self.sacrifice(player, obj);
        } else if let Some(sac) = sac_pick {
            self.sacrifice(player, sac);
        }
        if let Some(loyalty_cost) = ab.loyalty_cost {
            if loyalty_cost >= 0 {
                self.put_counters(obj, "loyalty", loyalty_cost);
            } else {
                self.remove_counters(obj, "loyalty", -loyalty_cost);
            }
        }
    }

    /// Once-per-turn bookkeeping, stats, and the log line.
    fn record_activation(&mut self, player: PlayerId, obj: ObjId, ab: &ActivatedAbility) {
        if ab.loyalty_cost.is_some() {
            self.activated_this_turn.insert(ActivationKey::Loyalty(obj));
        }
        if ab.once_per_turn {
            self.activated_this_turn
                .insert(ActivationKey::Ability(obj, ab.id));
        }
        self.player_mut(player).stat("abilities_activated", 1);
        let who = self.name_of(player);
        let source = self.lname_obj(obj);
        self.log("activate", vec![field("who", who), field("source", source)]);
        self.bump();
    }

    /// Cycling-style abilities (rule 702.29a): activated while the card
    /// is in the hand; discarding the card is part of the cost.
    #[rule("702.29a")]
    fn activate_from_hand(
        &mut self,
        player: PlayerId,
        obj: ObjId,
        ab: &Rc<ActivatedAbility>,
    ) -> bool {
        if !self.player(player).hand.contains(&obj) {
            return false;
        }
        if !self.pay_mana(player, &ab.cost()) {
            return false;
        }
        self.move_zone(obj, Zone::Graveyard); // discard as a cost
        let id = self.next_stack_id();
        self.stack.push(StackItem {
            id,
            obj: StackObject::Activated(Rc::clone(ab)),
            source: obj,
            controller: player,
            ability: Some(StackAbility::Activated(Rc::clone(ab))),
            targets: Vec::new(),
            target_groups: Vec::new(),
            division: Vec::new(),
            x: 0,
            is_spell: false,
        });
        self.player_mut(player).stat("cards_cycled", 1);
        let who = self.name_of(player);
        let name = self.obj(obj).base.name.clone();
        self.log("cycle", vec![field("who", who), field("card", name)]);
        self.bump();
        true
    }

    /// Playing a land is a special action, no stack (rules 116.2a,
    /// 305.1).
    #[rule("116.2a", "305.1")]
    pub fn play_land(&mut self, player: PlayerId, card: ObjId) -> bool {
        if self.player(player).lands_played >= 1 {
            return false; // rule 305.2
        }
        if player != self.active_player() || !self.stack.is_empty() {
            return false;
        }
        if !self.player(player).hand.contains(&card) {
            return false;
        }
        self.player_mut(player).lands_played += 1;
        let tapped = self
            .obj(card)
            .base
            .abilities
            .iter()
            .any(|ab| ab.as_static().is_some_and(|s| s.enters_tapped));
        self.move_zone_with(
            card,
            Zone::Battlefield,
            &super::MoveOptions {
                tapped,
                ..Default::default()
            },
        );
        self.queue_triggers(&Event::new(
            EventType::LandPlayed,
            EventData {
                obj: Some(card),
                player: Some(player),
                ..EventData::default()
            },
        ));
        self.player_mut(player).stat("lands_played", 1);
        let who = self.name_of(player);
        let name = self.obj(card).base.name.clone();
        self.log(
            "land",
            vec![
                field("who", who),
                field("card", name),
                field("tapped", LogValue::from(tapped.then_some(true))),
            ],
        );
        true
    }
}
