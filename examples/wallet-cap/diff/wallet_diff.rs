// forge diff evaluator adapter (Rust): the shipped Rust wallet code, and the proved rule compiled to Rust.
// Inputs come from wallet_inputs.py. Test code, not proved.
use crate::forge::{self, json, Value};
use crate::proved::Wallet;
use std::rc::Rc;

#[path = "../legacy_wallet.rs"]
mod legacy;

pub fn reference(i: &Value) -> Value {
    let s = Rc::new(Wallet::State::Wallet { cap: forge::int(&i["cap"]), spent: forge::int(&i["spent"]) });
    let (next, outcome) = Wallet::_default::Spend(&s, &forge::int(&i["amount"]));
    json!({ "ok": matches!(&*outcome, Wallet::Outcome::Ok {}), "spent": forge::out(next.spent()) })
}

pub fn candidates() -> Vec<(&'static str, fn(&Value) -> Value)> {
    vec![("legacy-rs", |i| {
        let (ok, spent) = legacy::spend(forge::u64(&i["cap"]), forge::u64(&i["spent"]), forge::i64(&i["amount"]));
        json!({ "ok": ok, "spent": spent })
    })]
}
