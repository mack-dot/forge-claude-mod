// Forge diff evaluator for Rust: the line protocol of cli/forge_diff.py. Forge generates a small Cargo project
// around this file, the compiled proved code (module `proved`) and the person's adapter (module `adapter`).
//
// The adapter defines:
//   pub fn reference(input: &Value) -> Value
//   pub fn candidates() -> Vec<(&'static str, fn(&Value) -> Value)>
// Helpers here: forge::int(v) parses an exact integer into a Dafny integer; forge::i64(v) and forge::u64(v) for
// code that takes machine integers (they panic when the value does not fit, as a real caller would fail);
// forge::out(&dafny_int) writes a Dafny integer with all its digits. A panic is reported as
// {"__throws__": "panic: <message>"}. Test code: NOT proved.
#![allow(warnings)]
#[path = "proved.rs"]
pub mod proved;
#[path = "adapter.rs"]
pub mod adapter;

pub mod forge {
    pub use serde_json::{json, Value};
    use std::str::FromStr;

    fn digits(v: &Value) -> String {
        match v {
            Value::Number(n) => n.to_string(),
            Value::String(s) => s.clone(),
            other => panic!("not an integer: {}", other),
        }
    }
    pub fn int(v: &Value) -> dafny_runtime::DafnyInt {
        dafny_runtime::DafnyInt::parse_bytes(digits(v).as_bytes(), 10)
    }
    pub fn i64(v: &Value) -> i64 {
        i64::from_str(&digits(v)).unwrap_or_else(|_| panic!("does not fit in i64: {}", v))
    }
    pub fn u64(v: &Value) -> u64 {
        u64::from_str(&digits(v)).unwrap_or_else(|_| panic!("does not fit in u64: {}", v))
    }
    pub fn out(x: &dafny_runtime::DafnyInt) -> Value {
        serde_json::from_str(&format!("{:?}", x)).expect("an integer")
    }
}

use serde_json::{json, Value};
use std::io::{BufRead, Write};

fn call(f: fn(&Value) -> Value, input: &Value) -> Value {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| f(input))) {
        Ok(v) => v,
        Err(e) => {
            let msg = e
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| e.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_default();
            json!({ "__throws__": format!("panic: {}", msg) })
        }
    }
}

fn main() {
    std::panic::set_hook(Box::new(|_| {})); // panics are reported in the protocol, not printed
    let mut cands = adapter::candidates();
    cands.sort_by(|a, b| a.0.cmp(b.0));
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    writeln!(out, "{}", json!({ "candidates": cands.iter().map(|c| c.0).collect::<Vec<_>>() })).unwrap();
    out.flush().unwrap();
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        if line.trim().is_empty() {
            continue;
        }
        let msg: Value = serde_json::from_str(&line).expect("protocol line");
        let input = &msg["input"];
        let mut answers = serde_json::Map::new();
        for (name, f) in &cands {
            answers.insert(name.to_string(), call(*f, input));
        }
        writeln!(out, "{}", json!({ "reference": call(adapter::reference, input), "candidates": answers })).unwrap();
        out.flush().unwrap();
    }
}
