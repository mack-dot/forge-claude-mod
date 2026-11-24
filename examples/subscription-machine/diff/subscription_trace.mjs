// forge diff trace adapter (JavaScript): the shipped TypeScript step function, the planted-bug copies, and the
// proved model compiled to JavaScript, as machines for the trace fuzz (cli/forge_trace.py). Test code, not proved.
import * as app from '../app/subscription.ts'
import { planted } from '../app/subscription_planted.ts'

const { Subscription: S } = globalThis.forge.loadDafny('../verified/subscription-machine/js/subscription_machine.js', ['Subscription'])
const BigNumber = globalThis.forge.require(process.env.NODE_PATH.split(':')[0] + '/bignumber.js')
const big = x => new BigNumber(String(x))
const tag = (v, names) => {
  const n = names.find(k => v['is_' + k])
  if (n === undefined) throw new TypeError('unknown constructor')
  return n
}
const kind = name => {
  if (!['Start', 'Renew', 'Pause', 'Resume', 'Cancel'].includes(name)) throw new TypeError('unknown kind')
  return S.Kind['create_' + name]()
}

const reference = {
  init: () => S.__default.Init(),
  step: (s, e, now) => {
    const r = S.__default.Step(s, S.Event.create_Event(kind(e.kind), big(e.id), big(e.len)), big(now))
    return r.is_Ok ? { ok: r.dtor_next } : { reject: tag(r.dtor_why, ['Duplicate', 'BadInput', 'BadState', 'Expired']) }
  },
  view: s => ({
    status: tag(s.dtor_status, ['New', 'Active', 'Paused', 'Canceled']),
    expiresAt: s.dtor_expiresAt, left: s.dtor_left, lastId: s.dtor_lastId, renewals: s.dtor_renewals,
  }),
}

const view = s => ({ status: s.status, expiresAt: s.expiresAt, left: s.left, lastId: s.lastId, renewals: s.renewals })

export const trace = {
  reference,
  candidates: {
    shipped: { init: app.initial, step: app.step, view, save: app.save, load: app.load },
    ...(process.env.FORGE_TRACE_PLANTED === '0' ? {} : planted),
  },
}
