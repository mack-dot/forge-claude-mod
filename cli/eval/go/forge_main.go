// Forge diff evaluator for Go: the line protocol of cli/forge_diff.py. Forge copies this file next to the
// person's adapter (diff/<check>_diff.go, package main) and builds both into one program.
//
// The adapter defines:
//   func reference(in Input) any            // the compiled proved rule's answer
//   var candidates = map[string]func(Input) any{ "name": ... } // the code under test
// Input is the JSON input. Numbers arrive as json.Number (exact digits); use forgeInt(v) for a *big.Int,
// forgeDafnyInt(v) for a Dafny integer, forgeInt64(v) for code that takes int64 (it panics when the value does
// not fit, as a real caller would fail). Return plain values; *big.Int, json.Number and Dafny integers are
// written with all their digits. A panic is reported as {"__throws__": "panic: <message>"}.
//
// Messy inputs (the unproved wrapper that turns raw JSON into Dafny values): the adapter may also define
//   var wrappers = map[string]func(json.RawMessage) any{ "name": ... }   (or func wrapper(raw json.RawMessage) any)
//   func failClosed(raw json.RawMessage) any  // the spec's fail-closed answer; nil when the proved rule takes it
// Each wrapper gets the RAW JSON bytes of the input and calls the app's real wrapper with them. Forge generates
// forge_messy.go, which binds these names (or empty defaults) to forgeWrappers / forgeFailClosed.
// Test code: NOT proved.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"sort"

	_dafny "dafny"
)

type Input = map[string]any

func forgeInt(v any) *big.Int {
	n, ok := new(big.Int).SetString(fmt.Sprint(v), 10)
	if !ok {
		panic(fmt.Sprintf("not an integer: %v", v))
	}
	return n
}

func forgeDafnyInt(v any) _dafny.Int { return _dafny.IntOfString(forgeInt(v).String()) }

func forgeInt64(v any) int64 {
	n := forgeInt(v)
	if !n.IsInt64() {
		panic(fmt.Sprintf("does not fit in int64: %s", n))
	}
	return n.Int64()
}

// Dafny integers and big integers become exact JSON numbers.
func forgePlain(x any) any {
	switch v := x.(type) {
	case _dafny.Int:
		return json.Number(v.String())
	case *big.Int:
		return json.Number(v.String())
	case map[string]any:
		out := map[string]any{}
		for k, e := range v {
			out[k] = forgePlain(e)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, e := range v {
			out[i] = forgePlain(e)
		}
		return out
	}
	return x
}

func forgeCall(fn func(Input) any, in Input) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = map[string]any{"__throws__": fmt.Sprintf("panic: %v", r)}
		}
	}()
	return forgePlain(fn(in))
}

func forgeCallRaw(fn func(json.RawMessage) any, raw json.RawMessage) (out any) {
	defer func() {
		if r := recover(); r != nil {
			out = map[string]any{"__throws__": fmt.Sprintf("panic: %v", r)}
		}
	}()
	return forgePlain(fn(append(json.RawMessage(nil), raw...))) // a fresh copy per call
}

func main() {
	names := make([]string, 0, len(candidates))
	for n := range candidates {
		names = append(names, n)
	}
	sort.Strings(names)
	wnames := make([]string, 0, len(forgeWrappers))
	for n := range forgeWrappers {
		wnames = append(wnames, n)
	}
	sort.Strings(wnames)
	w := bufio.NewWriter(os.Stdout)
	enc := json.NewEncoder(w)
	enc.Encode(map[string]any{"candidates": append(append([]string{}, names...), wnames...), "messy": wnames})
	w.Flush()
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		if len(sc.Bytes()) == 0 {
			continue
		}
		var msg struct {
			Input json.RawMessage
			Messy bool
		}
		json.Unmarshal(sc.Bytes(), &msg)
		decode := func() Input {
			var in Input
			d := json.NewDecoder(bytes.NewReader(msg.Input))
			d.UseNumber()
			d.Decode(&in)
			return in
		}
		if msg.Messy {
			var ref any
			if forgeFailClosed != nil {
				ref = forgeCallRaw(forgeFailClosed, msg.Input)
			}
			if ref == nil {
				ref = forgeCall(reference, decode())
			}
			cands := map[string]any{}
			for _, n := range wnames {
				cands[n] = forgeCallRaw(forgeWrappers[n], msg.Input)
			}
			enc.Encode(map[string]any{"reference": ref, "candidates": cands})
			w.Flush()
			continue
		}
		out := map[string]any{"reference": forgeCall(reference, decode())}
		cands := map[string]any{}
		for _, n := range names {
			cands[n] = forgeCall(candidates[n], decode())
		}
		for _, n := range wnames {
			cands[n] = forgeCallRaw(forgeWrappers[n], msg.Input)
		}
		out["candidates"] = cands
		enc.Encode(out)
		w.Flush()
	}
}

