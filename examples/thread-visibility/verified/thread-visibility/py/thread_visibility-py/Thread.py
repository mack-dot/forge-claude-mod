import sys
from typing import Callable, Any, TypeVar, NamedTuple
from math import floor
from itertools import count

import module_ as module_
import _dafny as _dafny
import System_ as System_
import ThreadRules as ThreadRules

# Module: Thread

class default__:
    def  __init__(self):
        pass

    @staticmethod
    def ById(items):
        m: _dafny.Map = _dafny.Map({})
        m = _dafny.Map({})
        d_0_i_: int
        d_0_i_ = 0
        while (d_0_i_) < (len(items)):
            m = (m).set(((items)[d_0_i_]).id_, (items)[d_0_i_])
            d_0_i_ = (d_0_i_) + (1)
        return m

    @staticmethod
    def Walk(m, items, it):
        up: _dafny.Seq = _dafny.Seq({})
        d_0_seen_: _dafny.Set
        d_0_seen_ = _dafny.Set({(it).id_})
        d_1_ref_: _dafny.Seq
        d_1_ref_ = (it).parent
        up = _dafny.SeqWithoutIsStrInference([])
        while (((d_1_ref_) != (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")))) and ((d_1_ref_) not in (d_0_seen_))) and ((d_1_ref_) in (m)):
            d_2_p_: ThreadRules.Item
            d_2_p_ = (m)[d_1_ref_]
            up = (up) + (_dafny.SeqWithoutIsStrInference([d_2_p_]))
            d_0_seen_ = (d_0_seen_) | (_dafny.Set({d_1_ref_}))
            d_1_ref_ = (d_2_p_).parent
        return up

    @staticmethod
    def Taken(m, items):
        taken: _dafny.Set = _dafny.Set({})
        taken = _dafny.Set({})
        d_0_i_: int
        d_0_i_ = 0
        while (d_0_i_) < (len(items)):
            d_1_t_: ThreadRules.Item
            d_1_t_ = (items)[d_0_i_]
            if (((((d_1_t_).kind) == (ThreadRules.Kind_Takedown())) and (((d_1_t_).parent) != (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, ""))))) and (((d_1_t_).parent) in (m))) and (ThreadRules.default__.MayTakeDown((d_1_t_).author, (m)[(d_1_t_).parent])):
                taken = (taken) | (_dafny.Set({(d_1_t_).parent}))
            d_0_i_ = (d_0_i_) + (1)
        return taken

    @staticmethod
    def ShownTo(v, items):
        out: _dafny.Seq = _dafny.Seq({})
        d_0_m_: _dafny.Map
        out0_: _dafny.Map
        out0_ = default__.ById(items)
        d_0_m_ = out0_
        d_1_taken_: _dafny.Set
        out1_: _dafny.Set
        out1_ = default__.Taken(d_0_m_, items)
        d_1_taken_ = out1_
        out = _dafny.SeqWithoutIsStrInference([])
        d_2_i_: int
        d_2_i_ = 0
        while (d_2_i_) < (len(items)):
            d_3_it_: ThreadRules.Item
            d_3_it_ = (items)[d_2_i_]
            d_4_ok_: bool
            d_4_ok_ = False
            if (((((d_3_it_).kind) == (ThreadRules.Kind_Post())) and (((d_3_it_).id_) not in (d_1_taken_))) and (not((d_3_it_).flagged))) and (ThreadRules.default__.Member(v, d_3_it_)):
                d_5_up_: _dafny.Seq
                out2_: _dafny.Seq
                out2_ = default__.Walk(d_0_m_, items, d_3_it_)
                d_5_up_ = out2_
                def lambda0_(forall_var_0_):
                    d_6_a_: ThreadRules.Item = forall_var_0_
                    return not ((d_6_a_) in (d_5_up_)) or (((((d_6_a_).id_) not in (d_1_taken_)) and (not((d_6_a_).flagged))) and (ThreadRules.default__.Member(v, d_6_a_)))

                d_4_ok_ = _dafny.quantifier((d_5_up_).UniqueElements, True, lambda0_)
            out = (out) + (_dafny.SeqWithoutIsStrInference([d_4_ok_]))
            d_2_i_ = (d_2_i_) + (1)
        return out

    @staticmethod
    def Ids(items):
        def iife0_():
            coll0_ = _dafny.Set()
            compr_0_: int
            for compr_0_ in _dafny.IntegerRange(0, len(items)):
                d_0_i_: int = compr_0_
                if ((0) <= (d_0_i_)) and ((d_0_i_) < (len(items))):
                    coll0_ = coll0_.union(_dafny.Set([((items)[d_0_i_]).id_]))
            return _dafny.Set(coll0_)
        return iife0_()
        

    @staticmethod
    def Find(items, id_):
        if (len(items)) == (0):
            return Opt_None()
        elif (((items)[(len(items)) - (1)]).id_) == (id_):
            return Opt_Some((items)[(len(items)) - (1)])
        elif True:
            d_0_r_ = default__.Find(_dafny.SeqWithoutIsStrInference((items)[:(len(items)) - (1):]), id_)
            return d_0_r_

    @staticmethod
    def Chain(items, ref, seen):
        d_0___accumulator_ = _dafny.SeqWithoutIsStrInference([])
        while True:
            with _dafny.label():
                if ((ref) == (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")))) or ((ref) in (seen)):
                    return (d_0___accumulator_) + (_dafny.SeqWithoutIsStrInference([]))
                elif True:
                    source0_ = default__.Find(items, ref)
                    if True:
                        if source0_.is_None:
                            return _dafny.SeqWithoutIsStrInference([])
                    if True:
                        d_1_p_ = source0_.v
                        return (_dafny.SeqWithoutIsStrInference([d_1_p_])) + (default__.Chain(items, (d_1_p_).parent, (seen) | (_dafny.Set({ref}))))
                break

    @staticmethod
    def Ancestors(items, it):
        return default__.Chain(items, (it).parent, _dafny.Set({(it).id_}))

    @staticmethod
    def Takes(items, t):
        return (((((t).kind) == (ThreadRules.Kind_Takedown())) and (((t).parent) != (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, ""))))) and ((default__.Find(items, (t).parent)).is_Some)) and (ThreadRules.default__.MayTakeDown((t).author, (default__.Find(items, (t).parent)).v))

    @staticmethod
    def TakenDown(items):
        def iife0_():
            coll0_ = _dafny.Set()
            compr_0_: ThreadRules.Item
            for compr_0_ in (items).Elements:
                d_0_t_: ThreadRules.Item = compr_0_
                if ((d_0_t_) in (items)) and (default__.Takes(items, d_0_t_)):
                    coll0_ = coll0_.union(_dafny.Set([(d_0_t_).parent]))
            return _dafny.Set(coll0_)
        return iife0_()
        

    @staticmethod
    def Fine(items, v, it):
        return ((((it).id_) not in (default__.TakenDown(items))) and (not((it).flagged))) and (ThreadRules.default__.Member(v, it))

    @staticmethod
    def Visible(v, items, it):
        def lambda0_(forall_var_0_):
            d_0_a_: ThreadRules.Item = forall_var_0_
            return not ((d_0_a_) in (default__.Ancestors(items, it))) or (default__.Fine(items, v, d_0_a_))

        return ((((it).kind) == (ThreadRules.Kind_Post())) and (default__.Fine(items, v, it))) and (_dafny.quantifier((default__.Ancestors(items, it)).UniqueElements, True, lambda0_))


class Opt:
    @classmethod
    def default(cls, ):
        return lambda: Opt_None()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_None(self) -> bool:
        return isinstance(self, Opt_None)
    @property
    def is_Some(self) -> bool:
        return isinstance(self, Opt_Some)

class Opt_None(Opt, NamedTuple('None_', [])):
    def __dafnystr__(self) -> str:
        return f'Thread.Opt.None'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Opt_None)
    def __hash__(self) -> int:
        return super().__hash__()

class Opt_Some(Opt, NamedTuple('Some', [('v', Any)])):
    def __dafnystr__(self) -> str:
        return f'Thread.Opt.Some({_dafny.string_of(self.v)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Opt_Some) and self.v == __o.v
    def __hash__(self) -> int:
        return super().__hash__()

