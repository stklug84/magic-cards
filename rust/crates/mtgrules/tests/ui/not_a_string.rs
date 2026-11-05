#[mtgrules::rule(100.1)]
fn number_token() {}

#[mtgrules::rule("100.1" "117.1")]
fn missing_comma() {}

#[mtgrules::rule]
fn no_numbers() {}

fn main() {}
