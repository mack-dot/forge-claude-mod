import sys
from typing import Callable, Any, TypeVar, NamedTuple
from math import floor
from itertools import count

import module_ as module_
import _dafny as _dafny
import System_ as System_

# Module: Wallet

class default__:
    def  __init__(self):
        pass

    @staticmethod
    def Spend(s, amount):
        next_: State = State.default()()
        outcome: Outcome = Outcome.default()()
        if ((0) < (amount)) and ((((s).spent) + (amount)) <= ((s).cap)):
            next_ = State_Wallet((s).cap, ((s).spent) + (amount))
            outcome = Outcome_Ok()
        elif True:
            next_ = s
            outcome = Outcome_Rejected()
        return next_, outcome

    @staticmethod
    def Valid(s):
        return ((0) <= ((s).spent)) and (((s).spent) <= ((s).cap))


class Outcome:
    @_dafny.classproperty
    def AllSingletonConstructors(cls):
        return [Outcome_Ok(), Outcome_Rejected()]
    @classmethod
    def default(cls, ):
        return lambda: Outcome_Ok()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Ok(self) -> bool:
        return isinstance(self, Outcome_Ok)
    @property
    def is_Rejected(self) -> bool:
        return isinstance(self, Outcome_Rejected)

class Outcome_Ok(Outcome, NamedTuple('Ok', [])):
    def __dafnystr__(self) -> str:
        return f'Wallet.Outcome.Ok'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Outcome_Ok)
    def __hash__(self) -> int:
        return super().__hash__()

class Outcome_Rejected(Outcome, NamedTuple('Rejected', [])):
    def __dafnystr__(self) -> str:
        return f'Wallet.Outcome.Rejected'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Outcome_Rejected)
    def __hash__(self) -> int:
        return super().__hash__()


class State:
    @classmethod
    def default(cls, ):
        return lambda: State_Wallet(int(0), int(0))
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Wallet(self) -> bool:
        return isinstance(self, State_Wallet)

class State_Wallet(State, NamedTuple('Wallet', [('cap', Any), ('spent', Any)])):
    def __dafnystr__(self) -> str:
        return f'Wallet.State.Wallet({_dafny.string_of(self.cap)}, {_dafny.string_of(self.spent)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, State_Wallet) and self.cap == __o.cap and self.spent == __o.spent
    def __hash__(self) -> int:
        return super().__hash__()

