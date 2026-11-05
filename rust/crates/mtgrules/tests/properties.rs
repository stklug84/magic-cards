//! Property tests of the engine's pure layers: the compiler is total
//! over arbitrary oracle text (it reports, it never panics), the mana
//! system's parser and pool keep their invariants, and the canonical
//! order is a permutation.

use std::collections::BTreeSet;

use mtgcards::CardData;
use mtgrules::compiler::{
    compile_card, num, parse_activated_line, parse_add_mana, parse_effect_text, parse_keyword_line,
    parse_static_line, parse_trigger_line, target_spec,
};
use mtgrules::manasys::{ManaPool, land_mana_types, mana_order, parse_cost};
use proptest::prelude::*;

/// Words the oracle templates are built from, so random lines hit the
/// parsers' branches instead of only their fallbacks.
const WORDS: &[&str] = &[
    "when",
    "whenever",
    "this",
    "creature",
    "enters",
    "dies",
    "attacks",
    "at",
    "the",
    "beginning",
    "of",
    "your",
    "upkeep",
    "end",
    "step",
    "each",
    "opponent",
    "loses",
    "you",
    "gain",
    "life",
    "draw",
    "a",
    "card",
    "cards",
    "destroy",
    "target",
    "up",
    "to",
    "two",
    "three",
    "any",
    "number",
    "exile",
    "counter",
    "spell",
    "copy",
    "tap",
    "create",
    "1/1",
    "2/2",
    "green",
    "black",
    "artifact",
    "token",
    "tokens",
    "with",
    "flying",
    "put",
    "-1/-1",
    "+1/+1",
    "counters",
    "on",
    "proliferate",
    "populate",
    "search",
    "library",
    "for",
    "basic",
    "land",
    "battlefield",
    "tapped",
    "scry",
    "deals",
    "damage",
    "divided",
    "as",
    "choose",
    "among",
    "or",
    "and",
    "{T}:",
    "Add",
    "{G}",
    "{B}",
    "{C}",
    "{1}",
    "{2},",
    "{T},",
    "Sacrifice",
    "Cycling",
    "Flying,",
    "deathtouch",
    "Equip",
    "+1:",
    "\u{2212}3:",
    "only",
    "sorcery",
    "spells",
    "control",
    "can't",
    "be",
    "countered",
    "get",
    "+1/+1.",
    "have",
    "trample",
    ".",
    ",",
    ":",
    "X",
    "one",
    "mana",
    "color",
    "commander's",
    "identity",
    "Do",
    "once",
    "turn",
];

fn oracle_line() -> impl Strategy<Value = String> {
    prop::collection::vec(prop::sample::select(WORDS), 0..14).prop_map(|words| words.join(" "))
}

fn oracle_text() -> impl Strategy<Value = String> {
    prop::collection::vec(oracle_line(), 0..4).prop_map(|lines| lines.join("\n"))
}

fn mana_symbols() -> impl Strategy<Value = String> {
    prop::collection::vec(
        prop::sample::select(vec![
            "{W}", "{U}", "{B}", "{R}", "{G}", "{C}", "{X}", "{1}", "{2}", "{10}", "{W/U}",
            "{B/R}", "{2/G}", "{G/P}", "{S}", "{E}", "{", "}", "x",
        ]),
        0..8,
    )
    .prop_map(|parts| parts.concat())
}

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(192))]

    #[test]
    fn the_compiler_is_total_over_arbitrary_text(text in oracle_text(), name in "[A-Z][a-z]{2,8}( [A-Z][a-z]{2,6})?") {
        for line in text.lines() {
            let _ = parse_keyword_line(line);
            let _ = parse_trigger_line(line, &name);
            let _ = parse_activated_line(line, &name);
            let _ = parse_static_line(line, &name);
            if let Some(spec) = target_spec(line) {
                prop_assert!(spec.count >= 1);
                prop_assert!(spec.minimum() <= spec.count);
                prop_assert_eq!(spec.optional, spec.minimum() == 0);
            }
        }
        let (effect, targets) = parse_effect_text(&text, &name);
        prop_assert!(!effect.node_name().is_empty());
        prop_assert!(targets.iter().all(|t| !t.what.is_empty()));
        let _ = parse_add_mana(&text);
        for types in [set(&["Creature"]), set(&["Instant"]), set(&["Land"]), set(&["Artifact", "Creature"]), set(&["Sorcery"])] {
            let card = CardData {
                oracle: text.clone(),
                types,
                mana_cost: "{1}{G}".to_owned(),
                color_identity: set(&["G"]),
                ..CardData::new(&name, "test")
            };
            let ch = compile_card(&card);
            prop_assert_eq!(&ch.name, &name);
        }
    }

    #[test]
    fn count_words_parse(word in "[a-z]{1,5}|[0-9]{1,3}") {
        let _ = num(&word);
    }

    #[test]
    fn parse_cost_is_total_and_mv_is_consistent(symbols in mana_symbols(), x in 0i64..12) {
        let cost = parse_cost(&symbols);
        prop_assert!(cost.generic >= 0 && cost.colorless >= 0 && cost.x_count >= 0);
        prop_assert!(cost.pips.values().all(|n| *n >= 0));
        let pips: i64 = cost.pips.values().sum();
        let hybrid = i64::try_from(cost.hybrid.len()).unwrap();
        prop_assert_eq!(cost.mv(), cost.generic + cost.colorless + pips + hybrid);
        let with_x = cost.with_x(x);
        prop_assert_eq!(with_x.mv(), cost.mv() + x * cost.x_count);
        prop_assert_eq!(with_x.x_count, 0);
        prop_assert_eq!(cost.with_extra_generic(3).mv(), cost.mv() + 3);
        prop_assert!(cost.reduced(5).mv() >= cost.mv() - 5);
        prop_assert!(cost.reduced(5).generic >= 0);
    }

    #[test]
    fn the_pool_never_overpays(
        pool in prop::collection::vec((prop::sample::select(vec!["W", "U", "B", "R", "G", "C"]), 0i64..4), 0..6),
        symbols in mana_symbols(),
    ) {
        let mut mana = ManaPool::new();
        for (t, n) in &pool {
            mana.add(t, *n);
        }
        let before = mana.total();
        let cost = parse_cost(&symbols).with_x(1);
        let payable = mana.can_pay(&cost);
        let paid = mana.pay(&cost);
        prop_assert_eq!(payable, paid);
        prop_assert!(mana.mana.values().all(|n| *n >= 0));
        if paid {
            prop_assert_eq!(mana.total(), before - cost.mv());
        } else {
            prop_assert_eq!(mana.total(), before);
        }
    }

    #[test]
    fn mana_order_is_a_permutation_in_canonical_order(types in prop::collection::btree_set("[A-Z]{1,3}", 0..8)) {
        let ordered = mana_order(types.iter().map(String::as_str));
        prop_assert_eq!(ordered.len(), types.len());
        prop_assert_eq!(&ordered.iter().cloned().collect::<BTreeSet<_>>(), &types);
        let rank = |t: &str| {
            ["W", "U", "B", "R", "G", "C"]
                .iter()
                .position(|c| *c == t)
                .map_or(usize::MAX, |i| i + 1)
        };
        for pair in ordered.windows(2) {
            let (a, b) = (rank(&pair[0]), rank(&pair[1]));
            prop_assert!(a < b || (a == usize::MAX && b == usize::MAX && pair[0] <= pair[1]), "{ordered:?}");
        }
        let (land_types, any) = land_mana_types(types.iter().map(String::as_str));
        prop_assert!(land_types.len() <= types.len());
        prop_assert!(!any || land_types.is_empty() || land_types.len() == 5);
    }
}
