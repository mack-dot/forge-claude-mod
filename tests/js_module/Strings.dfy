// Test fixture for the generated ES module (cli/js_wrapper.py): functions on strings, seqs and sets, compiled to
// JavaScript by tests/run_tests.py. Not a check; nothing here is approved.
module Strs {
  predicate IsSpace(c: char) { c == ' ' || c == '\t' || c == '\n' || c == '\U{A0}' || c == '\U{2028}' || c == '\U{3000}' }
  function TrimStart(s: string): string decreases |s| { if |s| > 0 && IsSpace(s[0]) then TrimStart(s[1..]) else s }
  function TrimEnd(s: string): string decreases |s| { if |s| > 0 && IsSpace(s[|s| - 1]) then TrimEnd(s[..|s| - 1]) else s }
  function Trim(s: string): string { TrimEnd(TrimStart(s)) }
  predicate Reserved(s: string) { var t := Trim(s); t == "system" || t == "*" || t == "" }
  function Join(a: string, b: string): string { a + "/" + b }
  predicate InSet(s: string, xs: set<string>) { s in xs }
  function Count(xs: seq<string>, s: string): nat { if |xs| == 0 then 0 else (if xs[0] == s then 1 else 0) + Count(xs[1..], s) }
  function Len(s: string): int { |s| }
  function Reverse(xs: seq<string>): seq<string> { if |xs| == 0 then [] else Reverse(xs[1..]) + [xs[0]] }
}
