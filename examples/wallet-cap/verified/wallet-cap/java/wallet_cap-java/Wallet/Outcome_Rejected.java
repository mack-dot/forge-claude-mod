// Class Outcome_Rejected
// Dafny class Outcome_Rejected compiled into Java
package Wallet;

@SuppressWarnings({"unchecked", "deprecation"})
public class Outcome_Rejected extends Outcome {
  public Outcome_Rejected () {
    super();
  }

  @Override
  public boolean equals(Object other) {
    if (this == other) return true;
    if (other == null) return false;
    if (getClass() != other.getClass()) return false;
    Outcome_Rejected o = (Outcome_Rejected)other;
    return true;
  }
  @Override
  public int hashCode() {
    long hash = 5381;
    hash = ((hash << 5) + hash) + 1;
    return (int)hash;
  }

  @Override
  public String toString() {
    StringBuilder s = new StringBuilder();
    s.append("Wallet.Outcome.Rejected");
    return s.toString();
  }
}
