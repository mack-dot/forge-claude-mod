// Package Wallet
// Dafny module Wallet compiled into Go

package Wallet

import (
	m__System "System_"
	_dafny "dafny"
	os "os"
)

var _ = os.Args
var _ _dafny.Dummy__
var _ m__System.Dummy__

type Dummy__ struct{}

// Definition of class Default__
type Default__ struct {
	dummy byte
}

func New_Default___() *Default__ {
	_this := Default__{}

	return &_this
}

type CompanionStruct_Default___ struct {
}

var Companion_Default___ = CompanionStruct_Default___{}

func (_this *Default__) Equals(other *Default__) bool {
	return _this == other
}

func (_this *Default__) EqualsGeneric(x interface{}) bool {
	other, ok := x.(*Default__)
	return ok && _this.Equals(other)
}

func (*Default__) String() string {
	return "_module.Default__"
}
func (_this *Default__) ParentTraits_() []*_dafny.TraitID {
	return [](*_dafny.TraitID){}
}

var _ _dafny.TraitOffspring = &Default__{}

func (_static *CompanionStruct_Default___) Spend(s State, amount _dafny.Int) (State, Outcome) {
	var next State = Companion_State_.Default()
	_ = next
	var outcome Outcome = Companion_Outcome_.Default()
	_ = outcome
	if ((amount).Sign() == 1) && ((((s).Dtor_spent()).Plus(amount)).Cmp((s).Dtor_cap()) <= 0) {
		next = Companion_State_.Create_Wallet_((s).Dtor_cap(), ((s).Dtor_spent()).Plus(amount))
		outcome = Companion_Outcome_.Create_Ok_()
	} else {
		next = s
		outcome = Companion_Outcome_.Create_Rejected_()
	}
	return next, outcome
}
func (_static *CompanionStruct_Default___) Valid(s State) bool {
	return (((s).Dtor_spent()).Sign() != -1) && (((s).Dtor_spent()).Cmp((s).Dtor_cap()) <= 0)
}

// End of class Default__

// Definition of datatype Outcome
type Outcome struct {
	Data_Outcome_
}

func (_this Outcome) Get_() Data_Outcome_ {
	return _this.Data_Outcome_
}

type Data_Outcome_ interface {
	isOutcome()
}

type CompanionStruct_Outcome_ struct {
}

var Companion_Outcome_ = CompanionStruct_Outcome_{}

type Outcome_Ok struct {
}

func (Outcome_Ok) isOutcome() {}

func (CompanionStruct_Outcome_) Create_Ok_() Outcome {
	return Outcome{Outcome_Ok{}}
}

func (_this Outcome) Is_Ok() bool {
	_, ok := _this.Get_().(Outcome_Ok)
	return ok
}

type Outcome_Rejected struct {
}

func (Outcome_Rejected) isOutcome() {}

func (CompanionStruct_Outcome_) Create_Rejected_() Outcome {
	return Outcome{Outcome_Rejected{}}
}

func (_this Outcome) Is_Rejected() bool {
	_, ok := _this.Get_().(Outcome_Rejected)
	return ok
}

func (CompanionStruct_Outcome_) Default() Outcome {
	return Companion_Outcome_.Create_Ok_()
}

func (_ CompanionStruct_Outcome_) AllSingletonConstructors() _dafny.Iterator {
	i := -1
	return func() (interface{}, bool) {
		i++
		switch i {
		case 0:
			return Companion_Outcome_.Create_Ok_(), true
		case 1:
			return Companion_Outcome_.Create_Rejected_(), true
		default:
			return Outcome{}, false
		}
	}
}

func (_this Outcome) String() string {
	switch _this.Get_().(type) {
	case nil:
		return "null"
	case Outcome_Ok:
		{
			return "Wallet.Outcome.Ok"
		}
	case Outcome_Rejected:
		{
			return "Wallet.Outcome.Rejected"
		}
	default:
		{
			return "<unexpected>"
		}
	}
}

func (_this Outcome) Equals(other Outcome) bool {
	switch _this.Get_().(type) {
	case Outcome_Ok:
		{
			_, ok := other.Get_().(Outcome_Ok)
			return ok
		}
	case Outcome_Rejected:
		{
			_, ok := other.Get_().(Outcome_Rejected)
			return ok
		}
	default:
		{
			return false // unexpected
		}
	}
}

func (_this Outcome) EqualsGeneric(other interface{}) bool {
	typed, ok := other.(Outcome)
	return ok && _this.Equals(typed)
}

func Type_Outcome_() _dafny.TypeDescriptor {
	return type_Outcome_{}
}

type type_Outcome_ struct {
}

func (_this type_Outcome_) Default() interface{} {
	return Companion_Outcome_.Default()
}

func (_this type_Outcome_) String() string {
	return "Wallet.Outcome"
}
func (_this Outcome) ParentTraits_() []*_dafny.TraitID {
	return [](*_dafny.TraitID){}
}

var _ _dafny.TraitOffspring = Outcome{}

// End of datatype Outcome

// Definition of datatype State
type State struct {
	Data_State_
}

func (_this State) Get_() Data_State_ {
	return _this.Data_State_
}

type Data_State_ interface {
	isState()
}

type CompanionStruct_State_ struct {
}

var Companion_State_ = CompanionStruct_State_{}

type State_Wallet struct {
	Cap   _dafny.Int
	Spent _dafny.Int
}

func (State_Wallet) isState() {}

func (CompanionStruct_State_) Create_Wallet_(Cap _dafny.Int, Spent _dafny.Int) State {
	return State{State_Wallet{Cap, Spent}}
}

func (_this State) Is_Wallet() bool {
	_, ok := _this.Get_().(State_Wallet)
	return ok
}

func (CompanionStruct_State_) Default() State {
	return Companion_State_.Create_Wallet_(_dafny.Zero, _dafny.Zero)
}

func (_this State) Dtor_cap() _dafny.Int {
	return _this.Get_().(State_Wallet).Cap
}

func (_this State) Dtor_spent() _dafny.Int {
	return _this.Get_().(State_Wallet).Spent
}

func (_this State) String() string {
	switch data := _this.Get_().(type) {
	case nil:
		return "null"
	case State_Wallet:
		{
			return "Wallet.State.Wallet" + "(" + _dafny.String(data.Cap) + ", " + _dafny.String(data.Spent) + ")"
		}
	default:
		{
			return "<unexpected>"
		}
	}
}

func (_this State) Equals(other State) bool {
	switch data1 := _this.Get_().(type) {
	case State_Wallet:
		{
			data2, ok := other.Get_().(State_Wallet)
			return ok && data1.Cap.Cmp(data2.Cap) == 0 && data1.Spent.Cmp(data2.Spent) == 0
		}
	default:
		{
			return false // unexpected
		}
	}
}

func (_this State) EqualsGeneric(other interface{}) bool {
	typed, ok := other.(State)
	return ok && _this.Equals(typed)
}

func Type_State_() _dafny.TypeDescriptor {
	return type_State_{}
}

type type_State_ struct {
}

func (_this type_State_) Default() interface{} {
	return Companion_State_.Default()
}

func (_this type_State_) String() string {
	return "Wallet.State"
}
func (_this State) ParentTraits_() []*_dafny.TraitID {
	return [](*_dafny.TraitID){}
}

var _ _dafny.TraitOffspring = State{}

// End of datatype State
