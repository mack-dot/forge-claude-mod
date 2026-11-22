# Decisions: wallet-cap

Record of the /forge add conversation. Each decision is checked by Dafny in
`spec/WalletExamples.dfy`; approval covers this file, the examples and the spec.

Rule in the person's words: "A wallet must never spend more than its cap."

## Defaults confirmed
- Money is whole cents (integers), no floats.
- Invalid input is rejected and changes nothing.

## Choices
| Id | Question (concrete) | Answer |
| --- | --- | --- |
| D1 | Cap $50.00, spent $20.00, spend $30.00: allow reaching exactly the cap? | Allow |
| D2 | Cap $50.00, spent $40.00, spend $20.00: reject, or cut to $10.00? | Reject; never a partial spend |
| D3 | Spend $0.00: reject, or accept as a no-op? | Reject |
| D4 | Spend -$10.00: reject, or treat as a refund? | Reject; refunds are a separate rule |

## Out of scope (not guaranteed by this check)
- Who may spend (authorization).
- Several wallets sharing one cap; concurrency; persistence.
