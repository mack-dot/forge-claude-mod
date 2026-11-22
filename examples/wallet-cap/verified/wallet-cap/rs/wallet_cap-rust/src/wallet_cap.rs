#![allow(warnings, unconditional_panic)]
#![allow(nonstandard_style)]
#![cfg_attr(any(), rustfmt::skip)]

pub mod _module {
    
}
/// impl/Wallet.dfy(3,1)
pub mod Wallet {
    pub use ::std::rc::Rc;
    pub use ::dafny_runtime::DafnyInt;
    pub use ::dafny_runtime::int;
    pub use ::std::fmt::Debug;
    pub use ::std::fmt::Formatter;
    pub use ::std::fmt::Result;
    pub use ::dafny_runtime::DafnyPrint;
    pub use ::dafny_runtime::SequenceIter;
    pub use ::dafny_runtime::seq;
    pub use ::std::cmp::PartialEq;
    pub use ::std::cmp::Eq;
    pub use ::std::hash::Hash;
    pub use ::std::hash::Hasher;
    pub use ::std::convert::AsRef;

    pub struct _default {}

    impl _default {
        /// impl/Wallet.dfy(4,3)
        pub fn Spend(s: &Rc<State>, amount: &DafnyInt) -> (Rc<State>, Rc<Outcome>) {
            let mut next: Rc<State>;
            let mut outcome: Rc<Outcome>;
            if int!(0) < amount.clone() && s.spent().clone() + amount.clone() <= s.cap().clone() {
                next = Rc::new(State::Wallet {
                            cap: s.cap().clone(),
                            spent: s.spent().clone() + amount.clone()
                        });
                outcome = Rc::new(Outcome::Ok {});
            } else {
                next = s.clone();
                outcome = Rc::new(Outcome::Rejected {});
            };
            return (
                    next.clone(),
                    outcome.clone()
                );
        }
        /// spec/WalletSpec.dfy(6,3)
        pub fn Valid(s: &Rc<State>) -> bool {
            int!(0) <= s.spent().clone() && s.spent().clone() <= s.cap().clone()
        }
    }

    /// spec/WalletSpec.dfy(3,3)
    #[derive(Clone)]
    pub enum Outcome {
        Ok {},
        Rejected {}
    }

    impl Outcome {}

    impl Debug
        for Outcome {
        fn fmt(&self, f: &mut Formatter) -> Result {
            DafnyPrint::fmt_print(self, f, true)
        }
    }

    impl DafnyPrint
        for Outcome {
        fn fmt_print(&self, _formatter: &mut Formatter, _in_seq: bool) -> std::fmt::Result {
            match self {
                Outcome::Ok{} => {
                    write!(_formatter, "Wallet.Outcome.Ok")?;
                    Ok(())
                },
                Outcome::Rejected{} => {
                    write!(_formatter, "Wallet.Outcome.Rejected")?;
                    Ok(())
                },
            }
        }
    }

    impl Outcome {
        /// Enumerates all possible values of Outcome
        pub fn _AllSingletonConstructors() -> SequenceIter<Rc<Outcome>> {
            seq![Rc::new(Outcome::Ok {}), Rc::new(Outcome::Rejected {})].iter()
        }
    }

    impl PartialEq
        for Outcome {
        fn eq(&self, other: &Self) -> bool {
            match (
                    self,
                    other
                ) {
                (Outcome::Ok{}, Outcome::Ok{}) => {
                    true
                },
                (Outcome::Rejected{}, Outcome::Rejected{}) => {
                    true
                },
                _ => {
                    false
                },
            }
        }
    }

    impl Eq
        for Outcome {}

    impl Hash
        for Outcome {
        fn hash<_H: Hasher>(&self, _state: &mut _H) {
            match self {
                Outcome::Ok{} => {
                    
                },
                Outcome::Rejected{} => {
                    
                },
            }
        }
    }

    impl AsRef<Outcome>
        for Outcome {
        fn as_ref(&self) -> &Self {
            self
        }
    }

    /// spec/WalletSpec.dfy(4,3)
    #[derive(Clone)]
    pub enum State {
        Wallet {
            cap: DafnyInt,
            spent: DafnyInt
        }
    }

    impl State {
        /// Returns a borrow of the field cap
        pub fn cap(&self) -> &DafnyInt {
            match self {
                State::Wallet{cap, spent, } => cap,
            }
        }
        /// Returns a borrow of the field spent
        pub fn spent(&self) -> &DafnyInt {
            match self {
                State::Wallet{cap, spent, } => spent,
            }
        }
    }

    impl Debug
        for State {
        fn fmt(&self, f: &mut Formatter) -> Result {
            DafnyPrint::fmt_print(self, f, true)
        }
    }

    impl DafnyPrint
        for State {
        fn fmt_print(&self, _formatter: &mut Formatter, _in_seq: bool) -> std::fmt::Result {
            match self {
                State::Wallet{cap, spent, } => {
                    write!(_formatter, "Wallet.State.Wallet(")?;
                    DafnyPrint::fmt_print(cap, _formatter, false)?;
                    write!(_formatter, ", ")?;
                    DafnyPrint::fmt_print(spent, _formatter, false)?;
                    write!(_formatter, ")")?;
                    Ok(())
                },
            }
        }
    }

    impl PartialEq
        for State {
        fn eq(&self, other: &Self) -> bool {
            match (
                    self,
                    other
                ) {
                (State::Wallet{cap, spent, }, State::Wallet{cap: _2_cap, spent: _2_spent, }) => {
                    cap == _2_cap && spent == _2_spent
                },
                _ => {
                    false
                },
            }
        }
    }

    impl Eq
        for State {}

    impl Hash
        for State {
        fn hash<_H: Hasher>(&self, _state: &mut _H) {
            match self {
                State::Wallet{cap, spent, } => {
                    Hash::hash(cap, _state);
                    Hash::hash(spent, _state)
                },
            }
        }
    }

    impl AsRef<State>
        for State {
        fn as_ref(&self) -> &Self {
            self
        }
    }
}