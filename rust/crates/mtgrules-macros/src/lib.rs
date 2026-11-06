//! `#[rule("601.2", "601.2h")]` and `unsupported!("903.4", "reason")`:
//! Comprehensive Rules traceability.
//!
//! The Python engine decorates every rules-implementing function with
//! `@rule(...)`, which validates the rule numbers against the CR text at
//! import time and fills a registry used by the coverage report. The Rust
//! counterpart is the [`macro@rule`] attribute: it checks the rule-number
//! *syntax* at compile time and registers the annotated item in a
//! link-time distributed slice (`mtgrules::cr::RULE_IMPLEMENTATIONS`), so
//! the binaries can validate every number against the newest
//! `MagicCompRules-*.txt` at startup (`mtgrules::cr::check_registry`) and
//! `mtg-cr coverage` can list implementers. [`unsupported!`] is the
//! counterpart of the Python `unsupported(number, reason)` call.
//!
//! Where the Python registry records `module.Class.method`, this one
//! records `module::path::name` plus the source file and line of the
//! annotation: an attribute on a method cannot see the `impl` it sits in,
//! and `file:line` is the more useful pointer anyway.
//!
//! The macros expand to paths under `::mtgrules::cr` and
//! `::mtgrules::__private::linkme`, so they are usable from `mtgrules`
//! itself (which declares `extern crate self as mtgrules`) and from any
//! crate depending on it; `linkme` need not be a direct dependency.

use std::sync::atomic::{AtomicUsize, Ordering};

use proc_macro::{Delimiter, Group, TokenStream, TokenTree};

/// Makes the generated statics unique within one compilation.
static COUNTER: AtomicUsize = AtomicUsize::new(0);

/// A validated rule number in its canonical (dot-less) form, e.g.
/// `704.5g`; the registry stores this form, like the Python
/// `n.rstrip(".")`.
struct RuleNumber {
    canonical: String,
}

/// Validate the syntax of a CR rule number: three digits, optionally
/// `.digits` and a lowercase letter, optionally a trailing dot
/// (`100`, `100.1`, `704.5g`, `117.`).
fn canonical_number(text: &str) -> Option<String> {
    let body = text.strip_suffix('.').unwrap_or(text);
    let (section, rest) = body.split_at_checked(3)?;
    if section.len() != 3 || !section.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    if rest.is_empty() {
        return Some(body.to_owned());
    }
    let sub = rest.strip_prefix('.')?;
    let digits_end = sub.bytes().take_while(u8::is_ascii_digit).count();
    if digits_end == 0 {
        return None;
    }
    match &sub[digits_end..] {
        "" => Some(body.to_owned()),
        letter if letter.len() == 1 && letter.bytes().all(|b| b.is_ascii_lowercase()) => {
            Some(body.to_owned())
        }
        _ => None,
    }
}

/// The content of a plain `"..."` string literal token (no escapes).
fn plain_string(tree: &TokenTree) -> Option<String> {
    let TokenTree::Literal(lit) = tree else {
        return None;
    };
    let text = lit.to_string();
    let inner = text.strip_prefix('"')?.strip_suffix('"')?;
    if inner.contains('\\') {
        return None;
    }
    Some(inner.to_owned())
}

/// Parse `"a", "b", ...` into rule numbers.
fn parse_numbers(attr: TokenStream) -> Result<Vec<RuleNumber>, String> {
    let mut numbers = Vec::new();
    let mut expect_literal = true;
    for tree in attr {
        if expect_literal {
            let Some(text) = plain_string(&tree) else {
                return Err(format!(
                    "#[rule] takes plain string literals (\"704.5g\"), found `{tree}`"
                ));
            };
            let Some(canonical) = canonical_number(&text) else {
                return Err(format!(
                    "#[rule]: {text:?} is not a CR rule number (expected \"NNN\", \"NNN.N\" or \"NNN.Nx\")"
                ));
            };
            numbers.push(RuleNumber { canonical });
            expect_literal = false;
        } else {
            match &tree {
                TokenTree::Punct(p) if p.as_char() == ',' => expect_literal = true,
                other => return Err(format!("#[rule]: expected `,`, found `{other}`")),
            }
        }
    }
    if numbers.is_empty() {
        return Err("#[rule] needs at least one rule number".to_owned());
    }
    Ok(numbers)
}

/// `compile_error!("...")` at the call site.
fn compile_error(message: &str) -> TokenStream {
    let escaped = message.replace('\\', "\\\\").replace('"', "\\\"");
    format!("::core::compile_error!(\"{escaped}\");")
        .parse()
        .expect("valid compile_error")
}

/// Kind and name of the item an attribute sits on.
struct ItemHead {
    kind: &'static str,
    name: String,
}

const ITEM_KEYWORDS: &[&str] = &[
    "fn", "struct", "enum", "union", "trait", "mod", "const", "static", "type",
];
const QUALIFIERS: &[&str] = &[
    "pub", "const", "unsafe", "async", "extern", "default", "safe",
];

/// Find the item keyword and its name, skipping attributes, visibility
/// and qualifiers (`pub(crate) const unsafe fn name`).
fn item_head(tokens: &[TokenTree]) -> Result<ItemHead, String> {
    let mut i = 0;
    while i < tokens.len() {
        match &tokens[i] {
            TokenTree::Punct(p) if p.as_char() == '#' => {
                // an attribute: `#` then the bracket group
                i += 2;
            }
            // a `pub(crate)` visibility group or an `extern "C"` ABI
            TokenTree::Group(_) | TokenTree::Literal(_) => i += 1,
            TokenTree::Ident(ident) => {
                let word = ident.to_string();
                if word == "impl" {
                    return Err(
                        "#[rule] goes on the functions of an impl block, not on the block"
                            .to_owned(),
                    );
                }
                if ITEM_KEYWORDS.contains(&word.as_str())
                    && !(word == "const" && is_const_fn(tokens, i))
                {
                    return item_name(tokens, i, &word);
                }
                if QUALIFIERS.contains(&word.as_str()) {
                    i += 1;
                } else {
                    return Err(format!("#[rule]: unsupported item starting with `{word}`"));
                }
            }
            other @ TokenTree::Punct(_) => {
                return Err(format!("#[rule]: unexpected `{other}` before the item"));
            }
        }
    }
    Err("#[rule]: no item found".to_owned())
}

/// Whether `const` at `i` qualifies a function rather than naming a
/// constant (`const fn`, `const unsafe fn`, ...).
fn is_const_fn(tokens: &[TokenTree], i: usize) -> bool {
    tokens[i + 1..]
        .iter()
        .take_while(|t| matches!(t, TokenTree::Ident(_)))
        .any(|t| matches!(t, TokenTree::Ident(id) if id.to_string() == "fn"))
}

fn item_name(tokens: &[TokenTree], keyword_at: usize, word: &str) -> Result<ItemHead, String> {
    let kind = ITEM_KEYWORDS
        .iter()
        .copied()
        .find(|k| *k == word)
        .expect("item keyword");
    let mut j = keyword_at + 1;
    // `static mut NAME`
    if let Some(TokenTree::Ident(id)) = tokens.get(j)
        && id.to_string() == "mut"
    {
        j += 1;
    }
    match tokens.get(j) {
        Some(TokenTree::Ident(name)) => Ok(ItemHead {
            kind,
            name: name.to_string(),
        }),
        _ => Err(format!("#[rule]: cannot find the name of this {kind}")),
    }
}

/// The registration static for `numbers` on `name`.
fn registration(numbers: &[RuleNumber], head: &ItemHead) -> String {
    let id = COUNTER.fetch_add(1, Ordering::Relaxed);
    // validated: digits, dots and one lowercase letter only, so quoting
    // the canonical form is a plain string literal
    let literals = numbers
        .iter()
        .map(|n| format!("\"{}\"", n.canonical))
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "#[::mtgrules::__private::linkme::distributed_slice(::mtgrules::cr::RULE_IMPLEMENTATIONS)]
         #[linkme(crate = ::mtgrules::__private::linkme)]
         static __MTG_RULE_IMPL_{id}: ::mtgrules::cr::RuleImpl = ::mtgrules::cr::RuleImpl {{
             numbers: &[{literals}],
             implementer: ::core::concat!(::core::module_path!(), \"::\", \"{name}\"),
             kind: \"{kind}\",
             file: ::core::file!(),
             line: ::core::line!(),
         }};",
        name = head.name,
        kind = head.kind,
    )
}

fn expand_rule(attr: TokenStream, item: TokenStream) -> Result<TokenStream, String> {
    let numbers = parse_numbers(attr)?;
    let tokens: Vec<TokenTree> = item.into_iter().collect();
    let head = item_head(&tokens)?;
    let registration: TokenStream = registration(&numbers, &head)
        .parse()
        .map_err(|e| format!("internal: {e:?}"))?;
    if head.kind == "fn" {
        // inside the body, so methods work too (an impl block cannot hold
        // a static) and the name cannot collide with anything
        let Some(TokenTree::Group(body)) = tokens.last() else {
            return Err("#[rule]: this fn has no body".to_owned());
        };
        if body.delimiter() != Delimiter::Brace {
            return Err("#[rule]: this fn has no body".to_owned());
        }
        let mut new_body = registration;
        new_body.extend(body.stream());
        let mut out: TokenStream = tokens[..tokens.len() - 1].iter().cloned().collect();
        let mut group = Group::new(Delimiter::Brace, new_body);
        group.set_span(body.span());
        out.extend([TokenTree::Group(group)]);
        Ok(out)
    } else {
        let mut out: TokenStream = tokens.into_iter().collect();
        out.extend(registration);
        Ok(out)
    }
}

/// Annotate a function, method, struct, enum, trait, const, static,
/// type alias or module with the Comprehensive Rules it implements.
///
/// ```ignore
/// #[rule("704.5g", "704.5h")]
/// fn creature_dies_sba(game: &mut Game) { /* ... */ }
/// ```
///
/// Rule numbers are checked for syntax here; whether they exist in the CR
/// text is checked at startup by `mtgrules::cr::check_registry`.
#[proc_macro_attribute]
pub fn rule(attr: TokenStream, item: TokenStream) -> TokenStream {
    match expand_rule(attr, item.clone()) {
        Ok(expanded) => expanded,
        Err(message) => {
            let mut out = compile_error(&message);
            out.extend(item);
            out
        }
    }
}

fn expand_unsupported(input: TokenStream) -> Result<TokenStream, String> {
    let trees: Vec<TokenTree> = input.into_iter().collect();
    let [number, comma, reason] = trees.as_slice() else {
        return Err("unsupported! takes two string literals: (\"903.4\", \"reason\")".to_owned());
    };
    if !matches!(comma, TokenTree::Punct(p) if p.as_char() == ',') {
        return Err("unsupported!: expected `,` between the number and the reason".to_owned());
    }
    let text = plain_string(number).ok_or_else(|| {
        format!("unsupported!: expected a plain string literal, found `{number}`")
    })?;
    let Some(canonical) = canonical_number(&text) else {
        return Err(format!("unsupported!: {text:?} is not a CR rule number"));
    };
    if !matches!(reason, TokenTree::Literal(lit) if lit.to_string().starts_with('"')) {
        return Err(format!(
            "unsupported!: the reason must be a string literal, found `{reason}`"
        ));
    }
    let id = COUNTER.fetch_add(1, Ordering::Relaxed);
    format!(
        "#[::mtgrules::__private::linkme::distributed_slice(::mtgrules::cr::UNSUPPORTED)]
         #[linkme(crate = ::mtgrules::__private::linkme)]
         static __MTG_UNSUPPORTED_{id}: ::mtgrules::cr::Unsupported = ::mtgrules::cr::Unsupported {{
             number: \"{canonical}\",
             reason: {reason},
             file: ::core::file!(),
             line: ::core::line!(),
         }};"
    )
    .parse()
    .map_err(|e| format!("internal: {e:?}"))
}

/// Declare a CR rule (or whole rule family) as out of scope, with a
/// reason, at module level:
///
/// ```ignore
/// unsupported!("903.4", "deck legality is validated by the graph tooling");
/// ```
#[proc_macro]
pub fn unsupported(input: TokenStream) -> TokenStream {
    expand_unsupported(input).unwrap_or_else(|message| compile_error(&message))
}
