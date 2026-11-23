// forge diff adapter (Rust) for the messy fuzz: the Rust wrappers that take the raw serde_json::Value, and the proved
// rule. Inputs come from wallet_inputs.py. Test code, not proved.
use crate::forge::{self, json, Value};
use crate::proved::Wallet;
use std::rc::Rc;

#[path = "../messy_wrappers.rs"]
mod messy;

pub fn reference(i: &Value) -> Value {
    let s = Rc::new(Wallet::State::Wallet { cap: forge::int(&i["cap"]), spent: forge::int(&i["spent"]) });
    let (next, outcome) = Wallet::_default::Spend(&s, &forge::int(&i["amount"]));
    json!({ "ok": matches!(&*outcome, Wallet::Outcome::Ok {}), "spent": forge::out(next.spent()) })
}

// No plain candidates here: the shipped legacy code has its own entry (wallet_diff.rs).
pub fn candidates() -> Vec<(&'static str, fn(&Value) -> Value)> {
    vec![]
}

// The spec's answer for a value the proved rule does not take: refused, nothing spent. None: the rule takes it.
pub fn fail_closed(i: &Value) -> Option<Value> {
    match (messy::whole(&i["cap"]), messy::whole(&i["spent"]), messy::whole(&i["amount"])) {
        (Some(c), Some(s), Some(_)) if forge::int(&json!(0)) <= s && s <= c => None,
        _ => Some(messy::refused()),
    }
}

// Each calls the wrapper with the raw value: no conversion here.
pub fn wrappers() -> Vec<(&'static str, fn(&Value) -> Value)> {
    vec![("wrapper-safe", messy::spend_safe), ("wrapper-panics", messy::spend_panics), ("wrapper-coerces", messy::spend_coerces)]
}
