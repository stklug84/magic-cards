//! Compile-time behaviour of `#[rule]` and `unsupported!`: every item
//! kind compiles and registers; a malformed number, a non-string
//! argument, a body-less fn or an impl block is a compile error with a
//! message naming the problem (expected output under `tests/ui/*.stderr`,
//! regenerate with `TRYBUILD=overwrite cargo test -p mtgrules --test macros`).

#[test]
fn ui() {
    let t = trybuild::TestCases::new();
    t.pass("tests/ui/ok.rs");
    t.compile_fail("tests/ui/bad_number.rs");
    t.compile_fail("tests/ui/not_a_string.rs");
    t.compile_fail("tests/ui/no_body.rs");
    t.compile_fail("tests/ui/on_impl.rs");
    t.compile_fail("tests/ui/unsupported_bad.rs");
}
