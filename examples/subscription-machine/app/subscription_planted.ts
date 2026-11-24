// PLANTED BUGS (test material, never shipped): four copies of app/subscription.ts, each with one bug that shows
// only after two or more events. The trace fuzz must find each one and shrink it to its minimal trace.
//   replay-off-by-one   the duplicate check uses < instead of <=, so an immediate retry is applied again
//   pause-expiry        after a Resume the expiry check is one tick late (now > expiresAt instead of >=)
//   restart-drops-id    save() leaves out lastId, so after a restart old events apply again
//   reject-leaks        Renew changes the state object before it checks expiry, and a rejected Renew keeps it
import { initial, load as goodLoad, save as goodSave, step as goodStep } from './subscription.ts'
import type { Sub, SubEvent, StepResult } from './subscription.ts'

type Internal = Sub & { resumed?: boolean }

function replayOffByOne(s: Sub, e: SubEvent, now: number): StepResult {
  if (e.id < 1) return { reject: 'BadInput' }
  if (e.id < s.lastId) return { reject: 'Duplicate' } // BUG: an equal id is a retry, too
  return goodStep({ ...s, lastId: Math.min(s.lastId, e.id - 1) }, e, now)
}

function pauseExpiry(s: Internal, e: SubEvent, now: number): StepResult {
  // BUG: after a Resume this code gives one grace tick: the expiry check becomes now > expiresAt
  const expired = s.resumed ? now > s.expiresAt : now >= s.expiresAt
  if (e.id < 1) return { reject: 'BadInput' }
  if (e.id <= s.lastId) return { reject: 'Duplicate' }
  if ((e.kind === 'Start' || e.kind === 'Renew') && e.len < 1) return { reject: 'BadInput' }
  switch (e.kind) {
    case 'Start':
      return s.status === 'New' ? { ok: { status: 'Active', expiresAt: now + e.len, left: 0, lastId: e.id, renewals: 0 } } : { reject: 'BadState' }
    case 'Renew':
      if (s.status !== 'Active') return { reject: 'BadState' }
      if (expired) return { reject: 'Expired' }
      return { ok: { ...s, expiresAt: s.expiresAt + e.len, lastId: e.id, renewals: s.renewals + 1 } }
    case 'Pause':
      if (s.status !== 'Active') return { reject: 'BadState' }
      if (expired) return { reject: 'Expired' }
      return { ok: { ...s, status: 'Paused', left: s.expiresAt - now, lastId: e.id } }
    case 'Resume':
      if (s.status !== 'Paused') return { reject: 'BadState' }
      return { ok: { ...s, status: 'Active', expiresAt: now + s.left, left: 0, lastId: e.id, resumed: true } as Internal }
    case 'Cancel':
      if (s.status !== 'Active' && s.status !== 'Paused') return { reject: 'BadState' }
      return { ok: { ...s, status: 'Canceled', left: 0, lastId: e.id } }
  }
  throw new TypeError('unknown event kind')
}

function rejectLeaks(s: Sub, e: SubEvent, now: number): StepResult {
  if (e.kind === 'Renew' && e.id > s.lastId && e.len >= 1 && s.status === 'Active') {
    s.renewals += 1 // BUG: changes the caller's state before the expiry check
    if (now >= s.expiresAt) return { reject: 'Expired' }
    return { ok: { ...s, expiresAt: s.expiresAt + e.len, lastId: e.id } }
  }
  return goodStep(s, e, now)
}

const view = (s: Sub) => ({ status: s.status, expiresAt: s.expiresAt, left: s.left, lastId: s.lastId, renewals: s.renewals })

export const planted = {
  'replay-off-by-one': { init: initial, step: replayOffByOne, view, save: goodSave, load: goodLoad },
  'pause-expiry': { init: initial, step: pauseExpiry, view, save: (s: Internal) => JSON.stringify(s), load: (t: string) => JSON.parse(t) },
  'restart-drops-id': {
    init: initial,
    step: goodStep,
    view,
    save: (s: Sub) => JSON.stringify({ ...s, lastId: undefined }), // BUG: lastId is not written
    load: (t: string) => ({ lastId: 0, ...JSON.parse(t) }),
  },
  'reject-leaks': { init: initial, step: rejectLeaks, view, save: goodSave, load: goodLoad },
}
