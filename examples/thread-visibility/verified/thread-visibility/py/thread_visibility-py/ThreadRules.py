import sys
from typing import Callable, Any, TypeVar, NamedTuple
from math import floor
from itertools import count

import module_ as module_
import _dafny as _dafny
import System_ as System_

# Module: ThreadRules

class default__:
    def  __init__(self):
        pass

    @staticmethod
    def Member(v, it):
        return (((v).channel) != (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")))) and (((v).channel) == ((it).channel))

    @staticmethod
    def MayTakeDown(actor, target):
        return ((actor) != (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")))) and (((actor) == ((target).author)) or ((actor) == (_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "mod")))))


class Kind:
    @_dafny.classproperty
    def AllSingletonConstructors(cls):
        return [Kind_Post(), Kind_Takedown()]
    @classmethod
    def default(cls, ):
        return lambda: Kind_Post()
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Post(self) -> bool:
        return isinstance(self, Kind_Post)
    @property
    def is_Takedown(self) -> bool:
        return isinstance(self, Kind_Takedown)

class Kind_Post(Kind, NamedTuple('Post', [])):
    def __dafnystr__(self) -> str:
        return f'ThreadRules.Kind.Post'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Post)
    def __hash__(self) -> int:
        return super().__hash__()

class Kind_Takedown(Kind, NamedTuple('Takedown', [])):
    def __dafnystr__(self) -> str:
        return f'ThreadRules.Kind.Takedown'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Kind_Takedown)
    def __hash__(self) -> int:
        return super().__hash__()


class Item:
    @classmethod
    def default(cls, ):
        return lambda: Item_Item(_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")), _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")), _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")), Kind.default()(), _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")), False)
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Item(self) -> bool:
        return isinstance(self, Item_Item)

class Item_Item(Item, NamedTuple('Item', [('id_', Any), ('channel', Any), ('author', Any), ('kind', Any), ('parent', Any), ('flagged', Any)])):
    def __dafnystr__(self) -> str:
        return f'ThreadRules.Item.Item({self.id_.VerbatimString(True)}, {self.channel.VerbatimString(True)}, {self.author.VerbatimString(True)}, {_dafny.string_of(self.kind)}, {self.parent.VerbatimString(True)}, {_dafny.string_of(self.flagged)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Item_Item) and self.id_ == __o.id_ and self.channel == __o.channel and self.author == __o.author and self.kind == __o.kind and self.parent == __o.parent and self.flagged == __o.flagged
    def __hash__(self) -> int:
        return super().__hash__()


class Viewer:
    @classmethod
    def default(cls, ):
        return lambda: Viewer_Viewer(_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")), _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "")))
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Viewer(self) -> bool:
        return isinstance(self, Viewer_Viewer)

class Viewer_Viewer(Viewer, NamedTuple('Viewer', [('user', Any), ('channel', Any)])):
    def __dafnystr__(self) -> str:
        return f'ThreadRules.Viewer.Viewer({self.user.VerbatimString(True)}, {self.channel.VerbatimString(True)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Viewer_Viewer) and self.user == __o.user and self.channel == __o.channel
    def __hash__(self) -> int:
        return super().__hash__()

