//! State-based actions (rule 704), game end (rule 104) and priority
//! (rules 117, 405.5).

use indexmap::IndexMap;

use super::{COMMANDER_DAMAGE_LOSS, Game, POISON_LOSS_THRESHOLD, field};
use crate::objects::{ObjId, PlayerId, Zone};
use crate::policy::Action;
use crate::rule;

impl Game {
    /// Perform all applicable SBAs; `true` if any happened (rules 704.3,
    /// 704.5).
    #[rule("704.3", "704.5")]
    pub fn check_state_based_actions(&mut self) -> bool {
        let mut acted = self.sba_players();
        acted = self.sba_permanents() || acted;
        acted = self.sba_attachments() || acted;
        self.sba_legend_rule() || acted
    }

    /// Player-loss SBAs: life, empty draw, poison, commander damage.
    ///
    /// All players who meet a loss condition lose simultaneously (rule
    /// 704.3); only then is the game's end decided, so two players losing
    /// at once draw the game instead of the second one "winning" (fix
    /// F4).
    #[rule("704.3", "704.5a", "704.5b", "704.5c", "704.6c")]
    fn sba_players(&mut self) -> bool {
        let mut losers: Vec<(PlayerId, &'static str)> = Vec::new();
        for p in self.alive() {
            let player = self.player(p);
            if player.life <= 0 {
                losers.push((p, "life 0 or less (704.5a)"));
            } else if player.drew_from_empty {
                losers.push((p, "drew from empty library (704.5b)"));
            } else if player.poison >= POISON_LOSS_THRESHOLD {
                losers.push((p, "ten or more poison counters (704.5c)"));
            } else if player
                .commander_damage
                .values()
                .any(|dmg| *dmg >= COMMANDER_DAMAGE_LOSS)
            {
                losers.push((p, "21+ commander damage (903.10a)"));
            }
        }
        for (p, reason) in &losers {
            self.lose(*p, reason);
        }
        if !losers.is_empty() {
            self.check_game_end();
        }
        !losers.is_empty()
    }

    /// Creature/planeswalker death and counter-annihilation SBAs.
    #[rule("704.5f", "704.5g", "704.5h", "704.5i", "704.5q", "702.12b")]
    fn sba_permanents(&mut self) -> bool {
        let mut acted = false;
        for obj in self.battlefield_objects() {
            let ch = self.chars(obj);
            if ch.types.contains("Creature") {
                let tough = ch.toughness.unwrap_or(0);
                if tough <= 0 {
                    // rule 704.5f
                    self.move_zone(obj, Zone::Graveyard);
                    acted = true;
                    continue;
                }
                // rules 704.5g/h destroy; indestructible (702.12b) makes
                // the destruction do nothing, so it is not an action
                let destructible = !ch.keywords.contains("indestructible");
                let (damage, deathtouch) = {
                    let o = self.obj(obj);
                    (o.damage, o.deathtouch_damage)
                };
                if damage >= tough && destructible {
                    // rule 704.5g
                    self.destroy(obj);
                    acted = true;
                    continue;
                }
                if deathtouch && damage > 0 && destructible {
                    // rule 704.5h
                    self.destroy(obj);
                    acted = true;
                    continue;
                }
            }
            if ch.types.contains("Planeswalker") && self.obj(obj).counter("loyalty") <= 0 {
                // rule 704.5i
                self.move_zone(obj, Zone::Graveyard);
                acted = true;
                continue;
            }
            // rule 704.5q: +1/+1 and -1/-1 counters annihilate
            let (plus, minus) = (
                self.obj(obj).counter("+1/+1"),
                self.obj(obj).counter("-1/-1"),
            );
            if plus != 0 && minus != 0 {
                let n = plus.min(minus);
                self.remove_counters(obj, "+1/+1", n);
                self.remove_counters(obj, "-1/-1", n);
                acted = true;
            }
        }
        acted
    }

    /// Aura and Equipment attachment SBAs (rules 704.5m/n).
    #[rule("704.5m", "704.5n")]
    fn sba_attachments(&mut self) -> bool {
        let mut acted = false;
        for obj in self.battlefield_objects() {
            let ch = self.chars(obj);
            let attached_to = self.obj(obj).attached_to;
            let host_on_battlefield =
                attached_to.is_some_and(|h| self.obj(h).zone == Zone::Battlefield);
            if ch.subtypes.contains("Aura") && (attached_to.is_none() || !host_on_battlefield) {
                self.move_zone(obj, Zone::Graveyard); // rule 704.5m
                acted = true;
            } else if ch.subtypes.contains("Equipment")
                && attached_to.is_some()
                && !host_on_battlefield
            {
                self.obj_mut(obj).attached_to = None; // rule 704.5n
                acted = true;
            }
        }
        acted
    }

    /// Apply the legend rule (rule 704.5j).
    #[rule("704.5j")]
    fn sba_legend_rule(&mut self) -> bool {
        let mut acted = false;
        for p in self.alive() {
            let mut named: IndexMap<String, Vec<ObjId>> = IndexMap::new();
            for obj in self.player(p).battlefield.clone() {
                let ch = self.chars(obj);
                if ch.supertypes.contains("Legendary") && !ch.name.is_empty() {
                    named.entry(ch.name.clone()).or_default().push(obj);
                }
            }
            for objs in named.values() {
                if objs.len() > 1 {
                    let keep = self.policy(p).choose_legend(self, objs);
                    for o in objs {
                        if *o != keep {
                            self.move_zone(*o, Zone::Graveyard);
                        }
                    }
                    acted = true;
                }
            }
        }
        acted
    }

    /// Eliminate the player (rule 104.3); the game's end is decided after.
    fn lose(&mut self, player: PlayerId, reason: &str) {
        if self.player(player).lost {
            return;
        }
        {
            let p = self.player_mut(player);
            p.lost = true;
            reason.clone_into(&mut p.lose_reason);
        }
        let who = self.name_of(player);
        self.log(
            "player_loses",
            vec![field("who", who), field("why", reason)],
        );
    }

    /// End the game once eliminations are complete: a sole survivor wins
    /// (rule 104.2a); if every remaining player lost at the same time,
    /// the game is a draw (rule 104.4a).
    #[rule("104.2a", "104.4a")]
    fn check_game_end(&mut self) {
        let alive = self.alive();
        if alive.len() == 1 {
            self.winner = Some(alive[0]);
            self.game_over = true;
        } else if alive.is_empty() {
            self.winner = None;
            self.game_over = true;
        }
    }

    /// Rule 704.3: repeat SBAs plus trigger placement until quiescent.
    #[rule("704.3")]
    fn sba_trigger_cycle(&mut self) {
        loop {
            let acted = self.check_state_based_actions();
            let placed = self.put_triggers_on_stack();
            if self.game_over || (!acted && !placed) {
                return;
            }
        }
    }

    /// Give priority around the table until all players pass, resolving
    /// the stack as needed (rules 117.4, 405.5); the loop ends when the
    /// stack is empty and everyone passed in succession.
    #[rule("117.3", "117.4", "117.5", "405.5", "704.3")]
    pub fn priority_loop(&mut self) {
        loop {
            // rule 704.3: SBAs + triggers before a player gets priority
            self.sba_trigger_cycle();
            if self.game_over {
                return;
            }
            self.priority_round();
            if self.game_over {
                return;
            }
            if !self.stack.is_empty() {
                self.resolve_top(); // rule 117.4
                if self.game_over {
                    return;
                }
                continue;
            }
            return; // step/phase ends
        }
    }

    /// One round of priority: actions until all players pass.
    #[rule("117.3", "117.4")]
    fn priority_round(&mut self) {
        let order = self.players_apnap();
        if order.is_empty() {
            return;
        }
        let mut passes = 0;
        let mut i = 0;
        while passes < order.len() {
            let player = order[i % order.len()];
            i += 1;
            if self.player(player).lost {
                passes += 1;
                continue;
            }
            let Some(action) = self.policy(player).choose_action(self, player) else {
                passes += 1;
                continue;
            };
            if self.perform(player, action) {
                passes = 0;
                // re-run SBAs/triggers between actions
                self.sba_trigger_cycle();
                if self.game_over {
                    return;
                }
            } else {
                passes += 1;
            }
        }
    }

    /// Perform one policy-chosen action; `false` when it was illegal.
    pub fn perform(&mut self, player: PlayerId, action: Action) -> bool {
        match action {
            Action::Cast {
                card,
                x,
                from_command,
            } => self.cast_spell(player, card, x, from_command),
            Action::Activate { obj, ability, x } => self.activate_ability(player, obj, &ability, x),
            Action::Land(card) => self.play_land(player, card),
        }
    }
}
