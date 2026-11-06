//! Hand-written card implementations beyond the compiler grammar (the
//! Python `overrides.py`).
//!
//! The rules-engine equivalent of per-card scripts in mature engines:
//! each entry builds real abilities from the card's actual oracle text.
//! Simplifications are marked SIMPLIFIED and reported via [`notes`].

mod cards;

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::rc::Rc;

use mtgcards::CardData;

use crate::Game;
use crate::abilities::{
    Ability, AbilityFactory, StaticAbility, TokenSpec, TriggerSpec, TriggeredAbility,
};
use crate::effects::{Effect, land_mana_ability};
use crate::events::{Event, EventType};
use crate::layers::ContinuousEffect;
use crate::objects::{Characteristics, ObjId, Zone};
use crate::protocols::{CountValue, TriggerCondition};
use crate::replacements::Replacement;

thread_local! {
    /// card name -> note about any simplification made
    static NOTES: RefCell<BTreeMap<String, String>> = const { RefCell::new(BTreeMap::new()) };
}

/// Record a simplification note for the coverage report.
pub fn note(name: &str, text: &str) {
    NOTES.with(|n| {
        n.borrow_mut().insert(name.to_owned(), text.to_owned());
    });
}

/// The simplification notes recorded so far, by card name.
#[must_use]
pub fn notes() -> BTreeMap<String, String> {
    NOTES.with(|n| n.borrow().clone())
}

/// One hand-written card implementation: mutates the compiled base.
pub type OverrideFn = fn(&mut Characteristics, &CardData);

// ---------------------------------------------------------------- helpers

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

fn colors(symbols: &str) -> BTreeSet<String> {
    symbols.chars().map(|c| c.to_string()).collect()
}

/// Trigger on resolved `PutCounters` events of a counter kind.
#[must_use]
pub fn counters_put_cond(kind: &'static str, own_only: bool) -> TriggerCondition {
    Rc::new(move |game: &Game, source: ObjId, event: &Event| {
        if !event.data.resolved {
            return false;
        }
        if event.data.kind.as_deref() != Some(kind) {
            return false;
        }
        let Some(obj) = event.data.obj else {
            return false;
        };
        let o = game.obj(obj);
        if !o.base.types.contains("Creature") {
            return false;
        }
        !(own_only && o.controller != game.obj(source).controller)
    })
}

/// The knobs of a predefined token.
pub struct TokenDef {
    /// the token's name
    pub name: &'static str,
    /// power
    pub power: i64,
    /// toughness
    pub toughness: i64,
    /// the colour symbols, e.g. `"G"`
    pub colors: &'static str,
    /// the card types
    pub types: &'static [&'static str],
    /// the subtypes
    pub subtypes: &'static [&'static str],
    /// the keywords
    pub keywords: &'static [&'static str],
    /// whether the token enters tapped
    pub tapped: bool,
}

impl TokenDef {
    /// A `power`/`toughness` creature token of `colors`.
    #[must_use]
    pub const fn creature(
        name: &'static str,
        power: i64,
        toughness: i64,
        colors: &'static str,
    ) -> Self {
        TokenDef {
            name,
            power,
            toughness,
            colors,
            types: &["Creature"],
            subtypes: &[],
            keywords: &[],
            tapped: false,
        }
    }

    /// Build the token spec (with `abilities` the token's own).
    #[must_use]
    pub fn spec_with(&self, abilities: Vec<AbilityFactory>) -> TokenSpec {
        TokenSpec {
            name: self.name.to_owned(),
            power: Some(self.power),
            toughness: Some(self.toughness),
            colors: colors(self.colors),
            types: set(self.types),
            subtypes: set(self.subtypes),
            keywords: set(self.keywords),
            tapped: self.tapped,
            abilities,
            ..TokenSpec::default()
        }
    }

    /// Build the token spec.
    #[must_use]
    pub fn spec(&self) -> TokenSpec {
        self.spec_with(Vec::new())
    }
}

/// 1/1 green Snake with deathtouch.
pub const SNAKE: TokenDef = TokenDef {
    subtypes: &["Snake"],
    keywords: &["deathtouch"],
    ..TokenDef::creature("Snake", 1, 1, "G")
};
/// 1/1 black Insect.
pub const INSECT: TokenDef = TokenDef {
    subtypes: &["Insect"],
    ..TokenDef::creature("Insect", 1, 1, "B")
};
/// 1/1 green Elf Warrior.
pub const ELF: TokenDef = TokenDef {
    subtypes: &["Elf", "Warrior"],
    ..TokenDef::creature("Elf Warrior", 1, 1, "G")
};
/// 2/2 black Zombie.
pub const ZOMBIE: TokenDef = TokenDef {
    subtypes: &["Zombie"],
    ..TokenDef::creature("Zombie", 2, 2, "B")
};
/// 1/1 blue Thopter artifact creature with flying.
pub const THOPTER: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Thopter"],
    keywords: &["flying"],
    ..TokenDef::creature("Thopter", 1, 1, "U")
};
/// 1/1 colorless Servo artifact creature.
pub const SERVO: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Servo"],
    ..TokenDef::creature("Servo", 1, 1, "")
};
/// 1/1 white Soldier.
pub const SOLDIER: TokenDef = TokenDef {
    subtypes: &["Soldier"],
    ..TokenDef::creature("Soldier", 1, 1, "W")
};
/// 1/1 colorless Gnome artifact creature.
pub const GNOME: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Gnome"],
    ..TokenDef::creature("Gnome", 1, 1, "")
};
/// 2/2 colorless Robot artifact creature, enters tapped.
pub const ROBOT: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Robot"],
    tapped: true,
    ..TokenDef::creature("Robot", 2, 2, "")
};
/// 1/1 colorless Myr artifact creature.
pub const MYR: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Myr"],
    ..TokenDef::creature("Myr", 1, 1, "")
};
/// 4/4 colorless Golem artifact creature with flying.
pub const GOLEM: TokenDef = TokenDef {
    types: &["Artifact", "Creature"],
    subtypes: &["Golem"],
    keywords: &["flying"],
    ..TokenDef::creature("Golem", 4, 4, "")
};

/// One Reef Worm chain stage: `(name, power, toughness, deeper)`.
#[derive(Clone, Copy, Debug)]
pub struct ReefStage {
    name: &'static str,
    power: i64,
    toughness: i64,
    deeper: Option<&'static ReefStage>,
}

/// the last Reef Worm stage: a 9/9 Kraken.
pub const REEF_KRAKEN: ReefStage = ReefStage {
    name: "Kraken",
    power: 9,
    toughness: 9,
    deeper: None,
};
/// the middle Reef Worm stage: a 6/6 Whale.
pub const REEF_WHALE: ReefStage = ReefStage {
    name: "Whale",
    power: 6,
    toughness: 6,
    deeper: Some(&REEF_KRAKEN),
};
/// the first Reef Worm stage: a 3/3 Fish.
pub const REEF_FISH: ReefStage = ReefStage {
    name: "Fish",
    power: 3,
    toughness: 3,
    deeper: Some(&REEF_WHALE),
};

/// One Reef Worm chain token, carrying the next stage's trigger.
#[must_use]
pub fn reef_spec(stage: &'static ReefStage) -> TokenSpec {
    let abilities: Vec<AbilityFactory> = vec![reef_chain(stage)];
    TokenDef {
        subtypes: std::slice::from_ref(&stage.name),
        ..TokenDef::creature(stage.name, stage.power, stage.toughness, "U")
    }
    .spec_with(abilities)
}

/// Fish -> Whale -> Kraken death chain (Reef Worm): the dying stage's
/// trigger makes the next one.
fn reef_chain(stage: &'static ReefStage) -> AbilityFactory {
    Rc::new(move || {
        let effect = match stage.deeper {
            Some(deeper) => Effect::create_tokens(CountValue::Fixed(1), reef_spec(deeper)),
            None => Effect::Noop {
                note: String::new(),
            },
        };
        let mut ability = TriggeredAbility::new(TriggerSpec::on(EventType::Dies), effect);
        ability.text = format!("When this {} dies ...", stage.name);
        Ability::from(ability)
    })
}

// ---------------------------------------------------------------- effects

/// Return the dying creature to the battlefield under your control
/// (Necroskitter; rule 603.10a look-back).
#[must_use]
pub fn reanimate() -> Effect {
    Effect::custom("Reanimate", |game: &mut Game, ctx| {
        let Some(obj) = ctx.event_obj else {
            return;
        };
        let o = game.obj(obj);
        if o.zone == Zone::Graveyard && !o.is_token {
            game.obj_mut(obj).controller = ctx.controller;
            game.move_zone(obj, Zone::Battlefield);
            game.player_mut(ctx.controller)
                .stat("necroskitter_steals", 1);
        }
    })
}

// ---------------------------------------------------------------- statics

/// A static ability granting `kw` to (all) creatures.
#[must_use]
pub fn grant_all_creatures(kw: &'static str, controller_only: bool, others: bool) -> StaticAbility {
    let factory = move |_game: &Game, source: ObjId| {
        let applies = move |g: &Game, obj: ObjId, ch: &Characteristics| {
            if !ch.types.contains("Creature") {
                return false;
            }
            if controller_only && g.obj(obj).controller != g.obj(source).controller {
                return false;
            }
            !(others && obj == source)
        };
        let grant = move |_g: &Game, _o: ObjId, ch: &mut Characteristics| {
            ch.keywords.insert(kw.to_owned());
        };
        vec![ContinuousEffect::new(6, "", applies, grant).with_source(Some(source))]
    };
    StaticAbility::continuous(Rc::new(factory), &format!("grant {kw}"))
}

/// "If one or more tokens would be created ... instead" (rule 614.1c).
#[must_use]
pub fn token_doubler(creature_only: bool, factor: i64) -> StaticAbility {
    let factory = move |_game: &Game, source: ObjId| {
        let matches = move |g: &Game, event: &Event| {
            if event.data.controller != Some(g.obj(source).controller) {
                return false;
            }
            !creature_only
                || event
                    .data
                    .spec
                    .as_ref()
                    .is_some_and(|s| s.types.contains("Creature"))
        };
        vec![
            Replacement::new(EventType::CreateToken, move |_g, mut event| {
                event.data.count = event.data.count.map(|n| n * factor);
                Some(event)
            })
            .when(matches)
            .with_source(Some(source)),
        ]
    };
    StaticAbility::replacement(Rc::new(factory), "token doubler")
}

/// "If one or more counters would be put ... twice that many instead".
#[must_use]
pub fn counter_doubler() -> StaticAbility {
    let factory = move |_game: &Game, source: ObjId| {
        let matches = move |g: &Game, event: &Event| {
            if event.data.resolved {
                return false;
            }
            event
                .data
                .obj
                .is_some_and(|obj| g.obj(obj).controller == g.obj(source).controller)
        };
        vec![
            Replacement::new(EventType::PutCounters, |_g, mut event| {
                event.data.count = event.data.count.map(|n| n * 2);
                Some(event)
            })
            .when(matches)
            .with_source(Some(source)),
        ]
    };
    StaticAbility::replacement(Rc::new(factory), "counter doubler")
}

// ---------------------------------------------------------------- registry

/// Look up the hand-written implementation of a card by name.
#[must_use]
pub fn lookup(name: &str) -> Option<OverrideFn> {
    cards::OVERRIDES
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, f)| *f)
}

/// Apply the hand-written implementation for `card`, if one exists.
pub fn apply_override(ch: &mut Characteristics, card: &CardData) -> bool {
    let mut func = lookup(&card.name);
    if func.is_none()
        && let Some((front, _)) = card.name.split_once(" // ")
    {
        func = lookup(front);
    }
    let Some(func) = func else {
        return false;
    };
    func(ch, card);
    // land mana facts still apply (e.g. Treasure Vault, Myriad Landscape)
    if ch.types.contains("Land")
        && let Some(land_colors) = card.behavior.land_colors()
        && !ch
            .abilities
            .iter()
            .any(|a| a.as_activated().is_some_and(|ab| ab.is_mana_ability))
    {
        ch.abilities.push(land_mana_ability(&land_colors).into());
    }
    true
}
