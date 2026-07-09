# Chicago City Council Reform 2026 — Simulation Configurations

This document describes the simulation configurations that we've set up in the `configs/` directory. 

## Background

Chicago currently elects its City Council from **50 single-member wards** by
plurality. This project updates the Metric Geometry and Gerrymandering Group's
2019 city council reform proposals, comparing the status quo against
multi-member districts elected by **Single Transferable Vote (STV)**, along with
variations on turnout and voter-bloc structure. Representation is
measured by simulating elections over an ensemble of districting plans
and tallying the seats won by each group's candidates.

## Completed

- Updated pipeline geography to use Chicago's precincts from 2022/23. The total count dropped from ~2,000 to 1,291, but it still works for the purposes of generating a district map ensemble.
- Had to rework the pipeline in a number of places to be flexible enough for more than two voting blocs. Updated our configs, mapping setings, settings file generation, profile generation, and visualizations to account for this structural shift.
- Made the decision to switch to BT MCMC rather than normal BT for time/efficiency purposes - still working on fully understanding the ramifications of this decision
- Created a few initial visualizations to test the revamped pipeline and experiment with parameters a bit. Once I felt good about the basic configuration, I kicked off a run on the cluster - which will likely be running overnight.

## Configurations

| Run Name | Config File | Districts | Seats | Voting Method | Voter Blocs | Turnout (bloc) |
|---|---|---|---|---|---|---|
| Basic – 50×1 Plurality | `basic.json` | 50 × 1 | 50 | Plurality | W-A, B, H | 0.50 / 0.50 / 0.50 |
| Low POC Turnout | `low-poc-turnout.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 0.4529 / 0.25 / 0.25 |
| Asian Bloc Separate – Basic | `asian-seperate-bloc.json` | 10 × 5 | 50 | FastSTV (5 seats) | A, W, B, H | 0.50 / 0.50 / 0.50 / 0.50 |
| 10 × 3 STV | `10x3-stv.json` | 10 × 3 | 30 | FastSTV (3 seats) | W-A, B, H | 0.50 / 0.50 / 0.50 |
| 10 × 5 STV | `10x5-stv.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 0.50 / 0.50 / 0.50 |

- **Basic** — roughly the status quo baseline (50 single-member wards, plurality.
- **Low POC Turnout** — turnout sensitivity: Black, Latino, and Asian turnout at
  half the White rate (0.25 vs 0.50). The `W-A` bloc turnout is the VAP-weighted
  blend of its White (0.50) and Asian (0.25) portions — 0.4529, close to 0.50
  because White is ~81% of the bloc's VAP.
- **Asian Bloc Separate** — bloc-structure sensitivity: Asian voters modeled as
  their own 4th bloc instead of being merged into `W-A`. We are curious to see if available Asian candidates get elected with heavier Asian voter support and a moderate amount of crossover from other voting blocs.
- **10 × 3 STV** and **10 × 5 STV** — the multi-member STV alternatives (3- and
  5-seat districts).

## Shared parameters

Held constant across every run:

- **Geodata:** `./data/chicago_precincts_vap_cvap.gpkg` (1,291 Chicago precincts).
- **Candidate slates:** `W` ×4, `B` ×3, `H` ×3, `A` ×1.
- **Voter models:** `slate_pl` (Plackett-Luce) and `slate_bt` (Bradley-Terry, MCMC).
- **Focal group:** `A` (Asian).
- **Voters per district:** 10,000 · **Replicates per district:** 100.
- **GerryChain ensemble:** chain length 10,000 · 50 subsampled plans · ε = 0.05.
- **Alphas (Dirichlet):** all 1 (uniform within-slate preferences).
- **Tiebreak:** random · **Seed:** 42.

## Cohesion matrices

Rows are voter blocs, columns are candidate slates; each row sums to 1.

**Standard 3-bloc** (`basic`, `low-poc-turnout`, `10x3-stv`, `10x5-stv`):

| bloc ↓ / slate → | W | B | H | A |
|---|---|---|---|---|
| **W-A** | 0.35 | 0.15 | 0.15 | 0.35 |
| **B** | 0.15 | 0.40 | 0.30 | 0.15 |
| **H** | 0.30 | 0.15 | 0.40 | 0.15 |

The `W-A` row is the average of the underlying White and Asian rows (below).

**Asian Bloc Separate** (`asian-seperate-bloc`):

| bloc ↓ / slate → | W | B | H | A |
|---|---|---|---|---|
| **W** | 0.40 | 0.15 | 0.15 | 0.30 |
| **A** | 0.30 | 0.15 | 0.15 | 0.40 |
| **B** | 0.15 | 0.40 | 0.30 | 0.15 |
| **H** | 0.30 | 0.15 | 0.40 | 0.15 |

## To-Do / In Progress

- Currently running large baseline simulation on cluster, will proceed to run other simulations immediately following its completion
- Reworking visualizations to better illustrate toggle effects for each voting bloc
- VoteKit's Cambridge ballot generator only accepts two blocs - we've had to remove it from our 3+ bloc configurations, but I'd like to find another configuration for which we could include it alongside PL and BT since it's an interesting voter model. Thinking about doing "Asian voters vs. everyone else" since that population group is of specific interest - but open to thoughts on this!