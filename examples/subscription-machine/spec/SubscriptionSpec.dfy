// A subscription lifecycle as a state machine: Step(state, event, now) gives the next state or a rejection.
// Time is a whole number of ticks; a subscription that is Active is expired at and after expiresAt.
// Every event has an id of 1 or more; an id at or below the last applied id is a duplicate (a retry or a replay).
abstract module SubscriptionSpec {
  datatype Status = New | Active | Paused | Canceled
  datatype State = State(status: Status, expiresAt: int, left: int, lastId: int, renewals: int)
  datatype Kind = Start | Renew | Pause | Resume | Cancel
  datatype Event = Event(kind: Kind, id: int, len: int)
  datatype Reject = Duplicate | BadInput | BadState | Expired
  datatype Result = Ok(next: State) | Err(why: Reject)

  function Init(): State { State(New, 0, 0, 0, 0) }

  predicate Valid(s: State) {
    s.lastId >= 0 && s.renewals >= 0 && s.left >= 0 && (s.status == Paused <==> s.left > 0)
  }

  function Step(s: State, e: Event, now: int): Result
    requires Valid(s)
    // M1: every accepted event gives a valid state.
    ensures Step(s, e, now).Ok? ==> Valid(Step(s, e, now).next)
    // M2: an event whose id is at or below the last applied id (a retry or a replay) is rejected; nothing changes.
    ensures 1 <= e.id <= s.lastId ==> Step(s, e, now) == Err(Duplicate)
    // M2b: an id below 1 is never accepted.
    ensures e.id < 1 ==> Step(s, e, now) == Err(BadInput)
    // M3: an accepted event records its id, so the same event can never apply twice.
    ensures Step(s, e, now).Ok? ==> Step(s, e, now).next.lastId == e.id
    // M4: a Renew or a Pause at or after the expiry time of an Active subscription is rejected as Expired.
    ensures e.id > s.lastId && s.status == Active && now >= s.expiresAt && (e.kind == Pause || (e.kind == Renew && e.len >= 1))
      ==> Step(s, e, now) == Err(Expired)
    // M5: a Resume gives back exactly the time that was left at the Pause.
    ensures e.id > s.lastId && s.status == Paused && e.kind == Resume
      ==> Step(s, e, now) == Ok(State(Active, now + s.left, 0, e.id, s.renewals))
    // M6: a Renew that is accepted adds exactly its length to the expiry time and counts one renewal.
    ensures e.kind == Renew && Step(s, e, now).Ok?
      ==> Step(s, e, now).next == s.(expiresAt := s.expiresAt + e.len, lastId := e.id, renewals := s.renewals + 1)
    // M7: a Canceled subscription accepts nothing.
    ensures s.status == Canceled ==> Step(s, e, now).Err?
}
