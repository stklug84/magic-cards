//! The combat phase (CR 506-511) and combat-relevant keywords (CR 702).

use std::collections::BTreeSet;

use crate::events::{Event, EventData, EventType};
use crate::game::{Game, field};
use crate::objects::{Characteristics, Defender, ObjId, PlayerId, Zone};
use crate::rule;
use crate::stack::Target;

/// Whether `obj` can legally be declared as an attacker (rule 508.1).
#[rule("508.1")]
#[must_use]
pub fn can_attack(game: &Game, obj: ObjId) -> bool {
    let ch = game.chars(obj);
    let o = game.obj(obj);
    if !ch.types.contains("Creature") || o.tapped {
        return false;
    }
    if o.entered_this_turn && !ch.keywords.contains("haste") {
        return false; // rule 302.6
    }
    if ch.keywords.contains("defender") {
        return false; // rule 702.3
    }
    // Kulrath-style locks
    !ch.keywords.contains("shackled")
}

/// landwalk keyword -> the land subtype the defending player must control
const LANDWALK: [(&str, &str); 5] = [
    ("plainswalk", "Plains"),
    ("islandwalk", "Island"),
    ("swampwalk", "Swamp"),
    ("mountainwalk", "Mountain"),
    ("forestwalk", "Forest"),
];

/// Whether `blocker` can legally block `attacker` (rule 509.1b).
///
/// Evasion: flying (702.9c), fear (702.36b), intimidate (702.13b) and
/// landwalk (702.14c). Menace is a count restriction checked after all
/// blockers are declared (702.111b).
#[rule("509.1b", "702.9", "702.13b", "702.14c", "702.36b", "702.111")]
#[must_use]
pub fn can_block(game: &Game, blocker: ObjId, attacker: ObjId) -> bool {
    let ch_b = game.chars(blocker);
    let b = game.obj(blocker);
    if !ch_b.types.contains("Creature") || b.tapped || ch_b.keywords.contains("shackled") {
        return false;
    }
    !evades(game, &game.chars(attacker), &ch_b, b.controller)
}

/// Whether an evasion keyword of the attacker rules this blocker out.
fn evades(game: &Game, ch_a: &Characteristics, ch_b: &Characteristics, defender: PlayerId) -> bool {
    let kw = &ch_a.keywords;
    if kw.contains("flying")
        && !(ch_b.keywords.contains("flying") || ch_b.keywords.contains("reach"))
    {
        return true; // rules 702.9c / 702.17
    }
    let artifact = ch_b.types.contains("Artifact");
    if kw.contains("fear") && !(artifact || ch_b.colors.contains("B")) {
        return true; // rule 702.36b
    }
    if kw.contains("intimidate") && !artifact && ch_b.colors.is_disjoint(&ch_a.colors) {
        return true; // rule 702.13b
    }
    let walked: BTreeSet<&str> = kw
        .iter()
        .filter_map(|k| {
            LANDWALK
                .iter()
                .find(|(walk, _)| *walk == k)
                .map(|(_, land)| *land)
        })
        .collect();
    if walked.is_empty() {
        return false;
    }
    // rule 702.14c: unblockable while the defending player controls a
    // land of the walked type
    game.player(defender).battlefield.iter().any(|land| {
        let ch = game.chars(*land);
        ch.types.contains("Land") && ch.subtypes.iter().any(|s| walked.contains(s.as_str()))
    })
}

/// `(source, target, amount)` of one combat-damage assignment (rule
/// 510.1).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Assignment {
    /// the creature dealing the damage
    pub source: ObjId,
    /// the player or creature damaged
    pub target: Target,
    /// how much
    pub amount: i64,
}

/// The defender of an attacker as a damage target.
fn defender_target(defender: Defender) -> Target {
    match defender {
        Defender::Player(p) => Target::Player(p),
        Defender::Object(o) => Target::Object(o),
    }
}

/// One combat phase: declarations, damage steps, cleanup (CR 506-511).
#[derive(Clone, Debug, Default)]
pub struct CombatPhase {
    /// the declared attackers
    pub attackers: Vec<ObjId>,
}

impl CombatPhase {
    /// A combat phase with no attackers declared yet.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Run the whole combat phase for the active player.
    #[rule("507.1", "508.1", "509.1", "510.1", "511.1")]
    pub fn run(&mut self, game: &mut Game) {
        let active = game.active_player();
        // 507: beginning of combat
        game.queue_triggers(&step_event(EventType::BeginStep, "combat_begin", active));
        game.priority_loop();
        if game.game_over {
            return;
        }
        // 508: declare attackers
        self.declare_attackers(game);
        if game.game_over {
            return;
        }
        if !self.attackers.is_empty() {
            // 509: declare blockers
            self.declare_blockers(game);
            if game.game_over {
                return;
            }
            // 510: combat damage; rule 510.4 - an additional first-strike
            // combat damage step if any first/double strike creature
            let first_strikers = self.combatants(game).into_iter().any(|a| {
                let kw = &game.chars(a).keywords;
                kw.contains("first strike") || kw.contains("double strike")
            });
            if first_strikers {
                self.damage_step(game, true);
                game.priority_loop();
                if game.game_over {
                    return;
                }
            }
            self.damage_step(game, false);
            game.priority_loop();
            if game.game_over {
                return;
            }
        }
        // 511: end of combat
        game.queue_triggers(&step_event(EventType::EndStep, "combat_end", active));
        game.priority_loop();
        for obj in game.battlefield_objects() {
            let o = game.obj_mut(obj);
            o.attacking = None;
            o.blocking = Vec::new();
            o.blocked_by = Vec::new();
        }
    }

    /// All creatures currently in combat (attackers and blockers).
    #[must_use]
    pub fn combatants(&self, game: &Game) -> Vec<ObjId> {
        let mut out = self.attackers.clone();
        for a in &self.attackers {
            out.extend(game.obj(*a).blocked_by.iter().copied());
        }
        out.retain(|o| game.obj(*o).zone == Zone::Battlefield);
        out
    }

    /// Rule 508.1: the active player declares attackers.
    #[rule("508.1", "508.2", "702.20", "702.83b")]
    pub fn declare_attackers(&mut self, game: &mut Game) {
        let active = game.active_player();
        let candidates: Vec<ObjId> = game
            .player(active)
            .battlefield
            .iter()
            .copied()
            .filter(|o| can_attack(game, *o))
            .collect();
        let picks = game
            .policy(active)
            .declare_attackers(game, active, &candidates);
        let active_name = game.name_of(active);
        for (obj, defender) in picks {
            let ch = game.chars(obj);
            {
                let o = game.obj_mut(obj);
                o.attacking = Some(defender);
                if !ch.keywords.contains("vigilance") {
                    o.tapped = true; // rule 702.20
                }
            }
            self.attackers.push(obj);
            game.player_mut(active).stat("attacks", 1);
            let (defending_player, target_name) = match defender {
                Defender::Player(p) => (p, game.name_of(p)),
                Defender::Object(o) => (game.obj(o).controller, game.obj(o).base.name.clone()),
            };
            #[expect(
                clippy::cast_precision_loss,
                reason = "grudges are Python floats summing small powers"
            )]
            let power = ch.power.unwrap_or(0) as f64;
            *game
                .player_mut(defending_player)
                .grudges
                .entry(active_name.clone())
                .or_insert(0.0) += power;
            let card = game.obj(obj).base.name.clone();
            game.log(
                "attack",
                vec![
                    field("who", active_name.clone()),
                    field("card", card),
                    field("target", target_name),
                ],
            );
        }
        // rule 508.1: "attacks" triggers fire once the declaration is
        // complete; "alone" (rule 702.83b, exalted) needs the full list
        let alone = self.attackers.len() == 1;
        for obj in self.attackers.clone() {
            let defender = game.obj(obj).attacking;
            game.queue_triggers(&Event::new(
                EventType::Attacks,
                EventData {
                    obj: Some(obj),
                    defender,
                    alone: Some(alone),
                    ..EventData::default()
                },
            ));
        }
        game.bump();
        if !self.attackers.is_empty() {
            game.priority_loop();
        }
        self.attackers
            .retain(|a| game.obj(*a).zone == Zone::Battlefield);
    }

    /// Rule 509.1: each defending player declares blockers.
    #[rule("509.1", "509.1a")]
    pub fn declare_blockers(&mut self, game: &mut Game) {
        for defender in game.players_apnap().into_iter().skip(1) {
            let mine: Vec<ObjId> = self
                .attackers
                .iter()
                .copied()
                .filter(|a| match game.obj(*a).attacking {
                    Some(Defender::Player(p)) => p == defender,
                    Some(Defender::Object(o)) => game.obj(o).controller == defender,
                    None => false,
                })
                .collect();
            if mine.is_empty() {
                continue;
            }
            let blockers: Vec<ObjId> = game
                .player(defender)
                .battlefield
                .iter()
                .copied()
                .filter(|o| game.chars(*o).types.contains("Creature") && !game.obj(*o).tapped)
                .collect();
            let assignment = game
                .policy(defender)
                .declare_blockers(game, defender, &mine, &blockers);
            for (blocker, attacker) in assignment {
                if !can_block(game, blocker, attacker) {
                    continue;
                }
                game.obj_mut(blocker).blocking.push(attacker);
                game.obj_mut(attacker).blocked_by.push(blocker);
            }
        }
        // rule 702.111b menace: can't be blocked by exactly one creature
        for a in &self.attackers {
            if game.chars(*a).keywords.contains("menace") && game.obj(*a).blocked_by.len() == 1 {
                let blocker = game.obj(*a).blocked_by[0];
                let blocking = &mut game.obj_mut(blocker).blocking;
                if let Some(pos) = blocking.iter().position(|x| x == a) {
                    blocking.remove(pos);
                }
                game.obj_mut(*a).blocked_by = Vec::new();
            }
        }
        for a in &self.attackers {
            for b in game.obj(*a).blocked_by.clone() {
                let who = game.name_of(game.obj(b).controller);
                let blocker = game.obj(b).base.name.clone();
                let attacker = game.obj(*a).base.name.clone();
                game.log(
                    "block",
                    vec![
                        field("who", who),
                        field("blocker", blocker),
                        field("attacker", attacker),
                    ],
                );
            }
        }
        game.bump();
        game.priority_loop();
    }

    /// Whether `obj` deals damage in this damage step (rule 510.4).
    fn strikes(game: &Game, obj: ObjId, first_strike: bool) -> bool {
        let kw = &game.chars(obj).keywords;
        if first_strike {
            return kw.contains("first strike") || kw.contains("double strike");
        }
        !kw.contains("first strike") || kw.contains("double strike")
    }

    /// Assign then deal all combat damage simultaneously (rule 510.2).
    ///
    /// Attackers and blockers are handled independently (rule 510.1): a
    /// blocker strikes in the step its own keywords allow, whether or not
    /// the attacker it blocks deals damage in that step (fixes F2 and F3).
    #[rule("510.1", "510.2", "510.4", "702.2", "702.19")]
    pub fn damage_step(&mut self, game: &mut Game, first_strike: bool) {
        let mut assignments: Vec<Assignment> = Vec::new();
        for a in self.attackers.clone() {
            if game.obj(a).zone != Zone::Battlefield {
                continue;
            }
            let blockers: Vec<ObjId> = game
                .obj(a)
                .blocked_by
                .iter()
                .copied()
                .filter(|b| game.obj(*b).zone == Zone::Battlefield)
                .collect();
            if Self::strikes(game, a, first_strike) {
                assignments.extend(Self::attacker_assignments(game, a, &blockers));
            }
            assignments.extend(Self::blocker_strikes(game, a, &blockers, first_strike));
        }
        for Assignment {
            source,
            target,
            amount,
        } in assignments
        {
            match target {
                Target::Player(_) => {
                    game.deal_damage(Some(source), target, amount, true);
                    let controller = game.obj(source).controller;
                    game.player_mut(controller).stat("combat_damage", amount);
                }
                Target::Object(o) if game.obj(o).zone == Zone::Battlefield => {
                    game.deal_damage(Some(source), target, amount, true);
                }
                _ => {}
            }
        }
    }

    /// Damage assignments of one attacker to its blockers / defender.
    ///
    /// Rule 510.1a: assignment order = policy order; rules 510.1c-d lethal
    /// assignment; deathtouch (702.2b) makes any nonzero amount lethal;
    /// trample (702.19e) assigns the excess to the defender, and all of it
    /// when every blocker has left combat (702.19d).
    #[rule("510.1", "510.1c", "702.2", "702.19")]
    fn attacker_assignments(game: &Game, a: ObjId, blockers: &[ObjId]) -> Vec<Assignment> {
        let mut out = Vec::new();
        let ch = game.chars(a);
        let power = ch.power.unwrap_or(0).max(0);
        let Some(defender) = game.obj(a).attacking else {
            return out;
        };
        if power == 0 {
            return out;
        }
        let defender = defender_target(defender);
        let trample = ch.keywords.contains("trample");
        if blockers.is_empty() {
            // unblocked (510.1c), or blocked but every blocker left and
            // the attacker has trample (702.19d)
            if game.obj(a).blocked_by.is_empty() || trample {
                out.push(Assignment {
                    source: a,
                    target: defender,
                    amount: power,
                });
            }
            return out;
        }
        let mut remaining = power;
        let deathtouch = ch.keywords.contains("deathtouch");
        let last = *blockers.last().expect("non-empty");
        for b in blockers {
            if remaining <= 0 {
                break;
            }
            let toughness = game.chars(*b).toughness.unwrap_or(0);
            let lethal = if deathtouch {
                1
            } else {
                (toughness - game.obj(*b).damage).max(1)
            };
            let mut deal = remaining.min(lethal);
            if *b == last && !trample {
                deal = remaining;
            }
            out.push(Assignment {
                source: a,
                target: Target::Object(*b),
                amount: deal,
            });
            remaining -= deal;
        }
        if remaining > 0 && trample {
            out.push(Assignment {
                source: a,
                target: defender,
                amount: remaining,
            });
        }
        out
    }

    /// Assign the blockers' damage back at the attacker (rule 510.1c).
    #[rule("510.1c")]
    fn blocker_strikes(
        game: &Game,
        a: ObjId,
        blockers: &[ObjId],
        first_strike: bool,
    ) -> Vec<Assignment> {
        let mut out = Vec::new();
        for b in blockers {
            if Self::strikes(game, *b, first_strike) {
                let power = game.chars(*b).power.unwrap_or(0).max(0);
                if power != 0 {
                    out.push(Assignment {
                        source: *b,
                        target: Target::Object(a),
                        amount: power,
                    });
                }
            }
        }
        out
    }
}

/// A begin/end-step event of `step` for `player`.
pub(crate) fn step_event(kind: EventType, step: &str, player: PlayerId) -> Event {
    Event::new(
        kind,
        EventData {
            step: Some(step.to_owned()),
            player: Some(player),
            ..EventData::default()
        },
    )
}
