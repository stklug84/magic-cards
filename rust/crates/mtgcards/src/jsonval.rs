//! Helpers over decoded JSON values with Python's semantics.
//!
//! The data layer decodes JSON with `serde_json` (`preserve_order`,
//! `float_roundtrip`) and keeps values as the insertion-ordered
//! [`pycompat::json::Value`], which is what `json.loads` hands the Python
//! code. `str(value)`, truthiness and `type(value).__name__` are the
//! three Python operations the readers apply to such values.

use pycompat::json::Value;
use pycompat::pyrepr;

/// Convert parsed `serde_json` data into an insertion-ordered [`Value`].
///
/// Integers that fit 64 bits stay integers (a Python `int`); any other
/// number is a float.
#[must_use]
pub fn from_serde(v: &serde_json::Value) -> Value {
    match v {
        serde_json::Value::Null => Value::Null,
        serde_json::Value::Bool(b) => Value::Bool(*b),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Value::Int(i128::from(i))
            } else if let Some(u) = n.as_u64() {
                Value::Int(i128::from(u))
            } else {
                Value::Float(n.as_f64().unwrap_or(f64::NAN))
            }
        }
        serde_json::Value::String(s) => Value::Str(s.clone()),
        serde_json::Value::Array(items) => Value::List(items.iter().map(from_serde).collect()),
        serde_json::Value::Object(map) => Value::Dict(
            map.iter()
                .map(|(k, v)| (k.clone(), from_serde(v)))
                .collect(),
        ),
    }
}

/// Convert back to `serde_json` data (for writers that need it).
#[must_use]
pub fn to_serde(v: &Value) -> serde_json::Value {
    match v {
        Value::Null => serde_json::Value::Null,
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::Int(i) => i64::try_from(*i).map_or_else(
            |_| serde_json::Value::String(i.to_string()),
            |n| serde_json::Value::Number(n.into()),
        ),
        Value::Float(f) => serde_json::Number::from_f64(*f)
            .map_or(serde_json::Value::Null, serde_json::Value::Number),
        Value::Str(s) => serde_json::Value::String(s.clone()),
        Value::List(items) => serde_json::Value::Array(items.iter().map(to_serde).collect()),
        Value::Dict(pairs) => serde_json::Value::Object(
            pairs
                .iter()
                .map(|(k, v)| (k.clone(), to_serde(v)))
                .collect(),
        ),
    }
}

/// Python's `str(value)` of a decoded JSON value.
#[must_use]
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Str(s) => s.clone(),
        other => pyrepr::repr(other),
    }
}

/// Python truthiness of a decoded JSON value.
#[must_use]
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Int(i) => *i != 0,
        Value::Float(f) => *f != 0.0,
        Value::Str(s) => !s.is_empty(),
        Value::List(items) => !items.is_empty(),
        Value::Dict(pairs) => !pairs.is_empty(),
    }
}

/// Python's `type(value).__name__` for a decoded JSON value.
#[must_use]
pub fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Int(_) => "int",
        Value::Float(_) => "float",
        Value::Str(_) => "str",
        Value::List(_) => "list",
        Value::Dict(_) => "dict",
    }
}

/// `int(value)` for a decoded JSON value, `None` where Python raises
/// `TypeError` or `ValueError` (`"*"`, `"1+*"`, `null`, lists).
#[must_use]
pub fn int_or_none(value: &Value) -> Option<i64> {
    match value {
        Value::Int(i) => i64::try_from(*i).ok(),
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Float(f) if f.is_finite() => {
            #[expect(
                clippy::cast_possible_truncation,
                reason = "int(float) truncates toward zero"
            )]
            let truncated = f.trunc() as i64;
            Some(truncated)
        }
        Value::Str(s) => pycompat::pystr::parse_int(s)
            .ok()
            .and_then(|i| i64::try_from(i).ok()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{from_serde, int_or_none, py_str, truthy, type_name};
    use pycompat::json::Value;

    #[test]
    fn python_views_of_json() {
        let v = from_serde(&serde_json::json!({"a": 1, "b": 1.5, "c": [true, null]}));
        assert_eq!(py_str(&v), "{'a': 1, 'b': 1.5, 'c': [True, None]}");
        assert_eq!(type_name(&v), "dict");
        assert!(truthy(&v));
        assert!(!truthy(&Value::Float(0.0)));
        assert_eq!(int_or_none(&Value::from("6")), Some(6));
        assert_eq!(int_or_none(&Value::from("1+*")), None);
        assert_eq!(int_or_none(&Value::from(6.9)), Some(6));
        assert_eq!(int_or_none(&Value::Null), None);
    }
}
