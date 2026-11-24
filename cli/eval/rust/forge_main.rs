// Forge diff evaluator for Rust: the line protocol of cli/forge_diff.py. Forge generates a small Cargo project
// around this file, the compiled proved code (module `proved`) and the person's adapter (module `adapter`).
//
// The adapter defines:
//   pub fn reference(input: &Value) -> Value
//   pub fn candidates() -> Vec<(&'static str, fn(&Value) -> Value)>
// Helpers here: forge::int(v) parses an exact integer into a Dafny integer; forge::i64(v) and forge::u64(v) for
// code that takes machine integers (they panic when the value does not fit, as a real caller would fail);
// forge::out(&dafny_int) writes a Dafny integer with all its digits. A panic is reported as
// {"__throws__": "panic: <message>"}.
//
// Messy inputs (the unproved wrapper that turns raw JSON into Dafny values): the adapter may also define
//   pub fn wrappers() -> Vec<(&'static str, fn(&Value) -> Value)>   (or pub fn wrapper(raw: &Value) -> Value)
//   pub fn fail_closed(raw: &Value) -> Option<Value>  // the spec's fail-closed answer; None when the proved rule takes it
// Each wrapper gets the RAW serde_json::Value and calls the app's real wrapper with it. serde_json rejects a lone
// surrogate (\ud800) while it parses, before any wrapper runs, so that messy kind is not sent here ("skip").
// Test code: NOT proved.
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

// Forge points these at the adapter's names when it defines them (eval_build.rust_messy_bindings).
fn messy_wrappers() -> Vec<(&'static str, fn(&Value) -> Value)> {
    /*FORGE_WRAPPERS*/Vec::new()
}
fn messy_fail_closed(v: &Value) -> Option<Value> {
    /*FORGE_FAIL_CLOSED*/None
}

fn call(f: fn(&Value) -> Value, input: &Value) -> Value {
    call_any(|| f(input))
}

fn call_any(f: impl FnOnce() -> Value) -> Value {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
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

// The protocol owns the real stdout. Code under test that prints (println!) writes to stderr instead: before this, one
// println! in the shipped code broke the line protocol (DIFF_ERROR). On Unix: fd 1 is duplicated for the protocol,
// then fd 2 is copied onto fd 1. Elsewhere the protocol keeps stdout (a print there still breaks it).
#[cfg(unix)]
fn protocol_out() -> Box<dyn Write> {
    use std::os::unix::io::FromRawFd;
    extern "C" {
        fn dup(fd: i32) -> i32;
        fn dup2(from: i32, to: i32) -> i32;
    }
    unsafe {
        let fd = dup(1);
        if fd < 0 || dup2(2, 1) < 0 {
            return Box::new(std::io::stdout());
        }
        Box::new(std::io::BufWriter::new(std::fs::File::from_raw_fd(fd)))
    }
}

#[cfg(not(unix))]
fn protocol_out() -> Box<dyn Write> {
    Box::new(std::io::stdout())
}

fn main() {
    std::panic::set_hook(Box::new(|_| {})); // panics are reported in the protocol, not printed
    let mut cands = adapter::candidates();
    cands.sort_by(|a, b| a.0.cmp(b.0));
    let mut wraps = messy_wrappers();
    wraps.sort_by(|a, b| a.0.cmp(b.0));
    let mut out = protocol_out();
    let mut all: Vec<&str> = cands.iter().map(|c| c.0).collect();
    all.extend(wraps.iter().map(|c| c.0));
    let wnames: Vec<&str> = wraps.iter().map(|c| c.0).collect();
    writeln!(out, "{}", json!({ "candidates": all, "messy": wnames, "skip": ["lone-surrogate"] })).unwrap();
    out.flush().unwrap();
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap();
        if line.trim().is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                // Not judged: no Rust value can hold this input (the core counts it as not judged).
                writeln!(out, "{}", json!({ "reference": { "__throws__": format!("serde_json: {}", e) }, "candidates": {} })).unwrap();
                out.flush().unwrap();
                continue;
            }
        };
        let input = &msg["input"];
        let mut answers = serde_json::Map::new();
        if msg["messy"] == Value::Bool(true) {
            let mut reference = call_any(|| messy_fail_closed(input).unwrap_or(Value::Null));
            if reference.is_null() {
                reference = call(adapter::reference, input);
            }
            for (name, f) in &wraps {
                answers.insert(name.to_string(), call(*f, input));
            }
            writeln!(out, "{}", json!({ "reference": reference, "candidates": answers })).unwrap();
            out.flush().unwrap();
            continue;
        }
        for (name, f) in cands.iter().chain(wraps.iter()) {
            answers.insert(name.to_string(), call(*f, input));
        }
        writeln!(out, "{}", json!({ "reference": call(adapter::reference, input), "candidates": answers })).unwrap();
        out.flush().unwrap();
    }
}
