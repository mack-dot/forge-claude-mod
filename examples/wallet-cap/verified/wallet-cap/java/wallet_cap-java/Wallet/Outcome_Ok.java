// Class Outcome_Ok
// Dafny class Outcome_Ok compiled into Java
package Wallet;

@SuppressWarnings({"unchecked", "deprecation"})
public class Outcome_Ok extends Outcome {
  public Outcome_Ok () {
    super();
  }

  @Override
  public boolean equals(Object other) {
    if (this == other) return true;
    if (other == null) return false;
    if (getClass() != other.getClass()) return false;
    Outcome_Ok o = (Outcome_Ok)other;
    return true;
  }
  @Override
  public int hashCode() {
    long hash = 5381;
    hash = ((hash << 5) + hash) + 0;
    return (int)hash;
  }

  @Override
  public String toString() {
    StringBuilder s = new StringBuilder();
    s.append("Wallet.Outcome.Ok");
    return s.toString();
  }
}
