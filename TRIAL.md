# Timed trial (Phase 0)

## Success targets (write them down BEFORE the first tester starts; do not change them afterwards)

Forge "works" for this trial only if all of these hold:

| Claim | Target | Measured by |
| --- | --- | --- |
| People can use it | at least 4 of 5 testers reach VERIFIED on a real rule from their own app | the sheet below |
| It is fast enough | median time to VERIFIED under 45 minutes | the sheet below |
| People read what they approve | every tester can name one thing their spec does NOT guarantee | the sheet below |
| The proof catches real bugs | `/forge mutate` catches at least 90 % of planted bugs across all testers; every survivor is read and classed as harmless or as a spec gap | `/forge mutate` output |
| It finds bugs people did not know about | at least one real bug (code disagrees with the tester's own answer) per two rules | the sheet below |
| It does not cry wolf | false alarms (a failure that was not a real problem) are counted and reported | the sheet below |

Report the result against these targets, including every target missed. Never report only the best run.

Goal: the first real numbers on how long one rule takes with Forge, and where people get stuck.
One sheet per person. Do not help unless the person is stuck for 5 minutes; write down every help.

## Before you start

- Claude Code installed. Python 3.8+ on PATH. Install the mod:
  `/plugin install forge --marketplace mack-dot/forge-claude-mod`
- Pick ONE small rule from your own app that must not break (a limit, a price, a permission,
  a state change). Write it here in one sentence before you start:

  > Rule:

## Run

Start a timer. Optionally type `/forge:scan`. Then type `/forge:add` and your rule in your own words, and
follow the steps until `/forge check` shows VERIFIED.
Then run `/forge mutate` and keep its output.
Optional second part: `/forge:build-setup`, then `/forge build` until it shows BUILT and your app calls the
built code.

## Record

| Item | Value |
| --- | --- |
| Tester (role only, no name) | |
| App language | |
| Had the tester used Dafny before? | |
| Minutes: start to Dafny installed | |
| Minutes: start to a spec shown for approval | |
| Minutes: start to `/forge approve yes` | |
| Minutes: start to VERIFIED | |
| Minutes: start to BUILT (optional) | |
| Questions Claude asked about the rule | |
| Questions that changed what the tester wanted | |
| Times the tester was stuck for 5 minutes or more, and where | |
| Helps given by the observer | |
| Did the tester read the spec before approving? (observed) | |
| Could the tester say, afterwards, one thing the spec does NOT guarantee? | |
| Gave up? At which step, and why | |
| `/forge mutate`: planted bugs caught / total | |
| Survivors: harmless, or a spec gap (one line each) | |
| Real bugs found: places where the code disagreed with the tester's own answers | |
| False alarms: failures that were not real problems | |

## After

- One thing that confused you:
- One thing you would not trust yet:
- Would you use it again on a second rule? Why or why not:

Keep the filled sheets. Report medians and the full list of stuck points, not only the best run.
