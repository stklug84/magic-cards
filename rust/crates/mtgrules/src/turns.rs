//! Turn structure (CR 5xx): phases, steps, and turn-based actions.

use crate::combat::{CombatPhase, step_event};
use crate::events::EventType;
use crate::game::{Game, field};
use crate::objects::Zone;
use crate::rule;

/// Rule 103.8a: only in a two-player game does the starting player skip
/// their first draw step.
const TWO_PLAYER_GAME: usize = 2;

impl Game {
    /// Run one full turn for the current active player (rules 500.1,
    /// 500.4): the Python `TurnRunner.take_turn`.
    #[rule("500.1", "500.4")]
    pub fn take_turn(&mut self) {
        let active = self.active_player();
        self.player_mut(active).lands_played = 0;
        self.activated_this_turn.clear();
        self.turn += 1;
        let who = self.name_of(active);
        self.log(
            "turn",
            vec![field("n", i64::from(self.turn)), field("who", who)],
        );

        self.enter_phase("untap");
        self.untap_step();
        self.enter_phase("upkeep");
        self.upkeep_step();
        self.enter_phase("draw");
        if self.turn > 1 || self.players.len() > TWO_PLAYER_GAME {
            self.draw_step(); // rule 103.8a: first player skips draw
        } else {
            self.draw_step_skip_first();
        }
        self.enter_phase("main1");
        self.main_phase();
        self.enter_phase("combat");
        CombatPhase::new().run(self);
        if self.game_over {
            return;
        }
        self.enter_phase("main2");
        self.main_phase();
        self.enter_phase("end");
        self.end_step();
        self.enter_phase("cleanup");
        self.cleanup_step();
        self.empty_pools();
    }

    /// Advance to `phase` and log the transition.
    fn enter_phase(&mut self, phase: &str) {
        phase.clone_into(&mut self.phase);
        let who = self.name_of(self.active_player());
        self.log(
            "phase",
            vec![
                field("phase", phase),
                field("turn", i64::from(self.turn)),
                field("who", who),
            ],
        );
    }

    /// Empty every mana pool (rule 500.4: at the end of each step and
    /// phase, at the coarse boundaries the engine exposes).
    fn empty_pools(&mut self) {
        for p in &mut self.players {
            p.mana_pool.empty();
        }
    }

    /// No player receives priority during the untap step (rule 502.4).
    #[rule("502.1", "502.4")]
    fn untap_step(&mut self) {
        let active = self.active_player();
        for obj in self.player(active).battlefield.clone() {
            let o = self.obj_mut(obj);
            o.entered_this_turn = false;
            o.tapped = false;
        }
        self.bump();
    }

    /// Rule 503.1: upkeep triggers, then priority.
    #[rule("503.1")]
    fn upkeep_step(&mut self) {
        let active = self.active_player();
        self.queue_triggers(&step_event(EventType::BeginStep, "upkeep", active));
        self.priority_loop();
        self.empty_pools();
    }

    /// Rule 504.1: the active player draws, then priority.
    #[rule("504.1")]
    fn draw_step(&mut self) {
        let active = self.active_player();
        self.draw(active, 1);
        self.priority_loop();
        self.empty_pools();
    }

    /// The starting player's skipped first draw (rule 103.8a).
    #[rule("103.8a")]
    fn draw_step_skip_first(&mut self) {
        self.priority_loop();
        self.empty_pools();
    }

    /// A main phase (sorcery-speed window for the active player).
    #[rule("505.1", "505.6")]
    fn main_phase(&mut self) {
        self.priority_loop();
        self.empty_pools();
    }

    /// Rule 513.1: end-step triggers, then priority.
    #[rule("513.1")]
    fn end_step(&mut self) {
        let active = self.active_player();
        self.queue_triggers(&step_event(EventType::BeginStep, "end", active));
        self.priority_loop();
        self.empty_pools();
    }

    /// Rule 514: discard to hand size, remove damage, end "until end of
    /// turn" effects.
    #[rule("514.1", "514.2", "514.3")]
    fn cleanup_step(&mut self) {
        let active = self.active_player();
        // rule 514.1: discard to maximum hand size
        while i64::try_from(self.player(active).hand.len()).unwrap_or(i64::MAX)
            > self.player(active).max_hand_size
        {
            let Some(pick) = self.policy(active).choose_discard(self, active) else {
                break;
            };
            let hand = &mut self.player_mut(active).hand;
            if let Some(pos) = hand.iter().position(|c| *c == pick) {
                hand.remove(pos);
            }
            self.obj_mut(pick).zone = Zone::Graveyard;
            self.player_mut(active).graveyard.push(pick);
            self.bump();
        }
        // rule 514.2: remove all marked damage; "until end of turn" ends
        for obj in self.battlefield_objects() {
            let o = self.obj_mut(obj);
            o.damage = 0;
            o.deathtouch_damage = false;
        }
        self.end_of_turn_cleanup();
        // rule 514.3a: if SBAs / triggers arise, players get priority
        if self.check_state_based_actions() || !self.pending_triggers.is_empty() {
            self.priority_loop();
        }
        self.empty_pools();
    }
}
