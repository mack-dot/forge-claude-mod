// Class __default
// Dafny class __default compiled into Java
package Wallet;

@SuppressWarnings({"unchecked", "deprecation"})
public class __default {
  public __default() {
  }
  public static dafny.Tuple2 Spend(State s, java.math.BigInteger amount)
  {
    State next = State.Default();
    Outcome outcome = Outcome.Default();
    if(true) {
      if (((amount).signum() == 1) && ((((s).dtor_spent()).add(amount)).compareTo((s).dtor_cap()) <= 0)) {
        next = State.create((s).dtor_cap(), ((s).dtor_spent()).add(amount));
        outcome = Outcome.create_Ok();
      } else {
        next = s;
        outcome = Outcome.create_Rejected();
      }
    }
    return new dafny.Tuple2<>(next, outcome);
  }
  public static boolean Valid(State s) {
    return (((s).dtor_spent()).signum() != -1) && (((s).dtor_spent()).compareTo((s).dtor_cap()) <= 0);
  }
  @Override
  public java.lang.String toString() {
    return "Wallet._default";
  }
}
