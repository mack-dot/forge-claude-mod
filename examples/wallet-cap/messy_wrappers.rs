// Three Rust wrappers around the proved wallet rule that take the RAW JSON body {cap, spent, amount} as a
// serde_json::Value, as a Rust service gets it. THIS FILE IS NOT PROVED. Two are planted on purpose so that the messy
// fuzz of `forge_check.py diff` has something real to find. Do not fix them: the tests depend on these bugs.
//   spend_safe     never panics: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
//   spend_panics   bug: unwraps amount as a number before it checks the type, so a null, missing or text amount panics.
//   spend_coerces  bug: turns the text "5" into the number 5 (the proved rule never sees text).
// It is compiled inside Forge's evaluator crate, where the proved code is the module `crate::proved`.
use crate::proved::Wallet;
use dafny_runtime::DafnyInt;
use serde_json::{json, Value};
use std::rc::Rc;

pub fn refused() -> Value {
    json!({ "ok": false, "spent": null }) // fail closed: nothing spent, no new state
}

fn whole_text(s: &str) -> bool {
    let d = s.strip_prefix('-').unwrap_or(s);
    !d.is_empty() && d.bytes().all(|c| c.is_ascii_digit())
}

pub fn whole(v: &Value) -> Option<DafnyInt> {
    match v {
        Value::Number(n) if whole_text(&n.to_string()) => Some(DafnyInt::parse_bytes(n.to_string().as_bytes(), 10)),
        _ => None,
    }
}

fn proved(cap: DafnyInt, spent: DafnyInt, amount: DafnyInt) -> Value {
    let zero = DafnyInt::parse_bytes(b"0", 10);
    if !(zero <= spent && spent <= cap) {
        return refused();
    }
    let (next, outcome) = Wallet::_default::Spend(&Rc::new(Wallet::State::Wallet { cap, spent }), &amount);
    let spent: Value = serde_json::from_str(&format!("{:?}", next.spent())).expect("an integer");
    json!({ "ok": matches!(&*outcome, Wallet::Outcome::Ok {}), "spent": spent })
}

pub fn spend_safe(raw: &Value) -> Value {
    match (whole(&raw["cap"]), whole(&raw["spent"]), whole(&raw["amount"])) {
        (Some(c), Some(s), Some(a)) => proved(c, s, a),
        _ => refused(),
    }
}

pub fn spend_panics(raw: &Value) -> Value {
    let (Some(cap), Some(spent)) = (whole(&raw["cap"]), whole(&raw["spent"])) else { return refused() };
    let a = raw["amount"].as_f64().unwrap(); // bug: panics when amount is null, missing, text, a boolean, [] or {}
    if a < 0.0 {
        return json!({ "ok": false, "spent": raw["spent"] }); // (what the proved rule says for a negative amount)
    }
    match whole(&raw["amount"]) {
        Some(amount) => proved(cap, spent, amount),
        None => refused(),
    }
}

pub fn spend_coerces(raw: &Value) -> Value {
    let mut amount = raw["amount"].clone();
    if let Value::String(s) = &amount {
        if whole_text(s) {
            amount = serde_json::from_str(s).unwrap(); // bug
        }
    }
    match (whole(&raw["cap"]), whole(&raw["spent"]), whole(&amount)) {
        (Some(c), Some(s), Some(a)) => proved(c, s, a),
        _ => refused(),
    }
}
