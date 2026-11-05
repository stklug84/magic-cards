//! One-shot effect AST (CR 610) executed on spell/ability resolution.
//!
//! Each node performs its instruction through the game API, which routes
//! every state change through the replacement machinery (rule 614) and
//! the event system (rule 603). Nodes are produced by the oracle-text
//! compiler or by hand-authored card overrides.
//!
//! The Python module is a class per node; here [`Effect`] is one enum
//! with a variant per node, each carrying the same fields. The
//! resolution bodies live with the game kernel (`Game::resolve_effect`);
//! this module holds the data model, the resolution context and the
//! helpers that need no game state.

use std::rc::Rc;

use crate::abilities::{ActivatedAbility, TargetSpec, TokenSpec};
use crate::game::Game;
use crate::manasys::{land_mana_types, mana_order};
use crate::objects::{ObjId, PlayerId};
use crate::protocols::{CountValue, OneShot};
use crate::rule;
use crate::stack::Target;

/// Resolution context (rule 608.2): who controls the effect, its source,
/// chosen targets, X, and any resolution-time choices.
#[derive(Clone, Debug)]
pub struct Ctx {
    /// the controller of the effect
    pub controller: PlayerId,
    /// the source object
    pub source: Option<ObjId>,
    /// every chosen target in choice order (see `target_groups`)
    pub targets: Vec<Target>,
    /// the chosen X
    pub x: i64,
    /// for triggered abilities: the object of the triggering event ("that
    /// creature", rule 603.10a look-back)
    pub event_obj: Option<ObjId>,
    /// the chosen targets per `TargetSpec` (rule 601.2c); when empty,
    /// each entry of `targets` is its own group (one target per spec)
    pub target_groups: Vec<Vec<Target>>,
    /// the specs the groups were chosen for (rule 608.2b re-check)
    pub specs: Vec<TargetSpec>,
    /// rule 601.2d: announced division of a "divided" amount, aligned
    /// with the targets of the dividing effect's spec
    pub division: Vec<i64>,
}

impl Ctx {
    /// A context for `controller` with `source`, no targets, X = 0.
    #[must_use]
    pub fn new(controller: PlayerId, source: Option<ObjId>) -> Self {
        Ctx {
            controller,
            source,
            targets: Vec::new(),
            x: 0,
            event_obj: None,
            target_groups: Vec::new(),
            specs: Vec::new(),
            division: Vec::new(),
        }
    }

    /// The targets chosen for the i-th `TargetSpec`.
    #[must_use]
    pub fn group(&self, i: usize) -> Vec<Target> {
        if !self.target_groups.is_empty() {
            return self.target_groups.get(i).cloned().unwrap_or_default();
        }
        self.targets.get(i).map(|t| vec![*t]).unwrap_or_default()
    }

    /// The first target chosen for the i-th spec, if any.
    #[must_use]
    pub fn target(&self, i: usize) -> Option<Target> {
        self.group(i).first().copied()
    }

    /// The i-th spec's targets that are still legal (rule 608.2b).
    #[must_use]
    pub fn legal_group(&self, game: &Game, i: usize) -> Vec<Target> {
        self.group(i)
            .into_iter()
            .filter(|t| game.still_legal_target(*t, self, i))
            .collect()
    }
}

/// Evaluate a count expression: plain number, X, or `f(game, ctx)`.
#[must_use]
pub fn count_value(value: &CountValue, game: &Game, ctx: &Ctx) -> i64 {
    match value {
        CountValue::Fixed(n) => *n,
        CountValue::X => ctx.x,
        CountValue::Dynamic(f) => f(game, ctx),
    }
}

/// The one-shot effect nodes (rule 610.1).
#[derive(Clone, Debug)]
pub enum Effect {
    /// Effects performed in written order (rule 608.2c).
    Sequence(Vec<Effect>),
    /// A hand-written effect body `fn(game, ctx)` (overrides).
    Custom {
        /// the body
        body: CustomBody,
        /// a note for the coverage report
        note: String,
    },
    /// Create N tokens from a `TokenSpec` (rule 111.2).
    CreateTokens {
        /// how many
        count: CountValue,
        /// what
        spec: Rc<TokenSpec>,
        /// `you` (the only value the engine distinguishes)
        controller: String,
        /// `None`: the spec's default
        tapped: Option<bool>,
    },
    /// You (or each player) draw N cards (rule 121.1).
    DrawCards {
        /// how many
        count: CountValue,
        /// `you` | `each` | `controller_of_target`
        who: String,
    },
    /// You gain N life (rule 119.3).
    GainLife {
        /// how much
        amount: CountValue,
    },
    /// A player or each opponent loses N life (rule 119.3).
    LoseLife {
        /// how much
        amount: CountValue,
        /// `you` | `each_opponent` | `target`
        who: String,
    },
    /// Each opponent loses N life, you gain that much (aristocrat drains).
    Drain {
        /// how much
        amount: CountValue,
    },
    /// The source deals N damage (rule 120.1). `to = "target"` deals N to
    /// every target of the spec at `index`; `"divided"` deals the division
    /// announced on casting (rule 601.2d) to the targets still legal.
    DealDamage {
        /// how much
        amount: CountValue,
        /// `target` | `each_creature` | `divided`
        to: String,
        /// the target spec index
        index: usize,
    },
    /// Destroy the target, or every matching permanent (rule 701.7).
    Destroy {
        /// the target spec index
        index: usize,
        /// `""` | `creatures`
        all_of: String,
    },
    /// Exile the target permanent(s) (rule 701.9).
    ExileObj {
        /// the target spec index
        index: usize,
    },
    /// Sacrifice the effect's source (rule 701.22).
    SacrificeSelf,
    /// Return the target (or one of your lands) to its owner's hand.
    ReturnToHand {
        /// the target spec index
        index: usize,
        /// bounce lands returning their controller's own land
        self_land: bool,
    },
    /// Put counters on the target / self / each matching creature (rule
    /// 122.1).
    PutCounters {
        /// `+1/+1` | `-1/-1` | `charge` | ...
        kind: String,
        /// how many
        count: CountValue,
        /// `target` | `self` | `each_creature` | `each_opponent_creature`
        on: String,
    },
    /// Proliferate, N times (rule 702.87).
    Proliferate {
        /// how often
        times: i64,
    },
    /// Ramp: search the library for up to N basic/any lands (rule 701.23).
    SearchLands {
        /// how many
        count: CountValue,
        /// onto the battlefield tapped
        tapped: bool,
        /// into the hand instead
        to_hand: bool,
        /// basic lands only
        basic_only: bool,
    },
    /// Removal rider (Path to Exile / Assassin's Trophy): the destroyed
    /// permanent's controller may search for a basic land.
    TargetControllerBasicLand {
        /// the target spec index
        index: usize,
    },
    /// Swords to Plowshares rider: the target's controller gains life
    /// equal to its (last-known) power.
    TargetControllerGainsPower {
        /// the target spec index
        index: usize,
    },
    /// Feed the Swarm rider: you lose life equal to the target's mana value.
    LoseLifeTargetMV {
        /// the target spec index
        index: usize,
    },
    /// Copy target instant or sorcery spell (rule 707.10).
    CopySpell {
        /// the target spec index
        index: usize,
    },
    /// Dread Tiller style: put a land card from your hand onto the
    /// battlefield.
    PutLandFromHand {
        /// tapped
        tapped: bool,
    },
    /// The Reaper style: put the creature that just died onto the
    /// battlefield under your control (rule 603.10a look-back).
    TakeDeadCreature,
    /// Add mana to the controller's pool (rule 106.2).
    AddMana(AddMana),
    /// Counter the target spell (rule 701.5).
    CounterSpell,
    /// Creatures you control get +P/+T until end of turn.
    PumpAll {
        /// power bonus
        power: i64,
        /// toughness bonus
        toughness: i64,
        /// tokens only
        tokens_only: bool,
    },
    /// Your permanents gain hexproof and indestructible until end of turn.
    ProtectAll,
    /// Populate, N times (rule 701.34).
    Populate {
        /// how often
        times: i64,
    },
    /// You get N energy counters (rule 122.1).
    EnergyGain {
        /// how many
        count: CountValue,
    },
    /// Scry N (rule 701.26).
    Scry {
        /// how many
        count: CountValue,
    },
    /// Search your library for a card and put it into your hand.
    TutorAny,
    /// Untap the source (or the target).
    Untap {
        /// `self` | `target`
        on: String,
    },
    /// Tap the target permanent(s).
    TapTarget {
        /// the target spec index
        index: usize,
    },
    /// Exile target(s), return them to the battlefield (Conjurer's Closet
    /// / Restoration Angel style).
    Blink {
        /// the target spec index
        index: usize,
    },
    /// A clause the compiler recognised but the engine does not model;
    /// recorded on the card so the coverage report can list it.
    Noop {
        /// the clause
        note: String,
    },
}

/// A hand-written effect body (a closure; `Debug` prints a placeholder).
#[derive(Clone)]
pub struct CustomBody(pub OneShot);

impl std::fmt::Debug for CustomBody {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("<custom>")
    }
}

impl Effect {
    /// A hand-written effect.
    #[must_use]
    pub fn custom(note: &str, body: impl Fn(&mut Game, &Ctx) + 'static) -> Self {
        Effect::Custom {
            body: CustomBody(Rc::new(body)),
            note: note.to_owned(),
        }
    }

    /// `CreateTokens` under your control, the spec's tapped default.
    #[must_use]
    pub fn create_tokens(count: impl Into<CountValue>, spec: TokenSpec) -> Self {
        Effect::CreateTokens {
            count: count.into(),
            spec: Rc::new(spec),
            controller: "you".to_owned(),
            tapped: None,
        }
    }

    /// `DrawCards` for you.
    #[must_use]
    pub fn draw(count: impl Into<CountValue>) -> Self {
        Effect::DrawCards {
            count: count.into(),
            who: "you".to_owned(),
        }
    }

    /// `DealDamage` to the targets of spec 0.
    #[must_use]
    pub fn damage(amount: impl Into<CountValue>) -> Self {
        Effect::DealDamage {
            amount: amount.into(),
            to: "target".to_owned(),
            index: 0,
        }
    }

    /// `Destroy` the targets of spec 0.
    #[must_use]
    pub fn destroy() -> Self {
        Effect::Destroy {
            index: 0,
            all_of: String::new(),
        }
    }

    /// `PutCounters` on the targets of spec 0.
    #[must_use]
    pub fn counters(kind: &str, count: impl Into<CountValue>) -> Self {
        Effect::PutCounters {
            kind: kind.to_owned(),
            count: count.into(),
            on: "target".to_owned(),
        }
    }

    /// The Python class name of the node (for dumps and reports).
    #[must_use]
    pub const fn node_name(&self) -> &'static str {
        match self {
            Effect::Sequence(_) => "Sequence",
            Effect::Custom { .. } => "Custom",
            Effect::CreateTokens { .. } => "CreateTokens",
            Effect::DrawCards { .. } => "DrawCards",
            Effect::GainLife { .. } => "GainLife",
            Effect::LoseLife { .. } => "LoseLife",
            Effect::Drain { .. } => "Drain",
            Effect::DealDamage { .. } => "DealDamage",
            Effect::Destroy { .. } => "Destroy",
            Effect::ExileObj { .. } => "ExileObj",
            Effect::SacrificeSelf => "SacrificeSelf",
            Effect::ReturnToHand { .. } => "ReturnToHand",
            Effect::PutCounters { .. } => "PutCounters",
            Effect::Proliferate { .. } => "Proliferate",
            Effect::SearchLands { .. } => "SearchLands",
            Effect::TargetControllerBasicLand { .. } => "TargetControllerBasicLand",
            Effect::TargetControllerGainsPower { .. } => "TargetControllerGainsPower",
            Effect::LoseLifeTargetMV { .. } => "LoseLifeTargetMV",
            Effect::CopySpell { .. } => "CopySpell",
            Effect::PutLandFromHand { .. } => "PutLandFromHand",
            Effect::TakeDeadCreature => "TakeDeadCreature",
            Effect::AddMana(_) => "AddMana",
            Effect::CounterSpell => "CounterSpell",
            Effect::PumpAll { .. } => "PumpAll",
            Effect::ProtectAll => "ProtectAll",
            Effect::Populate { .. } => "Populate",
            Effect::EnergyGain { .. } => "EnergyGain",
            Effect::Scry { .. } => "Scry",
            Effect::TutorAny => "TutorAny",
            Effect::Untap { .. } => "Untap",
            Effect::TapTarget { .. } => "TapTarget",
            Effect::Blink { .. } => "Blink",
            Effect::Noop { .. } => "Noop",
        }
    }

    /// Every node of the tree in pre-order (a sequence yields itself,
    /// then its parts).
    pub fn walk(&self, visit: &mut dyn FnMut(&Effect)) {
        visit(self);
        if let Effect::Sequence(parts) = self {
            for part in parts {
                part.walk(visit);
            }
        }
    }
}

/// Add mana to the controller's pool (rule 106.2).
///
/// `types` lists the produced mana symbols in order: "Add {B}{R}" is
/// `["B", "R"]` and produces both. With `choice` the source produces ONE
/// mana of one of the listed types ("Add {B} or {R}", fix F1).
/// `any_color` / `commander_identity` produce one mana of a colour chosen
/// from WUBRG / the commander's colour identity; `amount` is how many
/// mana of that one chosen colour ("Add three mana of any one color").
#[rule("106.2")]
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AddMana {
    /// e.g. `["C", "C"]` or `["B", "R"]`
    pub types: Vec<String>,
    /// one mana of any colour
    pub any_color: bool,
    /// one mana of a colour in the commander's identity
    pub commander_identity: bool,
    /// one mana of one of `types`
    pub choice: bool,
    /// how many mana of the chosen type
    pub amount: i64,
}

impl Default for AddMana {
    fn default() -> Self {
        AddMana {
            types: Vec::new(),
            any_color: false,
            commander_identity: false,
            choice: false,
            amount: 1,
        }
    }
}

impl AddMana {
    /// "Add {C}{C}" style: the listed symbols, all produced.
    #[must_use]
    pub fn fixed(types: &[&str]) -> Self {
        AddMana {
            types: types.iter().map(|s| (*s).to_owned()).collect(),
            ..AddMana::default()
        }
    }

    /// "Add one mana of any color".
    #[must_use]
    pub fn any() -> Self {
        AddMana {
            any_color: true,
            ..AddMana::default()
        }
    }

    /// Whether the produced mana's type is chosen when it is produced.
    #[must_use]
    pub fn chooses(&self) -> bool {
        self.choice || self.any_color || self.commander_identity
    }

    /// The types the controller may choose from, in canonical order, as
    /// one string of symbols (`"WUBRG"`); `"C"` when nothing is allowed.
    #[must_use]
    pub fn allowed(&self, game: &Game, controller: PlayerId) -> String {
        let joined = if self.commander_identity {
            mana_order(game.commander_identity(controller)).concat()
        } else if self.any_color {
            return "WUBRG".to_owned();
        } else {
            mana_order(&self.types).concat()
        };
        if joined.is_empty() {
            "C".to_owned()
        } else {
            joined
        }
    }
}

/// The `{T}: Add ...` ability of a land known only by its graph colours.
///
/// A multi-colour land offers ONE mana of one of its colours (the "Add
/// {B} or {R}" reading, fix F1); all five colours mean "any color".
#[must_use]
pub fn land_mana_ability<I, S>(colors: I) -> ActivatedAbility
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let (types, any_c) = land_mana_types(colors);
    let choice = types.len() > 1;
    let types = if types.is_empty() {
        vec!["C".to_owned()]
    } else {
        types
    };
    ActivatedAbility {
        tap_cost: true,
        is_mana_ability: true,
        effect: Some(Effect::AddMana(AddMana {
            types,
            any_color: any_c,
            choice,
            ..AddMana::default()
        })),
        text: "{T}: Add mana.".to_owned(),
        ..ActivatedAbility::default()
    }
}

#[cfg(test)]
mod tests {
    use super::{AddMana, Ctx, Effect, land_mana_ability};
    use crate::objects::{ObjId, PlayerId};
    use crate::stack::Target;

    #[test]
    fn groups_fall_back_to_one_target_per_spec() {
        let mut ctx = Ctx::new(PlayerId(0), None);
        ctx.targets = vec![Target::Object(ObjId(1)), Target::Player(PlayerId(1))];
        assert_eq!(ctx.group(1), vec![Target::Player(PlayerId(1))]);
        assert_eq!(ctx.target(2), None);
        ctx.target_groups = vec![
            vec![Target::Object(ObjId(1)), Target::Object(ObjId(2))],
            vec![],
        ];
        assert_eq!(ctx.group(0).len(), 2);
        assert_eq!(ctx.target(1), None);
        assert_eq!(ctx.group(5), Vec::<Target>::new());
    }

    #[test]
    fn land_mana_abilities() {
        let dual = land_mana_ability(["R", "B"]);
        assert!(dual.tap_cost && dual.is_mana_ability);
        let Some(Effect::AddMana(add)) = dual.effect else {
            panic!("AddMana")
        };
        assert_eq!(
            (add.types, add.choice, add.any_color),
            (vec!["B".to_owned(), "R".to_owned()], true, false)
        );
        let Some(Effect::AddMana(five)) = land_mana_ability(["W", "U", "B", "R", "G"]).effect
        else {
            panic!("AddMana")
        };
        assert_eq!(
            (five.types, five.any_color, five.choice),
            (vec!["C".to_owned()], true, false)
        );
        let Some(Effect::AddMana(colorless)) = land_mana_ability(Vec::<&str>::new()).effect else {
            panic!("AddMana")
        };
        assert!(!colorless.chooses());
        assert_eq!(colorless.types, vec!["C".to_owned()]);
        assert!(AddMana::any().chooses());
        assert_eq!(Effect::destroy().node_name(), "Destroy");
    }
}
