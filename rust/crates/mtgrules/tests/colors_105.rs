//! Colours (CR 105): the compiler colours a card by its colours (rule
//! 105.2), which the graph asserts as `:hasColor`, never by its colour
//! identity (rule 903.4), which only Commander deck building and "your
//! commander's colour identity" read. Colouring a card by its identity,
//! as the engine did before, made colourless cards with a coloured
//! identity (Signets, Talismans, devoid creatures) play as coloured.

use std::collections::BTreeSet;

use mtgcards::CardData;
use mtgrules::compiler::compile_card;

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| (*s).to_owned()).collect()
}

/// The colours `compile_card` gives a card with these facts.
fn compiled_colors(
    types: &[&str],
    mana_cost: &str,
    colors: &[&str],
    identity: &[&str],
) -> Vec<String> {
    let card = CardData {
        types: set(types),
        mana_cost: mana_cost.to_owned(),
        colors: set(colors),
        color_identity: set(identity),
        ..CardData::new("Card", "test")
    };
    compile_card(&card).colors.into_iter().collect()
}

#[test]
fn a_colourless_card_with_a_coloured_identity_is_colourless() {
    let colourless = Vec::<String>::new();
    // Azorius Signet: {2}, "{1}, {T}: Add {W}{U}."
    assert_eq!(
        compiled_colors(&["Artifact"], "{2}", &[], &["W", "U"]),
        colourless
    );
    // devoid (rule 702.114): Eldrazi Displacer costs {2}{W} yet is colourless
    assert_eq!(
        compiled_colors(&["Creature"], "{2}{W}", &[], &["W"]),
        colourless
    );
}

#[test]
fn the_identity_does_not_widen_the_colours() {
    // Lingering Souls: {2}{W}, flashback {1}{B}
    assert_eq!(
        compiled_colors(&["Sorcery"], "{2}{W}", &["W"], &["W", "B"]),
        ["W"]
    );
}

#[test]
fn a_colour_indicator_colours_a_card_without_a_mana_cost() {
    // Dryad Arbor: a land creature with a green colour indicator
    assert_eq!(
        compiled_colors(&["Land", "Creature"], "", &["G"], &["G"]),
        ["G"]
    );
}

#[test]
fn only_the_five_colours_are_colours() {
    // colourless is not a colour (rule 105.2c); stray letters are dropped
    assert_eq!(
        compiled_colors(&["Creature"], "{1}{G}", &["G", "C", "X"], &["G"]),
        ["G"]
    );
}
