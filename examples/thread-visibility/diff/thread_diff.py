"""forge diff evaluator adapter (Python): the shipped thread code and the app's wrapper against the proved rule.
Inputs come from thread_inputs.py. Test code, not proved."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'verified', 'thread-visibility', 'py', 'thread_visibility-py'))
import Thread  # noqa: E402  the compiled proved rule
import ThreadRules  # noqa: E402
import _dafny  # noqa: E402
import thread_app  # noqa: E402  the code shipped before Forge
import thread_wrapper  # noqa: E402  the unproved wrapper


def reference(i):
    s = _dafny.Seq
    items = [ThreadRules.Item_Item(s(it['id']), s(it['channel']), s(it['author']),
                                   ThreadRules.Kind_Takedown() if it['kind'] == 'takedown' else ThreadRules.Kind_Post(),
                                   s(it['parent']), bool(it['flagged'])) for it in i['items']]
    out = Thread.default__.ShownTo(ThreadRules.Viewer_Viewer(s(i['viewer']['user']), s(i['viewer']['channel'])), s(items))
    return [it['id'] for it, ok in zip(i['items'], out) if ok]


def fail_closed(i):
    """Nothing visible when the rule cannot take the input; None when it can."""
    return None if thread_wrapper.readable((i or {}).get('viewer'), (i or {}).get('items')) else []


candidates = {'shipped': lambda i: thread_app.visible_ids(i['viewer'], i['items'])}
wrappers = {'wrapper': lambda i: thread_wrapper.visible_ids(i.get('viewer'), i.get('items')) if isinstance(i, dict) else thread_wrapper.visible_ids(None, None)}
