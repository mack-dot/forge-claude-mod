include "../spec/SubscriptionSpec.dfy"

module Subscription refines SubscriptionSpec {
  function Step ... {
    if e.id < 1 then Err(BadInput)
    else if e.id <= s.lastId then Err(Duplicate)
    else if (e.kind == Start || e.kind == Renew) && e.len < 1 then Err(BadInput)
    else match e.kind
      case Start => if s.status == New then Ok(State(Active, now + e.len, 0, e.id, 0)) else Err(BadState)
      case Renew =>
        if s.status != Active then Err(BadState)
        else if now >= s.expiresAt then Err(Expired)
        else Ok(s.(expiresAt := s.expiresAt + e.len, lastId := e.id, renewals := s.renewals + 1))
      case Pause =>
        if s.status != Active then Err(BadState)
        else if now >= s.expiresAt then Err(Expired)
        else Ok(s.(status := Paused, left := s.expiresAt - now, lastId := e.id))
      case Resume =>
        if s.status != Paused then Err(BadState)
        else Ok(s.(status := Active, expiresAt := now + s.left, left := 0, lastId := e.id))
      case Cancel =>
        if s.status == Active || s.status == Paused then Ok(s.(status := Canceled, left := 0, lastId := e.id))
        else Err(BadState)
  }
}
