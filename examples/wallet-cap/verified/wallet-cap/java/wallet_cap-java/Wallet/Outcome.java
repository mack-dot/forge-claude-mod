// Class Outcome
// Dafny class Outcome compiled into Java
package Wallet;

@SuppressWarnings({"unchecked", "deprecation"})
public abstract class Outcome {
  public Outcome() {
  }
  private static final dafny.TypeDescriptor<Outcome> _TYPE = dafny.TypeDescriptor.<Outcome>referenceWithInitializer(Outcome.class, () -> Outcome.Default());
  public static dafny.TypeDescriptor<Outcome> _typeDescriptor() {
    return (dafny.TypeDescriptor<Outcome>) (dafny.TypeDescriptor<?>) _TYPE;
  }

  private static final Outcome theDefault = Outcome.create_Ok();
  public static Outcome Default() {
    return theDefault;
  }
  public static Outcome create_Ok() {
    return new Outcome_Ok();
  }
  public static Outcome create_Rejected() {
    return new Outcome_Rejected();
  }
  public boolean is_Ok() { return this instanceof Outcome_Ok; }
  public boolean is_Rejected() { return this instanceof Outcome_Rejected; }
  public static java.util.ArrayList<Outcome> AllSingletonConstructors() {
    java.util.ArrayList<Outcome> singleton_iterator = new java.util.ArrayList<>();
    singleton_iterator.add(new Outcome_Ok());
    singleton_iterator.add(new Outcome_Rejected());
    return singleton_iterator;
  }
}
