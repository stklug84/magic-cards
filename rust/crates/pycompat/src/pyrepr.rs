//! `repr()` of the Python objects the viz layer prints verbatim.
//!
//! The terminal UI shows event payloads it has no formatter for as
//! `f"{kind} {data}"`, i.e. the `repr` of a dict: `{'spell': "Olivia's
//! Dragoon", 'who': 'Black B'}`. Strings pick single quotes unless they
//! contain a single quote and no double quote; `\\`, the chosen quote,
//! `\n`, `\r`, `\t` are escaped, other control characters become `\xNN`;
//! `None`, `True`, `False`, `float.__repr__`.
//!
//! Approximation: CPython also escapes non-printable non-ASCII characters
//! (per `str.isprintable`); here only control characters are escaped,
//! which covers every string the engine produces (card and player names).

use std::fmt::Write as _;

use crate::json::Value;
use crate::pyfloat;

/// `repr(value)`.
#[must_use]
pub fn repr(value: &Value) -> String {
    let mut out = String::new();
    write_repr(&mut out, value);
    out
}

fn write_repr(out: &mut String, value: &Value) {
    match value {
        Value::Null => out.push_str("None"),
        Value::Bool(true) => out.push_str("True"),
        Value::Bool(false) => out.push_str("False"),
        Value::Int(i) => {
            let _ = write!(out, "{i}");
        }
        Value::Float(f) => out.push_str(&pyfloat::repr(*f)),
        Value::Str(s) => out.push_str(&repr_str(s)),
        Value::List(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_repr(out, item);
            }
            out.push(']');
        }
        Value::Dict(pairs) => {
            out.push('{');
            for (i, (k, v)) in pairs.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&repr_str(k));
                out.push_str(": ");
                write_repr(out, v);
            }
            out.push('}');
        }
    }
}

/// `repr(s)` for a `str`.
#[must_use]
pub fn repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if c.is_control() => {
                let code = u32::from(c);
                if code < 0x100 {
                    let _ = write!(out, "\\x{code:02x}");
                } else if code < 0x1_0000 {
                    let _ = write!(out, "\\u{code:04x}");
                } else {
                    let _ = write!(out, "\\U{code:08x}");
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::{repr, repr_str};
    use crate::json::Value;

    #[test]
    fn quotes() {
        assert_eq!(repr_str("it's"), "\"it's\"");
        assert_eq!(repr_str("say \"hi\""), "'say \"hi\"'");
        assert_eq!(repr_str("both ' and \""), "'both \\' and \"'");
        assert_eq!(repr_str("\u{7}bell"), "'\\x07bell'");
    }

    #[test]
    fn dict_repr() {
        let v = Value::dict([
            ("spell", Value::from("Olivia's Dragoon")),
            ("x", Value::Null),
        ]);
        assert_eq!(repr(&v), "{'spell': \"Olivia's Dragoon\", 'x': None}");
    }
}
