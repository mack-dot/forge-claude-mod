// Class State
// Dafny class State compiled into Java
package Wallet;

@SuppressWarnings({"unchecked", "deprecation"})
public class State {
  public java.math.BigInteger _cap;
  public java.math.BigInteger _spent;
  public State (java.math.BigInteger cap, java.math.BigInteger spent) {
    this._cap = cap;
    this._spent = spent;
  }

  @Override
  public boolean equals(Object other) {
    if (this == other) return true;
    if (other == null) return false;
    if (getClass() != other.getClass()) return false;
    State o = (State)other;
    return true && java.util.Objects.equals(this._cap, o._cap) && java.util.Objects.equals(this._spent, o._spent);
  }
  @Override
  public int hashCode() {
    long hash = 5381;
    hash = ((hash << 5) + hash) + 0;
    hash = ((hash << 5) + hash) + java.util.Objects.hashCode(this._cap);
    hash = ((hash << 5) + hash) + java.util.Objects.hashCode(this._spent);
    return (int)hash;
  }

  @Override
  public String toString() {
    StringBuilder s = new StringBuilder();
    s.append("Wallet.State.Wallet");
    s.append("(");
    s.append(dafny.Helpers.toString(this._cap));
    s.append(", ");
    s.append(dafny.Helpers.toString(this._spent));
    s.append(")");
    return s.toString();
  }
  private static final dafny.TypeDescriptor<State> _TYPE = dafny.TypeDescriptor.<State>referenceWithInitializer(State.class, () -> State.Default());
  public static dafny.TypeDescriptor<State> _typeDescriptor() {
    return (dafny.TypeDescriptor<State>) (dafny.TypeDescriptor<?>) _TYPE;
  }

  private static final State theDefault = State.create(java.math.BigInteger.ZERO, java.math.BigInteger.ZERO);
  public static State Default() {
    return theDefault;
  }
  public static State create(java.math.BigInteger cap, java.math.BigInteger spent) {
    return new State(cap, spent);
  }
  public static State create_Wallet(java.math.BigInteger cap, java.math.BigInteger spent) {
    return create(cap, spent);
  }
  public boolean is_Wallet() { return true; }
  public java.math.BigInteger dtor_cap() {
    return this._cap;
  }
  public java.math.BigInteger dtor_spent() {
    return this._spent;
  }
}
