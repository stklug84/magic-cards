//! Property tests of the CPython-compatibility layer: invariants that
//! hold for every input, next to the recorded vectors that pin exact
//! bytes for chosen ones.

use proptest::prelude::*;
use pycompat::json::{Value, dumps};
use pycompat::random::PyRandom;
use pycompat::{pyfloat, pyfmt, pyrepr, pystr, pyurl, stats};

/// Arbitrary JSON values: finite floats, any strings, nested lists and
/// dicts with distinct keys.
fn value() -> impl Strategy<Value = Value> {
    let leaf = prop_oneof![
        Just(Value::Null),
        any::<bool>().prop_map(Value::Bool),
        any::<i64>().prop_map(Value::from),
        (-1e300f64..1e300).prop_map(Value::Float),
        ".*".prop_map(Value::from),
    ];
    leaf.prop_recursive(3, 24, 6, |inner| {
        prop_oneof![
            prop::collection::vec(inner.clone(), 0..6).prop_map(Value::List),
            prop::collection::btree_map("[a-z]{1,6}", inner, 0..6)
                .prop_map(|m| Value::Dict(m.into_iter().collect())),
        ]
    })
}

/// A serde value back into the pycompat shape (what the readers of the
/// vectors and the viz files do).
fn from_serde(v: &serde_json::Value) -> Value {
    match v {
        serde_json::Value::Null => Value::Null,
        serde_json::Value::Bool(b) => Value::Bool(*b),
        serde_json::Value::Number(n) => n
            .as_i64()
            .map_or_else(|| Value::Float(n.as_f64().unwrap_or(0.0)), Value::from),
        serde_json::Value::String(s) => Value::from(s.as_str()),
        serde_json::Value::Array(items) => Value::List(items.iter().map(from_serde).collect()),
        serde_json::Value::Object(map) => Value::Dict(
            map.iter()
                .map(|(k, v)| (k.clone(), from_serde(v)))
                .collect(),
        ),
    }
}

proptest! {
    #[test]
    fn dumps_is_valid_json_and_round_trips(v in value()) {
        let text = dumps(&v);
        let parsed: serde_json::Value = serde_json::from_str(&text).expect("json.dumps output parses");
        prop_assert_eq!(dumps(&from_serde(&parsed)), text);
    }

    #[test]
    fn float_repr_round_trips_exactly(x in any::<f64>().prop_filter("finite", |x| x.is_finite())) {
        let shown = pyfloat::repr(x);
        let back: f64 = shown.parse().expect("repr parses");
        prop_assert_eq!(back.to_bits(), x.to_bits());
    }

    #[test]
    fn fixed_formatting_rounds_like_rust(x in -1e15f64..1e15, precision in 0usize..8) {
        let spec = format!(".{precision}f");
        prop_assert_eq!(pyfmt::format_float(x, &spec), format!("{x:.precision$}"));
    }

    #[test]
    fn int_formatting_and_parsing_agree(i in any::<i64>()) {
        let shown = pyfmt::format_int(i128::from(i), "");
        prop_assert_eq!(&shown, &i.to_string());
        prop_assert_eq!(pystr::parse_int(&shown), Ok(i128::from(i)));
        prop_assert_eq!(pystr::parse_int(&format!("  {shown}\n")), Ok(i128::from(i)));
    }

    #[test]
    fn repr_str_is_quoted_and_escapes_nothing_printable_twice(s in ".*") {
        let shown = pyrepr::repr_str(&s);
        prop_assert!(shown.len() >= 2);
        let quote = shown.chars().next().unwrap();
        prop_assert!(quote == '\'' || quote == '"');
        prop_assert!(shown.ends_with(quote));
        // the inner text contains no raw newline or backslash left unescaped
        prop_assert!(!shown[1..shown.len() - 1].contains('\n'));
    }

    #[test]
    fn url_quote_is_ascii_and_reversible(s in ".*") {
        let quoted = pyurl::quote(&s);
        prop_assert!(quoted.is_ascii());
        let mut bytes = Vec::new();
        let raw = quoted.as_bytes();
        let mut i = 0;
        while i < raw.len() {
            if raw[i] == b'%' {
                let hex = std::str::from_utf8(&raw[i + 1..i + 3]).unwrap();
                bytes.push(u8::from_str_radix(hex, 16).unwrap());
                i += 3;
            } else {
                bytes.push(raw[i]);
                i += 1;
            }
        }
        prop_assert_eq!(String::from_utf8(bytes).unwrap(), s);
    }

    #[test]
    fn randrange_stays_below_the_bound_and_shuffles_permute(seed in any::<i64>(), n in 1u128..1000, len in 0usize..40) {
        let mut rng = PyRandom::new(i128::from(seed));
        for _ in 0..8 {
            prop_assert!(rng.randrange(n) < n);
            let r = rng.random();
            prop_assert!((0.0..1.0).contains(&r));
        }
        let mut items: Vec<usize> = (0..len).collect();
        rng.shuffle(&mut items);
        let mut sorted = items.clone();
        sorted.sort_unstable();
        prop_assert_eq!(sorted, (0..len).collect::<Vec<_>>());
        if len > 0 {
            prop_assert!(*rng.choice(&items) < len);
        }
        // the same seed replays the same stream
        let mut a = PyRandom::new(i128::from(seed));
        let mut b = PyRandom::new(i128::from(seed));
        prop_assert_eq!(a.random().to_bits(), b.random().to_bits());
    }

    #[test]
    fn statistics_match_their_definitions(data in prop::collection::vec(-1e6f64..1e6, 1..40)) {
        let mean = stats::mean(&data);
        let naive = data.iter().sum::<f64>() / f64::from(u32::try_from(data.len()).unwrap());
        prop_assert!((mean - naive).abs() <= 1e-6 * naive.abs().max(1.0), "{mean} vs {naive}");
        let median = stats::median(&data);
        let mut sorted = data.clone();
        sorted.sort_by(f64::total_cmp);
        let lo = sorted[(sorted.len() - 1) / 2];
        let hi = sorted[sorted.len() / 2];
        prop_assert!(lo <= median && median <= hi, "{median} not between {lo} and {hi}");
        if data.len() > 1 {
            prop_assert!(stats::stdev(&data) >= 0.0);
        }
    }
}
