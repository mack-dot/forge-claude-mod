"""The app's wrapper around the built, proved thread rule (verified/ is written by `forge_check.py build`; never edit it).
NOT PROVED: it converts plain values to the generated types and back, and it never raises on them. A viewer or an item
it cannot read gives the fail-closed answer: nothing visible."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verified', 'thread-visibility', 'py', 'thread_visibility-py'))
import Thread  # noqa: E402  the emitted, proved-in-the-model module
import ThreadRules  # noqa: E402
import _dafny  # noqa: E402

FIELDS = ('id', 'channel', 'author', 'parent')


def readable(viewer, items):
    return (isinstance(viewer, dict) and isinstance(viewer.get('channel'), str) and isinstance(viewer.get('user'), str)
            and isinstance(items, list) and all(
                isinstance(it, dict) and all(isinstance(it.get(f), str) for f in FIELDS)
                and it.get('kind') in ('post', 'takedown') and type(it.get('flagged')) is bool for it in items))


def to_item(it):
    s = _dafny.Seq
    return ThreadRules.Item_Item(s(it['id']), s(it['channel']), s(it['author']),
                                 ThreadRules.Kind_Takedown() if it['kind'] == 'takedown' else ThreadRules.Kind_Post(),
                                 s(it['parent']), it['flagged'])


def visible_ids(viewer, items):
    if not readable(viewer, items):
        return []
    try:
        out = Thread.default__.ShownTo(ThreadRules.Viewer_Viewer(_dafny.Seq(viewer['user']), _dafny.Seq(viewer['channel'])),
                                         _dafny.Seq([to_item(it) for it in items]))
        return [it['id'] for it, ok in zip(items, out) if ok]
    except Exception:  # noqa: BLE001  last line of defence: a crash becomes "nothing visible"
        return []
