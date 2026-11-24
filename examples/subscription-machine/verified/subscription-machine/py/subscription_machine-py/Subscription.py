import sys
from typing import Callable, Any, TypeVar, NamedTuple
from math import floor
from itertools import count

import module_ as module_
import _dafny as _dafny
import System_ as System_

# Module: Subscription

class default__:
    def  __init__(self):
        pass

    @staticmethod
    def Step(s, e, now):
        pat_let_tv0_ = s
        pat_let_tv1_ = e
        pat_let_tv2_ = s
        pat_let_tv3_ = e
        pat_let_tv4_ = e
        pat_let_tv5_ = s
        pat_let_tv6_ = now
        pat_let_tv7_ = e
        pat_let_tv8_ = now
        pat_let_tv9_ = s
        pat_let_tv10_ = e
        if ((e).id_) < (1):
            return Result_Err(Reject_BadInput())
        elif ((e).id_) <= ((s).lastId):
            return Result_Err(Reject_Duplicate())
        elif ((((e).kind) == (Kind_Start())) or (((e).kind) == (Kind_Renew()))) and (((e).len_) < (1)):
            return Result_Err(Reject_BadInput())
        elif True:
            source0_ = (e).kind
            if True:
                if source0_.is_Start:
                    if ((s).status) == (Status_New()):
                        return Result_Ok(State_State(Status_Active(), (now) + ((e).len_), 0, (e).id_, 0))
                    elif True:
                        return Result_Err(Reject_BadState())
            if True:
                if source0_.is_Renew:
                    if ((s).status) != (Status_Active()):
                        return Result_Err(Reject_BadState())
                    elif (now) >= ((s).expiresAt):
                        return Result_Err(Reject_Expired())
                    elif True:
                        def iife0_(_pat_let0_0):
                            def iife1_(d_0_dt__update__tmp_h1_):
                                def iife2_(_pat_let1_0):
                                    def iife3_(d_1_dt__update_hrenewals_h1_):
                                        def iife4_(_pat_let2_0):
                                            def iife5_(d_2_dt__update_hlastId_h1_):
                                                def iife6_(_pat_let3_0):
                                                    def iife7_(d_3_dt__update_hexpiresAt_h1_):
                                                        return State_State((d_0_dt__update__tmp_h1_).status, d_3_dt__update_hexpiresAt_h1_, (d_0_dt__update__tmp_h1_).left, d_2_dt__update_hlastId_h1_, d_1_dt__update_hrenewals_h1_)
                                                    return iife7_(_pat_let3_0)
                                                return iife6_(((pat_let_tv2_).expiresAt) + ((pat_let_tv3_).len_))
                                            return iife5_(_pat_let2_0)
                                        return iife4_((pat_let_tv1_).id_)
                                    return iife3_(_pat_let1_0)
                                return iife2_(((pat_let_tv0_).renewals) + (1))
                            return iife1_(_pat_let0_0)
                        return Result_Ok(iife0_(s))
            if True:
                if source0_.is_Pause:
                    if ((s).status) != (Status_Active()):
                        return Result_Err(Reject_BadState())
                    elif (now) >= ((s).expiresAt):
                        return Result_Err(Reject_Expired())
                    elif True:
                        def iife8_(_pat_let4_0):
                            def iife9_(d_4_dt__update__tmp_h2_):
                                def iife10_(_pat_let5_0):
                                    def iife11_(d_5_dt__update_hlastId_h2_):
                                        def iife12_(_pat_let6_0):
                                            def iife13_(d_6_dt__update_hleft_h0_):
                                                def iife14_(_pat_let7_0):
                                                    def iife15_(d_7_dt__update_hstatus_h0_):
                                                        return State_State(d_7_dt__update_hstatus_h0_, (d_4_dt__update__tmp_h2_).expiresAt, d_6_dt__update_hleft_h0_, d_5_dt__update_hlastId_h2_, (d_4_dt__update__tmp_h2_).renewals)
                                                    return iife15_(_pat_let7_0)
                                                return iife14_(Status_Paused())
                                            return iife13_(_pat_let6_0)
                                        return iife12_(((pat_let_tv5_).expiresAt) - (pat_let_tv6_))
                                    return iife11_(_pat_let5_0)
                                return iife10_((pat_let_tv4_).id_)
                            return iife9_(_pat_let4_0)
                        return Result_Ok(iife8_(s))
            if True:
                if source0_.is_Resume:
                    if ((s).status) != (Status_Paused()):
                        return Result_Err(Reject_BadState())
                    elif True:
                        def iife16_(_pat_let8_0):
                            def iife17_(d_8_dt__update__tmp_h3_):
                                def iife18_(_pat_let9_0):
                                    def iife19_(d_9_dt__update_hlastId_h3_):
                                        def iife20_(_pat_let10_0):
                                            def iife21_(d_10_dt__update_hleft_h1_):
                                                def iife22_(_pat_let11_0):
                                                    def iife23_(d_11_dt__update_hexpiresAt_h2_):
                                                        def iife24_(_pat_let12_0):
                                                            def iife25_(d_12_dt__update_hstatus_h1_):
                                                                return State_State(d_12_dt__update_hstatus_h1_, d_11_dt__update_hexpiresAt_h2_, d_10_dt__update_hleft_h1_, d_9_dt__update_hlastId_h3_, (d_8_dt__update__tmp_h3_).renewals)
                                                            return iife25_(_pat_let12_0)
                                                        return iife24_(Status_Active())
                                                    return iife23_(_pat_let11_0)
                                                return iife22_((pat_let_tv8_) + ((pat_let_tv9_).left))
                                            return iife21_(_pat_let10_0)
                                        return iife20_(0)
                                    return iife19_(_pat_let9_0)
                                return iife18_((pat_let_tv7_).id_)
                            return iife17_(_pat_let8_0)
                        return Result_Ok(iife16_(s))
            if True:
                if (((s).status) == (Status_Active())) or (((s).status) == (Status_Paused())):
                    def iife26_(_pat_let13_0):
                        def iife27_(d_13_dt__update__tmp_h4_):
                            def iife28_(_pat_let14_0):
                                def iife29_(d_14_dt__update_hlastId_h4_):
                                    def iife30_(_pat_let15_0):
                                        def iife31_(d_15_dt__update_hleft_h2_):
                                            def iife32_(_pat_let16_0):
                                                def iife33_(d_16_dt__update_hstatus_h2_):
                                                    return State_State(d_16_dt__update_hstatus_h2_, (d_13_dt__update__tmp_h4_).expiresAt, d_15_dt__update_hleft_h2_, d_14_dt__update_hlastId_h4_, (d_13_dt__update__tmp_h4_).renewals)
                                                return iife33_(_pat_let16_0)
                                            return iife32_(Status_Canceled())
                                        return iife31_(_pat_let15_0)
                                    return iife30_(0)
                                return iife29_(_pat_let14_0)
                            return iife28_((pat_let_tv10_).id_)
                        return iife27_(_pat_let13_0)
                    return Result_Ok(iife26_(s))
                elif True:
                    return Result_Err(Reject_BadState())

    @staticmethod
    def Init():
        return State_State(Status_New(), 0, 0, 0, 0)

    @staticmethod
    def Valid(s):
        return (((((s).lastId) >= (0)) and (((s).renewals) >= (0))) and (((s).left) >= (0))) and ((((s).status) == (Status_Paused())) == (((s).left) > (0)))


class Status:
    @_dafny.classproperty
    def AllSingletonConstructors(cls):
        return [Status_New(), Status_Active(), Status_Paused(), Status_Canceled()]
    @classmethod
    def default(cls, ):
        return lambda: Status_New()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_New(self) -> bool:
        return isinstance(self, Status_New)
    @property
    def is_Active(self) -> bool:
        return isinstance(self, Status_Active)
    @property
    def is_Paused(self) -> bool:
        return isinstance(self, Status_Paused)
    @property
    def is_Canceled(self) -> bool:
        return isinstance(self, Status_Canceled)

class Status_New(Status, NamedTuple('New', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Status.New'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Status_New)
    def __hash__(self) -> int:
        return super().__hash__()

class Status_Active(Status, NamedTuple('Active', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Status.Active'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Status_Active)
    def __hash__(self) -> int:
        return super().__hash__()

class Status_Paused(Status, NamedTuple('Paused', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Status.Paused'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Status_Paused)
    def __hash__(self) -> int:
        return super().__hash__()

class Status_Canceled(Status, NamedTuple('Canceled', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Status.Canceled'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Status_Canceled)
    def __hash__(self) -> int:
        return super().__hash__()


class State:
    @classmethod
    def default(cls, ):
        return lambda: State_State(Status.default()(), int(0), int(0), int(0), int(0))
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_State(self) -> bool:
        return isinstance(self, State_State)

class State_State(State, NamedTuple('State', [('status', Any), ('expiresAt', Any), ('left', Any), ('lastId', Any), ('renewals', Any)])):
    def __dafnystr__(self) -> str:
        return f'Subscription.State.State({_dafny.string_of(self.status)}, {_dafny.string_of(self.expiresAt)}, {_dafny.string_of(self.left)}, {_dafny.string_of(self.lastId)}, {_dafny.string_of(self.renewals)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, State_State) and self.status == __o.status and self.expiresAt == __o.expiresAt and self.left == __o.left and self.lastId == __o.lastId and self.renewals == __o.renewals
    def __hash__(self) -> int:
        return super().__hash__()


class Kind:
    @_dafny.classproperty
    def AllSingletonConstructors(cls):
        return [Kind_Start(), Kind_Renew(), Kind_Pause(), Kind_Resume(), Kind_Cancel()]
    @classmethod
    def default(cls, ):
        return lambda: Kind_Start()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Start(self) -> bool:
        return isinstance(self, Kind_Start)
    @property
    def is_Renew(self) -> bool:
        return isinstance(self, Kind_Renew)
    @property
    def is_Pause(self) -> bool:
        return isinstance(self, Kind_Pause)
    @property
    def is_Resume(self) -> bool:
        return isinstance(self, Kind_Resume)
    @property
    def is_Cancel(self) -> bool:
        return isinstance(self, Kind_Cancel)

class Kind_Start(Kind, NamedTuple('Start', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Kind.Start'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Start)
    def __hash__(self) -> int:
        return super().__hash__()

class Kind_Renew(Kind, NamedTuple('Renew', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Kind.Renew'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Renew)
    def __hash__(self) -> int:
        return super().__hash__()

class Kind_Pause(Kind, NamedTuple('Pause', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Kind.Pause'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Pause)
    def __hash__(self) -> int:
        return super().__hash__()

class Kind_Resume(Kind, NamedTuple('Resume', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Kind.Resume'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Resume)
    def __hash__(self) -> int:
        return super().__hash__()

class Kind_Cancel(Kind, NamedTuple('Cancel', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Kind.Cancel'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Cancel)
    def __hash__(self) -> int:
        return super().__hash__()


class Event:
    @classmethod
    def default(cls, ):
        return lambda: Event_Event(Kind.default()(), int(0), int(0))
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Event(self) -> bool:
        return isinstance(self, Event_Event)

class Event_Event(Event, NamedTuple('Event', [('kind', Any), ('id_', Any), ('len_', Any)])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Event.Event({_dafny.string_of(self.kind)}, {_dafny.string_of(self.id_)}, {_dafny.string_of(self.len_)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Event_Event) and self.kind == __o.kind and self.id_ == __o.id_ and self.len_ == __o.len_
    def __hash__(self) -> int:
        return super().__hash__()


class Reject:
    @_dafny.classproperty
    def AllSingletonConstructors(cls):
        return [Reject_Duplicate(), Reject_BadInput(), Reject_BadState(), Reject_Expired()]
    @classmethod
    def default(cls, ):
        return lambda: Reject_Duplicate()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Duplicate(self) -> bool:
        return isinstance(self, Reject_Duplicate)
    @property
    def is_BadInput(self) -> bool:
        return isinstance(self, Reject_BadInput)
    @property
    def is_BadState(self) -> bool:
        return isinstance(self, Reject_BadState)
    @property
    def is_Expired(self) -> bool:
        return isinstance(self, Reject_Expired)

class Reject_Duplicate(Reject, NamedTuple('Duplicate', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Reject.Duplicate'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Reject_Duplicate)
    def __hash__(self) -> int:
        return super().__hash__()

class Reject_BadInput(Reject, NamedTuple('BadInput', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Reject.BadInput'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Reject_BadInput)
    def __hash__(self) -> int:
        return super().__hash__()

class Reject_BadState(Reject, NamedTuple('BadState', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Reject.BadState'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Reject_BadState)
    def __hash__(self) -> int:
        return super().__hash__()

class Reject_Expired(Reject, NamedTuple('Expired', [])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Reject.Expired'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Reject_Expired)
    def __hash__(self) -> int:
        return super().__hash__()


class Result:
    @classmethod
    def default(cls, ):
        return lambda: Result_Ok(State.default()())
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Ok(self) -> bool:
        return isinstance(self, Result_Ok)
    @property
    def is_Err(self) -> bool:
        return isinstance(self, Result_Err)

class Result_Ok(Result, NamedTuple('Ok', [('next_', Any)])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Result.Ok({_dafny.string_of(self.next_)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Result_Ok) and self.next_ == __o.next_
    def __hash__(self) -> int:
        return super().__hash__()

class Result_Err(Result, NamedTuple('Err', [('why', Any)])):
    def __dafnystr__(self) -> str:
        return f'Subscription.Result.Err({_dafny.string_of(self.why)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Result_Err) and self.why == __o.why
    def __hash__(self) -> int:
        return super().__hash__()

