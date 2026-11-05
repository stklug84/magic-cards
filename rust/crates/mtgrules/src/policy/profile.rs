//! The tunable decision knobs of the default policy.

use pycompat::pyrepr::repr_str;

/// Tunable decision knobs for [`DefaultPolicy`](super::DefaultPolicy).
///
/// Ported from the retired heuristic engine's `AIProfile`. Presets:
/// default, aggressive, control.
#[derive(Clone, Debug, PartialEq)]
pub struct PolicyProfile {
    /// the preset's name
    pub name: String,
    /// >1 attacks into worse boards
    pub aggression: f64,
    /// hold counterspell mana from turn 4 on
    pub hold_reactive_mana: bool,
    /// cast wipes when this far behind in board value
    pub wipe_board_deficit: i64,
    /// minimum threat worth a removal spell
    pub removal_key_threshold: f64,
    /// minimum spell mana value worth countering
    pub counter_value_threshold: i64,
    /// keep a hand with at least this many lands
    pub mulligan_min_lands: usize,
    /// ... and at most this many
    pub mulligan_max_lands: usize,
    /// keep anything after this many mulligans
    pub max_mulligans: usize,
    /// below this life, chump-block freely
    pub race_life: i64,
}

impl Default for PolicyProfile {
    fn default() -> Self {
        PolicyProfile {
            name: "default".to_owned(),
            aggression: 1.0,
            hold_reactive_mana: true,
            wipe_board_deficit: 6,
            removal_key_threshold: 2.0,
            counter_value_threshold: 3,
            mulligan_min_lands: 2,
            mulligan_max_lands: 5,
            max_mulligans: 3,
            race_life: 14,
        }
    }
}

impl PolicyProfile {
    /// The `aggressive` preset.
    #[must_use]
    pub fn aggressive() -> Self {
        PolicyProfile {
            name: "aggressive".to_owned(),
            aggression: 1.5,
            hold_reactive_mana: false,
            wipe_board_deficit: 9,
            counter_value_threshold: 4,
            race_life: 10,
            ..PolicyProfile::default()
        }
    }

    /// The `control` preset.
    #[must_use]
    pub fn control() -> Self {
        PolicyProfile {
            name: "control".to_owned(),
            aggression: 0.7,
            hold_reactive_mana: true,
            wipe_board_deficit: 4,
            removal_key_threshold: 1.5,
            counter_value_threshold: 2,
            ..PolicyProfile::default()
        }
    }
}

/// The preset names, in the Python `PROFILES` order.
pub const PROFILE_NAMES: &[&str] = &["default", "aggressive", "control"];

/// Look up a preset profile; the error is the Python message of the
/// `SystemExit` the CLI turns into exit code 1.
///
/// # Errors
///
/// When `name` is not a preset.
pub fn get_profile(name: &str) -> Result<PolicyProfile, String> {
    match name {
        "default" => Ok(PolicyProfile::default()),
        "aggressive" => Ok(PolicyProfile::aggressive()),
        "control" => Ok(PolicyProfile::control()),
        other => {
            let mut names: Vec<&str> = PROFILE_NAMES.to_vec();
            names.sort_unstable();
            let listed: Vec<String> = names.iter().map(|n| repr_str(n)).collect();
            Err(format!(
                "unknown AI profile {}; choose from [{}]",
                repr_str(other),
                listed.join(", ")
            ))
        }
    }
}
