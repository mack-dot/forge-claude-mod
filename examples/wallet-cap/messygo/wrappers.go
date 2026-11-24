//go:build forge

// Package messygo: three Go wrappers around the proved wallet rule that take the RAW JSON body {cap, spent, amount},
// as a Go service gets it. THIS FILE IS NOT PROVED. Two are planted on purpose so that the messy fuzz of
// `forge_check.py diff` has something real to find. Do not fix them: the tests depend on these bugs.
//   SpendSafe     never panics: a value that is not a whole number, or 0 <= spent <= cap that does not hold, fails closed.
//   SpendPanics   bug: asserts amount is a json.Number before it checks the type, so a null, missing or text amount panics.
//   SpendCoerces  bug: turns the text "5" into the number 5 (the proved rule never sees text).
// The build tag keeps it out of normal `go build` runs: the compiled proved package "Wallet" exists only in Forge's
// evaluator build.
package messygo

import (
	"bytes"
	"encoding/json"
	"math/big"
	"regexp"

	m_Wallet "Wallet"
	_dafny "dafny"
)

// Refused is the fail-closed answer: nothing spent, no new state.
func Refused() map[string]any { return map[string]any{"ok": false, "spent": nil} }

func decode(raw json.RawMessage) map[string]any {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var m map[string]any
	if d.Decode(&m) != nil {
		return nil
	}
	return m
}

func whole(v any) (*big.Int, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return nil, false
	}
	return new(big.Int).SetString(string(n), 10)
}

func proved(cap, spent, amount *big.Int) map[string]any {
	if spent.Sign() < 0 || spent.Cmp(cap) > 0 {
		return Refused()
	}
	s := m_Wallet.Companion_State_.Create_Wallet_(_dafny.IntOfString(cap.String()), _dafny.IntOfString(spent.String()))
	next, outcome := m_Wallet.Companion_Default___.Spend(s, _dafny.IntOfString(amount.String()))
	return map[string]any{"ok": outcome.Is_Ok(), "spent": json.Number(next.Dtor_spent().String())}
}

func SpendSafe(raw json.RawMessage) map[string]any {
	m := decode(raw)
	cap, ok1 := whole(m["cap"])
	spent, ok2 := whole(m["spent"])
	amount, ok3 := whole(m["amount"])
	if m == nil || !ok1 || !ok2 || !ok3 {
		return Refused()
	}
	return proved(cap, spent, amount)
}

func SpendPanics(raw json.RawMessage) map[string]any {
	m := decode(raw)
	cap, ok1 := whole(m["cap"])
	spent, ok2 := whole(m["spent"])
	if !ok1 || !ok2 {
		return Refused()
	}
	a := m["amount"].(json.Number) // bug: panics when amount is null, missing, text, a boolean, [] or {}
	if r, ok := new(big.Rat).SetString(string(a)); ok && r.Sign() < 0 {
		return map[string]any{"ok": false, "spent": json.Number(spent.String())} // (what the proved rule says for a negative amount)
	}
	amount, ok := whole(a)
	if !ok {
		return Refused()
	}
	return proved(cap, spent, amount)
}

var digits = regexp.MustCompile(`^-?[0-9]+$`)

func SpendCoerces(raw json.RawMessage) map[string]any {
	m := decode(raw)
	if s, isText := m["amount"].(string); isText && digits.MatchString(s) {
		m["amount"] = json.Number(s) // bug
	}
	cap, ok1 := whole(m["cap"])
	spent, ok2 := whole(m["spent"])
	amount, ok3 := whole(m["amount"])
	if !ok1 || !ok2 || !ok3 {
		return Refused()
	}
	return proved(cap, spent, amount)
}
