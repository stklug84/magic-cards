//! `json.dumps` with CPython's defaults, byte for byte.
//!
//! The simulator's `--log-file` and `--viz-file` records are written with
//! `json.dumps(record, default=str)`: separators `", "` and `": "`,
//! `ensure_ascii=True` (everything outside `0x20..=0x7e` becomes a
//! `\uXXXX` escape, astral characters as a surrogate pair), dict keys in
//! insertion order, floats in `float.__repr__` form and `NaN` /
//! `Infinity` for non-finite values (`allow_nan=True`).

use std::fmt::Write as _;

use crate::pyfloat;

/// A JSON value with insertion-ordered objects (a Python `dict`).
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    /// `None`
    Null,
    /// `True` / `False`
    Bool(bool),
    /// a Python `int` (arbitrary precision in Python; 128 bits here)
    Int(i128),
    /// a Python `float`
    Float(f64),
    /// a Python `str`
    Str(String),
    /// a Python `list` (or tuple)
    List(Vec<Value>),
    /// a Python `dict` with string keys, in insertion order
    Dict(Vec<(String, Value)>),
}

impl Value {
    /// A dict from `(key, value)` pairs, keeping their order.
    #[must_use]
    pub fn dict<K: Into<String>>(pairs: impl IntoIterator<Item = (K, Value)>) -> Self {
        Value::Dict(pairs.into_iter().map(|(k, v)| (k.into(), v)).collect())
    }

    /// Look a key up in a dict (`None` for other variants or a missing key).
    #[must_use]
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Dict(pairs) => pairs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }
}

impl From<bool> for Value {
    fn from(b: bool) -> Self {
        Value::Bool(b)
    }
}

impl From<i64> for Value {
    fn from(i: i64) -> Self {
        Value::Int(i128::from(i))
    }
}

impl From<i32> for Value {
    fn from(i: i32) -> Self {
        Value::Int(i128::from(i))
    }
}

impl From<usize> for Value {
    fn from(i: usize) -> Self {
        Value::Int(i as i128)
    }
}

impl From<f64> for Value {
    fn from(f: f64) -> Self {
        Value::Float(f)
    }
}

impl From<&str> for Value {
    fn from(s: &str) -> Self {
        Value::Str(s.to_owned())
    }
}

impl From<String> for Value {
    fn from(s: String) -> Self {
        Value::Str(s)
    }
}

impl<T: Into<Value>> From<Vec<T>> for Value {
    fn from(items: Vec<T>) -> Self {
        Value::List(items.into_iter().map(Into::into).collect())
    }
}

impl<T: Into<Value>> From<Option<T>> for Value {
    fn from(opt: Option<T>) -> Self {
        opt.map_or(Value::Null, Into::into)
    }
}

/// `json.dumps(value)` with CPython's default arguments.
#[must_use]
pub fn dumps(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value);
    out
}

fn write_value(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Int(i) => {
            let _ = write!(out, "{i}");
        }
        Value::Float(f) => out.push_str(&float_repr(*f)),
        Value::Str(s) => write_str(out, s),
        Value::List(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_value(out, item);
            }
            out.push(']');
        }
        Value::Dict(pairs) => {
            out.push('{');
            for (i, (k, v)) in pairs.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_str(out, k);
                out.push_str(": ");
                write_value(out, v);
            }
            out.push('}');
        }
    }
}

/// `float.__repr__`, with the JSON spellings of the non-finite values.
fn float_repr(f: f64) -> String {
    if f.is_nan() {
        "NaN".to_owned()
    } else if f.is_infinite() {
        if f > 0.0 { "Infinity" } else { "-Infinity" }.to_owned()
    } else {
        pyfloat::repr(f)
    }
}

/// The `ensure_ascii` string encoder (`ESCAPE_ASCII` in CPython).
fn write_str(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::{Value, dumps};

    #[test]
    fn separators_and_order() {
        let v = Value::dict([("b", Value::from(1)), ("a", Value::from(vec![Value::Null]))]);
        assert_eq!(dumps(&v), r#"{"b": 1, "a": [null]}"#);
    }

    #[test]
    fn ascii_escapes() {
        assert_eq!(
            dumps(&Value::from("ü\n\u{7f}😀")),
            r#""\u00fc\n\u007f\ud83d\ude00""#
        );
    }

    #[test]
    fn floats_use_python_repr() {
        assert_eq!(dumps(&Value::from(1.0)), "1.0");
        assert_eq!(dumps(&Value::from(1e16)), "1e+16");
        assert_eq!(dumps(&Value::from(f64::INFINITY)), "Infinity");
    }
}
