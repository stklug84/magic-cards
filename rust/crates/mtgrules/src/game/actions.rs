//! The game actions every effect is built from: tokens (rule 111),
//! draws (121), damage (120), life (119), counters (122), the keyword
//! actions (701) and the stack manipulations (counter, copy).

use std::rc::Rc;

use super::{Game, field};
use crate::abilities::{Ability, ActivatedAbility, TokenSpec};
use crate::effects::{AddMana, Effect};
use crate::events::{Event, EventData, EventType};
use crate::objects::{Characteristics, ObjId, PlayerId, Zone};
use crate::protocols::LogValue;
use crate::rule;
use crate::stack::{StackId, StackItem, StackObject, Target};

impl Game {
    /// Create `count` tokens from `spec` (rules 111.2, 111.3); the
    /// objects created, after replacement effects.
    #[rule("111.2", "111.3")]
    pub fn create_tokens(
        &mut self,
        controller: PlayerId,
        spec: Rc<TokenSpec>,
        count: i64,
        source: Option<ObjId>,
        tapped: Option<bool>,
    ) -> Vec<ObjId> {
        let Some(event) = self.emit(Event::new(
            EventType::CreateToken,
            EventData {
                spec: Some(Rc::clone(&spec)),
                count: Some(count),
                controller: Some(controller),
                source,
                ..EventData::default()
            },
        )) else {
            return Vec::new();
        };
        let count = event.data.count.unwrap_or(0);
        if count <= 0 {
            return Vec::new();
        }
        let mut specs: Vec<Rc<TokenSpec>> = vec![event.data.spec.clone().unwrap_or(spec)];
        specs.extend(event.data.extra_specs.iter().cloned());
        let mut made = Vec::new();
        for tok_spec in specs {
            for _ in 0..count {
                made.push(self.make_token(controller, &tok_spec, tapped));
            }
        }
        made
    }

    /// Build one token object and put it onto the battlefield.
    fn make_token(
        &mut self,
        controller: PlayerId,
        spec: &TokenSpec,
        tapped: Option<bool>,
    ) -> ObjId {
        let mut base = Characteristics {
            name: spec.name.clone(),
            colors: spec.colors.clone(),
            types: spec.types.clone(),
            subtypes: spec.subtypes.clone(),
            power: spec.power,
            toughness: spec.toughness,
            keywords: spec.keywords.clone(),
            ..Characteristics::default()
        };
        let treasure = spec.predefined == "treasure" || spec.predefined == "gold";
        if treasure {
            base.abilities.push(Ability::from(ActivatedAbility {
                tap_cost: true,
                sac_cost: "self".to_owned(),
                is_mana_ability: true,
                effect: Some(Effect::AddMana(AddMana::any())),
                text: "{T}, Sacrifice: Add one mana of any color.".to_owned(),
                ..ActivatedAbility::default()
            }));
        }
        for factory in &spec.abilities {
            base.abilities.push(factory());
        }
        let tok = self.create_object(base, controller, true, None);
        {
            let o = self.obj_mut(tok);
            o.zone = Zone::Battlefield;
            o.controller = controller;
            o.tapped = tapped.unwrap_or(spec.tapped);
            o.entered_this_turn = true;
        }
        self.player_mut(controller).battlefield.push(tok);
        self.player_mut(controller).stat("tokens_created", 1);
        if treasure {
            self.player_mut(controller).stat("treasures_made", 1);
        }
        let pt = spec.power.map(|p| {
            format!(
                "{p}/{}",
                spec.toughness
                    .map_or_else(|| "None".to_owned(), |t| t.to_string())
            )
        });
        let who = self.name_of(controller);
        self.log(
            "token",
            vec![
                field("who", who),
                field("name", spec.name.clone()),
                field("pt", LogValue::from(pt)),
            ],
        );
        self.bump();
        self.queue_triggers(&Event::about(EventType::EntersBattlefield, tok));
        tok
    }

    /// Draw `n` cards, one draw event at a time (rules 121.1, 121.4).
    #[rule("121.1", "121.4")]
    pub fn draw(&mut self, player: PlayerId, n: i64) {
        for _ in 0..n {
            if self
                .emit(Event::for_player(EventType::Draw, player))
                .is_none()
            {
                continue;
            }
            if self.player(player).library.is_empty() {
                self.player_mut(player).drew_from_empty = true; // rule 704.5b, lose later
                continue;
            }
            let card = self.player_mut(player).library.remove(0);
            self.obj_mut(card).zone = Zone::Hand;
            self.player_mut(player).hand.push(card);
            self.player_mut(player).stat("cards_drawn", 1);
            let who = self.name_of(player);
            self.log("draw", vec![field("who", who)]);
        }
        self.bump();
    }

    /// Deal damage from `source` to a player or permanent (rule 120).
    ///
    /// Damage from a source with lifelink also makes its controller gain
    /// that much life (rule 702.15b), through the normal life-gain path
    /// so prevention and replacement effects apply.
    #[rule("120.3", "119.3", "702.15b")]
    pub fn deal_damage(
        &mut self,
        source: Option<ObjId>,
        target: Target,
        amount: i64,
        combat: bool,
    ) {
        if amount <= 0 {
            return;
        }
        let Some(event) = self.emit(Event::new(
            EventType::Damage,
            EventData {
                source,
                target: Some(target),
                amount: Some(amount),
                combat: Some(combat),
                ..EventData::default()
            },
        )) else {
            return;
        };
        let amount = event.data.amount.unwrap_or(0);
        let src_ch = source.map(|s| self.chars(s));
        // damage triggers (e.g. "deals combat damage to a player")
        self.queue_triggers(&Event::new(
            EventType::Damage,
            EventData {
                source,
                target: Some(target),
                amount: Some(amount),
                combat: Some(combat),
                resolved: true,
                ..EventData::default()
            },
        ));
        let src = self.lname_source(source);
        let tgt = self.lname_target(target);
        self.log(
            "damage",
            vec![
                field("src", src),
                field("target", tgt),
                field("n", amount),
                field("combat", combat),
            ],
        );
        match target {
            Target::Player(p) => self.damage_player(source, src_ch.as_deref(), p, amount, combat),
            Target::Object(o) => self.damage_permanent(src_ch.as_deref(), o, amount),
            Target::Spell(_) => {}
        }
        if let (Some(src), Some(ch)) = (source, &src_ch)
            && ch.keywords.contains("lifelink")
        {
            let controller = self.obj(src).controller;
            self.gain_life(controller, amount);
        }
    }

    /// Damage to a player: life loss, poison, commander damage.
    #[rule("120.3a", "702.90b", "702.164a", "903.10a")]
    fn damage_player(
        &mut self,
        source: Option<ObjId>,
        src_ch: Option<&Characteristics>,
        target: PlayerId,
        amount: i64,
        combat: bool,
    ) {
        if src_ch.is_some_and(|ch| ch.keywords.contains("infect")) {
            // rule 702.90b: infect damage to a player is poison counters
            // instead of life loss
            self.add_poison(target, amount);
        } else {
            // rule 120.3a: damage to a player causes life loss
            self.lose_life_with(target, amount, true);
        }
        if combat && let Some(ch) = src_ch {
            // rule 702.164a toxic: combat damage also gives poison
            for kw in &ch.keywords {
                if let Some(n) = kw.strip_prefix("toxic:") {
                    self.add_poison(target, n.parse().unwrap_or(0));
                }
            }
        }
        if combat
            && let Some(src) = source
            && self.obj(src).commander
        {
            // rules 903.10a / 704.6c: commander damage
            *self
                .player_mut(target)
                .commander_damage
                .entry(src)
                .or_insert(0) += amount;
        }
    }

    /// Damage to a permanent: loyalty, wither, or marked damage.
    #[rule("120.3c", "702.80")]
    fn damage_permanent(&mut self, src_ch: Option<&Characteristics>, target: ObjId, amount: i64) {
        let ch = self.chars(target);
        if ch.types.contains("Planeswalker") {
            // rule 120.3c: damage removes that many loyalty counters
            self.remove_counters(target, "loyalty", amount);
        } else if src_ch.is_some_and(|c| c.keywords.contains("wither")) {
            // rule 702.80: wither deals damage as -1/-1 counters
            self.put_counters(target, "-1/-1", amount);
        } else {
            let deathtouch = src_ch.is_some_and(|c| c.keywords.contains("deathtouch"));
            let o = self.obj_mut(target);
            o.damage += amount;
            if deathtouch {
                o.deathtouch_damage = true; // rule 704.5h
            }
        }
        self.bump();
    }

    /// Give a player poison counters (infect / toxic).
    #[rule("122.1", "702.90b")]
    pub fn add_poison(&mut self, player: PlayerId, n: i64) {
        if n <= 0 {
            return;
        }
        let p = self.player_mut(player);
        p.poison += n;
        p.stat("poison_received", n);
        let (who, total) = (p.name.clone(), p.poison);
        self.log(
            "poison",
            vec![field("who", who), field("n", n), field("total", total)],
        );
        self.bump();
    }

    /// Give the player `n` life (rule 119.3), replaceable.
    pub fn gain_life(&mut self, player: PlayerId, n: i64) {
        if n <= 0 {
            return;
        }
        let Some(event) = self.emit(Event::new(
            EventType::GainLife,
            EventData {
                player: Some(player),
                amount: Some(n),
                ..EventData::default()
            },
        )) else {
            return;
        };
        let amount = event.data.amount.unwrap_or(0);
        let p = self.player_mut(player);
        p.life += amount;
        let (who, total) = (p.name.clone(), p.life);
        self.log(
            "life",
            vec![
                field("who", who),
                field("delta", amount),
                field("total", total),
            ],
        );
        self.bump();
    }

    /// Take `n` life from the player (rule 119.3), replaceable.
    pub fn lose_life(&mut self, player: PlayerId, n: i64) {
        self.lose_life_with(player, n, false);
    }

    /// [`Game::lose_life`] marking whether the loss came from damage.
    pub fn lose_life_with(&mut self, player: PlayerId, n: i64, damage: bool) {
        if n <= 0 {
            return;
        }
        let Some(event) = self.emit(Event::new(
            EventType::LoseLife,
            EventData {
                player: Some(player),
                amount: Some(n),
                damage,
                ..EventData::default()
            },
        )) else {
            return;
        };
        let amount = event.data.amount.unwrap_or(0);
        let p = self.player_mut(player);
        p.life -= amount;
        let (who, total) = (p.name.clone(), p.life);
        self.log(
            "life",
            vec![
                field("who", who),
                field("delta", -amount),
                field("total", total),
            ],
        );
        self.bump();
    }

    /// Put `n` counters of `kind` on a battlefield object (rules 122.1,
    /// 122.6).
    #[rule("122.1", "122.6")]
    pub fn put_counters(&mut self, obj: ObjId, kind: &str, n: i64) {
        if n <= 0 || self.obj(obj).zone != Zone::Battlefield {
            return;
        }
        let controller = self.obj(obj).controller;
        let Some(event) = self.emit(Event::new(
            EventType::PutCounters,
            EventData {
                obj: Some(obj),
                kind: Some(kind.to_owned()),
                count: Some(n),
                controller: Some(controller),
                ..EventData::default()
            },
        )) else {
            return;
        };
        let n = event.data.count.unwrap_or(0);
        if n <= 0 {
            return;
        }
        *self
            .obj_mut(obj)
            .counters
            .entry(kind.to_owned())
            .or_insert(0) += n;
        let controller = self.obj(obj).controller;
        self.player_mut(controller).stat("counters_received", n);
        self.bump();
        self.queue_triggers(&Event::new(
            EventType::PutCounters,
            EventData {
                obj: Some(obj),
                kind: Some(kind.to_owned()),
                count: Some(n),
                resolved: true,
                ..EventData::default()
            },
        ));
    }

    /// Remove up to `n` counters of `kind` from the object.
    pub fn remove_counters(&mut self, obj: ObjId, kind: &str, n: i64) {
        let have = self.obj(obj).counter(kind);
        let take = have.min(n);
        if take != 0 {
            let o = self.obj_mut(obj);
            if have - take == 0 {
                o.counters.shift_remove(kind);
            } else {
                o.counters.insert(kind.to_owned(), have - take);
            }
            self.bump();
        }
    }

    /// Proliferate for `player` (rule 702.87a): give each chosen permanent
    /// one more counter of each kind already there.
    #[rule("702.87")]
    pub fn proliferate(&mut self, player: PlayerId) {
        let picks = self.policy(player).choose_proliferate(self, player);
        for obj in picks {
            let kinds: Vec<String> = self.obj(obj).counters.keys().cloned().collect();
            for kind in kinds {
                self.put_counters(obj, &kind, 1);
            }
        }
        self.player_mut(player).stat("proliferates", 1);
    }

    /// Populate: copy one of the player's creature tokens (rule 701.34).
    #[rule("701.34")]
    pub fn populate(&mut self, player: PlayerId) {
        let tokens: Vec<ObjId> = self
            .player(player)
            .battlefield
            .iter()
            .copied()
            .filter(|o| self.obj(*o).is_token && self.chars(*o).types.contains("Creature"))
            .collect();
        if tokens.is_empty() {
            return;
        }
        let Some(pick) = self.policy(player).choose_populate(self, &tokens) else {
            return;
        };
        let ch = self.chars(pick);
        let base = Rc::clone(&self.obj(pick).base);
        let spec = TokenSpec {
            name: ch.name.clone(),
            power: base.power,
            toughness: base.toughness,
            colors: ch.colors.clone(),
            types: ch.types.clone(),
            subtypes: ch.subtypes.clone(),
            keywords: base.keywords.clone(),
            ..TokenSpec::default()
        };
        self.create_tokens(player, Rc::new(spec), 1, None, None);
    }

    /// Destroy the permanent (rule 701.7); indestructible ignores it.
    #[rule("701.7", "701.7b")]
    pub fn destroy(&mut self, obj: ObjId) {
        if self.obj(obj).zone != Zone::Battlefield {
            return;
        }
        if self.chars(obj).keywords.contains("indestructible") {
            return; // rule 702.12
        }
        if self.emit(Event::about(EventType::Destroy, obj)).is_none() {
            return;
        }
        self.move_zone(obj, Zone::Graveyard);
    }

    /// Exile the permanent (rule 701.9).
    #[rule("701.9")]
    pub fn exile(&mut self, obj: ObjId) {
        if self.obj(obj).zone != Zone::Battlefield {
            return;
        }
        if self.emit(Event::about(EventType::ExileObj, obj)).is_none() {
            return;
        }
        self.move_zone(obj, Zone::Exile);
    }

    /// Sacrifice the permanent (rules 701.22a-b): can't be replaced and
    /// ignores indestructible.
    #[rule("701.22")]
    pub fn sacrifice(&mut self, player: PlayerId, obj: ObjId) {
        if self.obj(obj).zone != Zone::Battlefield || self.obj(obj).controller != player {
            return;
        }
        self.emit(Event::about(EventType::Sacrifice, obj));
        self.move_zone(obj, Zone::Graveyard);
    }

    /// Tap the permanent, firing tap triggers.
    pub fn tap(&mut self, obj: ObjId) {
        if !self.obj(obj).tapped {
            self.obj_mut(obj).tapped = true;
            self.bump();
            self.queue_triggers(&Event::about(EventType::Tap, obj));
        }
    }

    /// Untap the permanent, firing untap triggers.
    pub fn untap(&mut self, obj: ObjId) {
        if self.obj(obj).tapped {
            self.obj_mut(obj).tapped = false;
            self.bump();
            self.queue_triggers(&Event::about(EventType::Untap, obj));
        }
    }

    /// Search the library for up to `n` lands, then shuffle (rule
    /// 701.23).
    #[rule("701.23")]
    pub fn search_lands(
        &mut self,
        player: PlayerId,
        n: i64,
        tapped: bool,
        to_hand: bool,
        basic_only: bool,
    ) {
        let mut found = 0;
        for card in self.player(player).library.clone() {
            if found >= n {
                break;
            }
            let base = Rc::clone(&self.obj(card).base);
            if !base.types.contains("Land") {
                continue;
            }
            if basic_only && !base.supertypes.contains("Basic") {
                continue;
            }
            let library = &mut self.player_mut(player).library;
            if let Some(pos) = library.iter().position(|c| *c == card) {
                library.remove(pos);
            }
            if to_hand {
                self.obj_mut(card).zone = Zone::Hand;
                self.player_mut(player).hand.push(card);
            } else {
                {
                    let o = self.obj_mut(card);
                    o.zone = Zone::Battlefield;
                    o.controller = player;
                }
                self.player_mut(player).battlefield.push(card);
                let timestamp = self.next_timestamp();
                {
                    let o = self.obj_mut(card);
                    o.reset_battlefield_state(timestamp);
                    o.entered_this_turn = true;
                    o.tapped = tapped;
                }
                self.queue_triggers(&Event::about(EventType::EntersBattlefield, card));
            }
            found += 1;
        }
        self.shuffle(player);
    }

    /// Shuffle the player's library (rule 701.24).
    #[rule("701.24")]
    pub fn shuffle(&mut self, player: PlayerId) {
        let mut library = std::mem::take(&mut self.player_mut(player).library);
        self.rng.shuffle(&mut library);
        self.player_mut(player).library = library;
        self.emit(Event::for_player(EventType::Shuffle, player));
        self.bump();
    }

    /// Scry `n` via the player's policy (rule 701.26).
    #[rule("701.26")]
    pub fn scry(&mut self, player: PlayerId, n: i64) {
        let n = usize::try_from(n).unwrap_or(0);
        let library = self.player(player).library.clone();
        let top: Vec<ObjId> = library.iter().take(n).copied().collect();
        let (mut keep, bottom) = self.policy(player).scry(self, player, &top);
        keep.extend(library.iter().skip(n).copied());
        keep.extend(bottom);
        self.player_mut(player).library = keep;
    }

    /// Search the library for any card to hand, then shuffle.
    pub fn tutor(&mut self, player: PlayerId) {
        let pick = self.policy(player).choose_tutor_card(self, player);
        if let Some(pick) = pick
            && let Some(pos) = self.player(player).library.iter().position(|c| *c == pick)
        {
            self.player_mut(player).library.remove(pos);
            self.obj_mut(pick).zone = Zone::Hand;
            self.player_mut(player).hand.push(pick);
        }
        self.shuffle(player);
    }

    /// Exile the permanent and return it to the battlefield.
    pub fn blink(&mut self, obj: ObjId) {
        let owner_ctl = self.obj(obj).controller;
        if self.obj(obj).is_token {
            self.move_zone(obj, Zone::Exile); // token ceases (704.5d)
            return;
        }
        if let Some(moved) = self.move_zone(obj, Zone::Exile)
            && self.obj(moved).zone == Zone::Exile
        {
            self.obj_mut(moved).controller = owner_ctl;
            self.move_zone(moved, Zone::Battlefield);
        }
    }

    /// Counter the spell on the stack (rule 701.5).
    #[rule("701.5")]
    pub fn counter_spell(&mut self, id: StackId) {
        let Some(pos) = self.stack_index(id) else {
            return;
        };
        let spell = self.lname_item(&self.stack[pos]);
        if self.uncounterable(&self.stack[pos]) {
            self.log("uncounterable", vec![field("spell", spell)]);
            return;
        }
        let who = self.name_of(self.stack[pos].controller);
        self.log("counter", vec![field("spell", spell), field("who", who)]);
        let item = self.stack.remove(pos);
        if item.is_spell
            && let StackObject::Spell(card) = item.obj
            && !self.obj(card).is_token
        {
            self.move_zone(card, Zone::Graveyard);
        }
        self.player_mut(item.controller)
            .stat("spells_countered_against", 1);
        self.bump();
    }

    /// "Spells you control can't be countered" statics (e.g. Chimil).
    fn uncounterable(&self, item: &StackItem) -> bool {
        self.player(item.controller).battlefield.iter().any(|obj| {
            self.chars(*obj)
                .abilities
                .iter()
                .any(|ab| ab.as_static().is_some_and(|s| s.uncounterable_spells))
        })
    }

    /// Put a copy of a spell on the stack (rule 707.10).
    ///
    /// The copy keeps the original's targets and X; it is created as a
    /// token object so it ceases to exist in any zone but the stack
    /// (704.5e / 707.10a) and resolves to a token if it is a permanent
    /// spell.
    #[rule("707.10", "707.10a", "704.5e")]
    pub fn copy_spell(&mut self, id: StackId, controller: PlayerId) -> Option<StackId> {
        let pos = self.stack_index(id)?;
        let item = &self.stack[pos];
        if !item.is_spell {
            return None;
        }
        let StackObject::Spell(card) = item.obj else {
            return None;
        };
        let (ability, targets, groups, division, x) = (
            item.ability.clone(),
            item.targets.clone(),
            item.target_groups.clone(),
            item.division.clone(),
            item.x,
        );
        let spell = self.lname_item(item);
        let base = self.obj(card).base.copy();
        let card_ref = self.obj(card).card_ref.clone();
        let copy_obj = self.create_object(base, controller, true, card_ref);
        {
            let o = self.obj_mut(copy_obj);
            o.is_copy = true;
            o.zone = Zone::Stack;
            o.controller = controller;
        }
        let copy_id = self.next_stack_id();
        self.stack.push(StackItem {
            id: copy_id,
            obj: StackObject::Spell(copy_obj),
            source: copy_obj,
            controller,
            ability,
            targets,
            target_groups: groups,
            division,
            x,
            is_spell: true,
        });
        self.player_mut(controller).stat("spells_copied", 1);
        let who = self.name_of(controller);
        self.log("copy", vec![field("spell", spell), field("who", who)]);
        self.bump();
        Some(copy_id)
    }
}
