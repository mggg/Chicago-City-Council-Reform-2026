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


## Data

- **Geodata:** `./data/chicago_precincts_vap_cvap.gpkg` (1,291 Chicago precincts).
- **Census Data**: Demographic data pulled from the 2020 Decennial Census at the block level and aggregated to precincts.


## Configurations

| Run Name | Config File | Districts | Seats | Voting Method | Voter Blocs | Turnout (bloc) |
|---|---|---|---|---|---|---|
| Basic – 50×1 Plurality | `basic.json` | 50 × 1 | 50 | Plurality | W-A, B, H | 1.00 / 1.00 / 1.00 |
| Low POC Turnout | `low-poc-turnout.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 0.75 / 0.50 / 0.50 |
| Asian Bloc Separate – Basic | `asian-seperate-bloc.json` | 10 × 5 | 50 | FastSTV (5 seats) | A, W, B, H | 1.00 / 1.00 / 1.00 / 1.00 |
| 10 × 3 STV | `10x3-stv.json` | 10 × 3 | 30 | FastSTV (3 seats) | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 10 × 5 STV | `10x5-stv.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 1.00 / 1.00 / 1.00 |

- **Basic** — roughly the status quo baseline (50 single-member wards, plurality.)
- **Low POC Turnout** — turnout sensitivity: every other run assumes full turnout
  (1.00) across all blocs, so this run is the only one that varies it. The `W-A`
  bloc turns out at 0.75, while Black and Latino turnout is lower, at 0.50 each.
- **Asian Bloc Separate** — bloc-structure sensitivity: Asian voters modeled as
  their own 4th bloc instead of being merged into `W-A`. We are curious to understand how modeling Asian voters as a separate bloc impacts their city council representation if candidates from their preferred slate receive heavier Asian voter support and a modest amount of crossover support from the white voting bloc.
- **10 × 3 STV** and **10 × 5 STV** — the multi-member STV alternatives (3- and
  5-seat districts).

## Shared parameters

Held constant across every run:

- **Candidate slates:** `W` ×4, `B` ×3, `H` ×3, `A` ×1 (11 total) as a base assumption. However, per-district
  candidate counts are no longer fixed to this base list: during settings
  generation, each slate's count is reapportioned in proportion to its VAP share
  in that specific district (a slate apportioned zero candidates is dropped from
  that district entirely), then perturbed by `candidate_noise_probability`, which introduces an element of "randomness" into candidate availability at a per-slate-per-district level. 
- **Candidate noise:** `candidate_noise_probability`: 0.10 — per slate, per
  district, a 10% independent chance of gaining a candidate and a 10% chance of losing one - modeling how candidate availability may vary in reality regardless of demographic proportions.
- **Voter models:** `slate_pl` (Plackett-Luce) and `slate_bt` (Bradley-Terry).
- **Focal group:** `A` (Asian).
- **Voters per district:** 10,000 · 
- **Replicates per district:** 10.
- **GerryChain ensemble:** chain length 10,000 · 50 subsampled plans · ε = 0.05.
- **Alphas (Dirichlet):** all 1 (uniform within-slate preferences).
- **Tiebreak:** random


## Cohesion matrices

Rows are voter blocs, columns are candidate slates; each row sums to 1.

**Standard 3-bloc** (`basic`, `low-poc-turnout`, `10x3-stv`, `10x5-stv`):

| bloc ↓ / slate → | W | B | H | A |
|---|---|---|---|---|
| **W-A** | 0.46 | 0.14 | 0.14 | 0.26 |
| **B** | 0.15 | 0.70 | 0.10 | 0.05 |
| **H** | 0.15 | 0.10 | 0.65 | 0.10 |

The `W-A` row is the VAP-weighted average (81% White / 19% Asian) of the
underlying White and Asian rows (below).

**Asian Bloc Separate** (`asian-seperate-bloc`):

| bloc ↓ / slate → | W | B | H | A |
|---|---|---|---|---|
| **W** | 0.50 | 0.15 | 0.15 | 0.20 |
| **A** | 0.30 | 0.10 | 0.10 | 0.50 |
| **B** | 0.15 | 0.70 | 0.10 | 0.05 |
| **H** | 0.15 | 0.10 | 0.65 | 0.10 |

The cohesion parameters here have been selected based on available estimates (from ecological regression) from the earlier 2019 MGGG study on Chicago city council reform, as well as a mayoral election analysis from the University of Illinois-Chicago. The details can be found in the accompanying writeup.


## Initial Results

Figures below are from the current pipeline (updated cohesion matrix, full
turnout except where noted, and per-district proportional candidate counts
with noise). Three figure types are shown for each completed run: a **by-mode
histogram** (distribution of citywide seats won by Asian-preferred candidates,
one series per voter model, against the Asian share-of-VAP and combined-support
reference lines), a **bubble plot** (the same distribution, sized by occurrence
count, one row per voter model plus a pooled "Combined" row), and a **by-slate
panel** (the same by-mode histogram repeated once per candidate slate — White,
Black, Latino, Asian — each against its own share-of-VAP and combined-support
reference lines).


### Basic – 50×1 Plurality

`Basic – 50×1 Plurality` hasn't been re-run under the current settings yet, so
its figures aren't included here — this section will be updated once that run
completes.


### 10 × 5 STV

![10 X 5 STV by-mode histogram](../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_FASTSTV_bymode.png)

![10 X 5 STV bubble plot](../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_bubbles_by_method.png)

![10 X 5 STV by-slate panel](../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_FASTSTV_byslate.png)


### 10 × 3 STV

![10 X 3 STV by-mode histogram](../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_FASTSTV_bymode.png)

![10 X 3 STV bubble plot](../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_bubbles_by_method.png)

![10 X 3 STV by-slate panel](../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_FASTSTV_byslate.png)


### Low POC Turnout

![Low POC Turnout by-mode histogram](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_FASTSTV_bymode.png)

![Low POC Turnout bubble plot](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_bubbles_by_method.png)

![Low POC Turnout by-slate panel](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_FASTSTV_byslate.png)


### Asian Bloc Separate – Basic

![Asian Bloc Separate by-mode histogram](../figures/Asian%20Bloc%20Separate%20-%20Basic/Asian%20Bloc%20Separate%20-%20Basic_10x50_FASTSTV_bymode.png)

![Asian Bloc Separate bubble plot](../figures/Asian%20Bloc%20Separate%20-%20Basic/Asian%20Bloc%20Separate%20-%20Basic_10x50_bubbles_by_method.png)

![Asian Bloc Separate by-slate panel](../figures/Asian%20Bloc%20Separate%20-%20Basic/Asian%20Bloc%20Separate%20-%20Basic_10x50_FASTSTV_byslate.png)

