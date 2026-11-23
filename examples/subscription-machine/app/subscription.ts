// The shipped subscription code (TypeScript): the app's own step function over plain JSON state. It must behave
// exactly like the proved model (Subscription.Step in impl/Subscription.dfy) for EVERY sequence of events, with
// retries, replays and restarts. Not proved: the trace fuzz (forge_check.py diff) compares it with the model.
export type Status = 'New' | 'Active' | 'Paused' | 'Canceled'
export type Kind = 'Start' | 'Renew' | 'Pause' | 'Resume' | 'Cancel'
export type Reject = 'Duplicate' | 'BadInput' | 'BadState' | 'Expired'
export interface Sub {
  status: Status
  expiresAt: number
  left: number // ticks left, kept while Paused
  lastId: number // the highest event id applied: a retry or a replay is a Duplicate
  renewals: number
}
export interface SubEvent {
  kind: Kind
  id: number
  len: number
}
export type StepResult = { ok: Sub } | { reject: Reject }

export function initial(): Sub {
  return { status: 'New', expiresAt: 0, left: 0, lastId: 0, renewals: 0 }
}

export function step(s: Sub, e: SubEvent, now: number): StepResult {
  if (e.id < 1) return { reject: 'BadInput' }
  if (e.id <= s.lastId) return { reject: 'Duplicate' }
  if ((e.kind === 'Start' || e.kind === 'Renew') && e.len < 1) return { reject: 'BadInput' }
  switch (e.kind) {
    case 'Start':
      return s.status === 'New' ? { ok: { status: 'Active', expiresAt: now + e.len, left: 0, lastId: e.id, renewals: 0 } } : { reject: 'BadState' }
    case 'Renew':
      if (s.status !== 'Active') return { reject: 'BadState' }
      if (now >= s.expiresAt) return { reject: 'Expired' }
      return { ok: { ...s, expiresAt: s.expiresAt + e.len, lastId: e.id, renewals: s.renewals + 1 } }
    case 'Pause':
      if (s.status !== 'Active') return { reject: 'BadState' }
      if (now >= s.expiresAt) return { reject: 'Expired' }
      return { ok: { ...s, status: 'Paused', left: s.expiresAt - now, lastId: e.id } }
    case 'Resume':
      if (s.status !== 'Paused') return { reject: 'BadState' }
      return { ok: { ...s, status: 'Active', expiresAt: now + s.left, left: 0, lastId: e.id } }
    case 'Cancel':
      if (s.status !== 'Active' && s.status !== 'Paused') return { reject: 'BadState' }
      return { ok: { ...s, status: 'Canceled', left: 0, lastId: e.id } }
  }
  throw new TypeError('unknown event kind')
}

// What the app writes to its database, and reads back after a restart.
export function save(s: Sub): string {
  return JSON.stringify(s)
}

export function load(text: string): Sub {
  const d = JSON.parse(text)
  return { status: d.status, expiresAt: d.expiresAt, left: d.left, lastId: d.lastId, renewals: d.renewals }
}
