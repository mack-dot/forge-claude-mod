//go:build forge

// forge diff adapter (Go) for the messy fuzz: the Go wrappers that take the raw JSON body, and the proved rule.
// Inputs come from wallet_inputs.py. Test code, not proved.
package main

import (
	"bytes"
	"encoding/json"
	"math/big"

	m_Wallet "Wallet"

	"example.com/walletcap/messygo"
)

func reference(in Input) any {
	s := m_Wallet.Companion_State_.Create_Wallet_(forgeDafnyInt(in["cap"]), forgeDafnyInt(in["spent"]))
	next, outcome := m_Wallet.Companion_Default___.Spend(s, forgeDafnyInt(in["amount"]))
	return map[string]any{"ok": outcome.Is_Ok(), "spent": next.Dtor_spent()}
}

// No plain candidates here: the shipped legacy code has its own entry (wallet_diff.go).
var candidates = map[string]func(Input) any{}

// The spec's answer for a value the proved rule does not take: refused, nothing spent. nil: the rule takes it.
func failClosed(raw json.RawMessage) any {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var m map[string]any
	if d.Decode(&m) != nil || m == nil {
		return messygo.Refused()
	}
	n := map[string]*big.Int{}
	for _, k := range []string{"cap", "spent", "amount"} {
		v, ok := m[k].(json.Number)
		b, whole := new(big.Int), false
		if ok {
			_, whole = b.SetString(string(v), 10)
		}
		if !whole {
			return messygo.Refused()
		}
		n[k] = b
	}
	if n["spent"].Sign() < 0 || n["spent"].Cmp(n["cap"]) > 0 {
		return messygo.Refused()
	}
	return nil
}

// Each calls the wrapper with the raw JSON: no conversion here.
var wrappers = map[string]func(json.RawMessage) any{
	"wrapper-safe":    func(r json.RawMessage) any { return messygo.SpendSafe(r) },
	"wrapper-panics":  func(r json.RawMessage) any { return messygo.SpendPanics(r) },
	"wrapper-coerces": func(r json.RawMessage) any { return messygo.SpendCoerces(r) },
}
