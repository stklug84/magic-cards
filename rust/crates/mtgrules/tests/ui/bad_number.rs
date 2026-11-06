#[mtgrules::rule("99.1")]
fn too_short() {}

#[mtgrules::rule("704.5G")]
fn uppercase_letter() {}

#[mtgrules::rule("704.5g", "704.5.6")]
fn too_deep() {}

fn main() {}
