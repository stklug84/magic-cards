"""Turn structure (CR 5xx): phases, steps, and turn-based actions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mtgrules.combat import CombatPhase
from mtgrules.cr import rule
from mtgrules.events import Event, EventType
from mtgrules.objects import Zone

if TYPE_CHECKING:
    from mtgrules.game import Game

#: rule 103.8a: only in a two-player game does the starting player skip
#: their first draw step
_TWO_PLAYER_GAME = 2


@rule("500.1", "500.4")
class TurnRunner:
    """Runs one turn: all phases and steps in order (rule 500.1)."""

    def __init__(self, game: Game) -> None:
        """Bind the game."""
        self.game = game

    def take_turn(self) -> None:
        """Run one full turn for the current active player."""
        game = self.game
        active = game.active_player
        active.lands_played = 0
        game.activated_this_turn = set()
        game.turn += 1
        game.log("turn", n=game.turn, who=active.name)

        self._enter("untap")
        self._untap_step()
        self._enter("upkeep")
        self._upkeep_step()
        self._enter("draw")
        if game.turn > 1 or len(game.players) > _TWO_PLAYER_GAME:
            self._draw_step()  # rule 103.8a: first player skips draw
        else:
            self._draw_step_skip_first()
        self._enter("main1")
        self._main_phase()
        self._enter("combat")
        CombatPhase(game).run()
        if game.game_over:
            return
        self._enter("main2")
        self._main_phase()
        self._enter("end")
        self._end_step()
        self._enter("cleanup")
        self._cleanup_step()
        self._end_of_phase()

    def _enter(self, phase: str) -> None:
        """Advance to *phase* and log the transition."""
        game = self.game
        game.phase = phase
        game.log("phase", phase=phase, turn=game.turn, who=game.active_player.name)

    def _end_of_phase(self) -> None:
        """Empty all mana pools (rule 500.4, coarse boundary)."""
        # rule 500.4: mana pools empty at end of each step/phase; we empty
        # at the coarse boundaries the engine exposes
        for p in self.game.players:
            p.mana_pool.empty()

    @rule("502.1", "502.4")
    def _untap_step(self) -> None:
        """No player receives priority during the untap step (502.4)."""
        game = self.game
        active = game.active_player
        for obj in list(active.battlefield):
            obj.entered_this_turn = False
            if obj.tapped:
                obj.tapped = False
        game.bump()

    @rule("503.1")
    def _upkeep_step(self) -> None:
        """Rule 503.1: upkeep triggers, then priority."""
        game = self.game
        game.queue_triggers(
            Event(
                EventType.BEGIN_STEP,
                {"step": "upkeep", "player": game.active_player},
            ),
        )
        game.priority_loop()
        self._pools()

    @rule("504.1")
    def _draw_step(self) -> None:
        """Rule 504.1: the active player draws, then priority."""
        game = self.game
        game.draw(game.active_player, 1)
        game.priority_loop()
        self._pools()

    @rule("103.8a")
    def _draw_step_skip_first(self) -> None:
        """Run the starting player's skipped first draw (rule 103.8a)."""
        self.game.priority_loop()
        self._pools()

    @rule("505.1", "505.6")
    def _main_phase(self) -> None:
        """Run a main phase (sorcery-speed window for the active player)."""
        game = self.game
        game.priority_loop()
        self._pools()

    @rule("513.1")
    def _end_step(self) -> None:
        """Rule 513.1: end-step triggers, then priority."""
        game = self.game
        game.queue_triggers(
            Event(EventType.BEGIN_STEP, {"step": "end", "player": game.active_player}),
        )
        game.priority_loop()
        self._pools()

    @rule("514.1", "514.2", "514.3")
    def _cleanup_step(self) -> None:
        """Rule 514: discard to hand size, remove damage, end 'until EOT'."""
        game = self.game
        active = game.active_player
        # rule 514.1: discard to maximum hand size
        while len(active.hand) > active.max_hand_size:
            pick = game.policy(active).choose_discard(game, active)
            active.hand.remove(pick)
            pick.zone = Zone.GRAVEYARD
            active.graveyard.append(pick)
            game.bump()
        # rule 514.2: remove all marked damage; "until end of turn" ends
        for obj in game.battlefield_objects():
            obj.damage = 0
            obj.deathtouch_damage = False
        game.layers.end_of_turn_cleanup()
        # rule 514.3a: if SBAs / triggers arise, players get priority
        if game.check_state_based_actions() or game.pending_triggers:
            game.priority_loop()
        self._pools()

    def _pools(self) -> None:
        """Empty every mana pool (rule 500.4)."""
        for p in self.game.players:
            p.mana_pool.empty()
