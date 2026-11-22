//go:build forge

// forge diff evaluator adapter (Go): the shipped Go wallet code, and the proved rule compiled to Go.
// Inputs come from wallet_inputs.py. The build tag keeps this file out of normal `go build` runs.
// Test code, not proved.
package main

import (
	m_Wallet "Wallet"

	"example.com/walletcap/legacygo"
)

func reference(in Input) any {
	s := m_Wallet.Companion_State_.Create_Wallet_(forgeDafnyInt(in["cap"]), forgeDafnyInt(in["spent"]))
	next, outcome := m_Wallet.Companion_Default___.Spend(s, forgeDafnyInt(in["amount"]))
	return map[string]any{"ok": outcome.Is_Ok(), "spent": next.Dtor_spent()}
}

var candidates = map[string]func(Input) any{
	"legacy-go": func(in Input) any {
		ok, spent := legacygo.Spend(forgeInt64(in["cap"]), forgeInt64(in["spent"]), forgeInt64(in["amount"]))
		return map[string]any{"ok": ok, "spent": spent}
	},
}
