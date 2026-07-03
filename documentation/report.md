# Kansas City Alternative Election Analysis

## Background

## Study Design

## Baseline: Modeling STV

## Variations and Alternatives

The table below lists each simulation configuration. Parameters shared across all runs are noted beneath the table.

| Run Name | Districts | Voting Method | POC Turnout | Voters per District | POC Cohesion (→POC / →W) | POC Alphas (→W / →POC) | Simulations | Config File |
|---|---|---|---|---|---|---|---|---|
| Basic - 4x3 STV | 4 × 3 | STV | 0.50 | 10,000 | 0.70 / 0.30 | 1 / 1 | 100 | basic.json |
| W60-C90 | 4 × 3 | STV | 0.50 | 10,000 | 0.90 / 0.10 | 1 / 1 | 100 | high-POC-cohesion.json |
| Low POC turnout | 4 × 3 | STV | 0.25 | 10,000 | 0.70 / 0.30 | 1 / 1 | 100 | low-poc-turnout.json |
| Strong candidates within each slate | 4 × 3 | STV | 0.50 | 10,000 | 0.70 / 0.30 | 2 / 0.5 | 100 | same-slate-strong-candidate.json |
| 12x1 Plurality | 12 × 1 | Plurality | 0.50 | 10,000 | 0.70 / 0.30 | 1 / 1 | 100 | 12x1-PMSD.json |
| 1,000 Voters per District | 4 × 3 | STV | 0.50 | 1,000 | 0.70 / 0.30 | 1 / 1 | 100 | 1K-voters.json |
| 10 Voter Profiles per District | 4 × 3 | STV | 0.50 | 10,000 | 0.70 / 0.30 | 1 / 1 | 10 | 10-profiles.json |
| 1,000 Voter Profiles per District | 4 × 3 | STV | 0.50 | 10,000 | 0.70 / 0.30 | 1 / 1 | 1,000 | 1K-profiles.json |
| 100K Voters per District | 4 × 3 | STV | 0.50 | 100,000 | 0.70 / 0.30 | 1 / 1 | 100 | 100K-voters.json |

**Shared parameters across all configurations:** W turnout = 0.50 · W cohesion (→W / →POC) = 0.60 / 0.40 · W alphas (→W / →POC) = 0.5 / 2 · chain length = 1,000 · total seats = 12 · ε = 0.05 · seed = 42

