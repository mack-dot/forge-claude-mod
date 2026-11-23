"""forge diff trace adapter (Python): the shipped step function, the planted-bug copies, and the proved model
compiled to Python, as machines for the trace fuzz (cli/forge_trace.py). Test code, not proved."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'verified', 'subscription-machine', 'py', 'subscription_machine-py'))
import Subscription as S  # noqa: E402  the compiled proved model
import subscription as app  # noqa: E402  the shipped code
import subscription_planted  # noqa: E402  planted bugs (test material)

KINDS = {'Start': S.Kind_Start, 'Renew': S.Kind_Renew, 'Pause': S.Kind_Pause, 'Resume': S.Kind_Resume,
         'Cancel': S.Kind_Cancel}


def ref_step(s, e, now):
    r = S.default__.Step(s, S.Event_Event(KINDS[e['kind']](), e['id'], e['len']), now)
    return {'ok': r.next_} if r.is_Ok else {'reject': type(r.why).__name__.split('_', 1)[1]}


def ref_view(s):
    return {'status': type(s.status).__name__.split('_', 1)[1], 'expiresAt': int(s.expiresAt), 'left': int(s.left),
            'lastId': int(s.lastId), 'renewals': int(s.renewals)}


candidates = {'shipped': {'init': app.initial, 'step': app.step, 'view': subscription_planted.view,
                          'save': app.save, 'load': app.load}}
if os.environ.get('FORGE_TRACE_PLANTED') != '0':
    candidates.update(subscription_planted.planted)

trace = {'reference': {'init': S.default__.Init, 'step': ref_step, 'view': ref_view}, 'candidates': candidates}
