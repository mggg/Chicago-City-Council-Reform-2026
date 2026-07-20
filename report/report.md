# Chicago City Council Reform 2026

## Table of Contents

- [Background](#background)
- [Data](#data)
- [Methodology](#methodology)
  - [Candidate Availability](#candidate-availability)
- [Configurations](#configurations)
- [Shared parameters](#shared-parameters)
- [Cohesion matrices](#cohesion-matrices)
- [Initial Results](#initial-results)
  - [Basic – 50×1 Plurality](#basic--501-plurality)
  - [10 × 5 STV](#10--5-stv)
  - [10 × 3 STV](#10--3-stv)
  - [Low POC Turnout](#low-poc-turnout)
  - [10 × 5 STV - Asian Bloc Separate](#10--5-stv---asian-bloc-separate)
  - [10 × 5 STV - Larger Asian Districts](#10--5-stv---larger-asian-districts)
  - [50 × 1 IRV](#50--1-irv)
  - [50 × 1 IRV - Larger Asian Districts](#50--1-irv---larger-asian-districts)
  - [50 × 1 PSMD - Larger Asian Districts](#50--1-psmd---larger-asian-districts)

<div style="page-break-after: always;"></div>

## Background

This project is intended to be a replication of the Metric Geometry and Gerrymandering Group's
2019 report on city council reform in the city of Chicago. The Chicago city council--both then and now--elects council members (alderpersons) from **50 single-member districts (wards)** using a runoff system that sees the top two vote-getters in a general election face each other in a runoff election if no candidate has secured a majority vote in the general. Since 2019, Chicago has had another city council election in 2023 in which [xx] new members were elected to city council. It was also the first city-wide election to use the new district maps drawn up after the latest decennial census in 2020. Combined with the shifting demographics and geography of the city, we thought it worthwhile to revisit the 2019 report and apply more mature methods to an analysis of Chicago city council elections.

This report employs new and updated tools like GerryChain and VoteKit in order to simulate a variety of electoral systems and scenarios - both from the original report and more novel configurations. Primarily, we look at simulations of multi-member districts elected by **Single Transferable Vote (STV)**, low person-of-color turnout, and optimizing for larger Asian bloc percentage-share in both 50 and 10 district plans.

## Data

- **Geodata:** `./data/chicago_precincts_vap_cvap.gpkg` (1,291 Chicago precincts).
- **Census Data**: Demographic data pulled from the 2020 Decennial Census at the block level and aggregated to precincts.

## Methodology

### Candidate Availability

To simulate candidate availability per-ward for each voting bloc, we make a few decisions and assumptions guided by available evidence from previous Chicago general city council elections. The first is deciding how many total candidates will be on the ballot for a given ward. In 2023, the average number of candidates running across all 50 wards (not counting write-ins) was approximately 3.48, with a large number of wards featuring anywhere between one to four candidates in the election, and fewer featuring counts larger than five.

![2023 Candidate Counts by Ward](../assets/candidates-by-ward.png)

To model this in our simulation, we sample from the first-success geometric distribution with a success probability of $0.3$, which provides an expected value of $3.33$ - roughly in the same ballpark as the average total candidates running per district in 2023. We sample in this way for every single district in every subsample of the districting ensemble, allowing some variance in total available candidates across districts and plans. However, because generating values in this way could result in a candidate pool size that is large enough to be unrealistic (and computationally expensive,) we set a cap on the total candidates by making a calculation with the district VAP:

$$Max\ Candidates = \lceil log_{10}(District\ VAP) \rceil$$

The application of the logarithmic function with each district's VAP as input serves to mirror the maximum observed candidates in the 2023 general election - 11 candidates in both Ward 5 and Ward 6. Since each ward in the existing 50 district maps contains an average of 44,000 constituents in the voting age population, this calculation will result in 11 total candidates. Using the same formula, maps with 10 districts will be expected to see a cap of 13 candidates - the reasoning here that larger districts with more seats available would see a larger candidate pool with the size limited by the willingness or ability of would-be candidates to persist their campaigns until election day.

Next, we make an assumption that the racial composition of the slate pool will be roughly proportional to that of the VAP in each district. Using the bloc proportions to create an interval with the intent to sample candidates of different slates from this interval. However, before we do we first square each element, normalizing the "squared interval" over the sum of the squared values. This creates an "exaggeration" effect when we sample slate candidates. In other words, if a district has a large Black VAP, it's even more likely that the Black voter-preferred slate of candidates will be larger than the others. Similarly, if the Asian VAP is small it's much less likely that the Asian voter-preferred slate will have many candidates - if any, since we allow for slates to be empty. This is intended to model how community dynamics, segregation, or lack of institutional support may impact candidate availability across geography with respect to race.


## Configurations

| Run Name | Config File | Districts | Seats | Voting Method | Voter Blocs | Turnout (bloc) |
|---|---|---|---|---|---|---|
| Basic – 50×1 Plurality | `basic.json` | 50 × 1 | 50 | Plurality | W-A, B, H | 1.00 / 1.00 / 1.00 |
| Low POC Turnout | `low-poc-turnout.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 0.75 / 0.50 / 0.50 |
| Asian Bloc Separate – Basic | `asian-seperate-bloc.json` | 10 × 5 | 50 | FastSTV (5 seats) | A, W, B, H | 1.00 / 1.00 / 1.00 / 1.00 |
| 10 × 3 STV | `10x3-stv.json` | 10 × 3 | 30 | FastSTV (3 seats) | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 10 × 5 STV | `10x5-stv.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 10 × 5 STV - Larger Asian Districts | `asian_optimized.json` | 10 × 5 | 50 | FastSTV (5 seats) | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 50 × 1 IRV | `50-irv.json` | 50 × 1 | 50 | IRV | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 50 × 1 IRV - Larger Asian Districts | `50-irv-asian-optimized.json` | 50 × 1 | 50 | IRV | W-A, B, H | 1.00 / 1.00 / 1.00 |
| 50 × 1 PSMD - Larger Asian Districts | `50-psmd-asian-optimized.json` | 50 × 1 | 50 | Plurality | W-A, B, H | 1.00 / 1.00 / 1.00 |

- **Basic** — roughly the status quo baseline (50 single-member wards, plurality.)
- **Low POC Turnout** — turnout sensitivity: every other run assumes full turnout
  (1.00) across all blocs, so this run is the only one that varies it. The `W-A`
  bloc turns out at 0.75, while Black and Latino turnout is lower, at 0.50 each.
- **Asian Bloc Separate** — bloc-structure sensitivity: Asian voters modeled as
  their own 4th bloc instead of being merged into `W-A`. We are curious to understand how modeling Asian voters as a separate bloc impacts their city council representation if candidates from their preferred slate receive heavier Asian voter support and a modest amount of crossover support from the white voting bloc.
- **10 × 3 STV** and **10 × 5 STV** — the multi-member STV alternatives (3- and
  5-seat districts).
- **50 × 1 IRV** — same neutral districting, bloc structure, and turnout as
  Basic, but elected by Instant-Runoff Voting instead of plurality.
- **"Larger Asian Districts" runs** (10 × 5 STV, 50 × 1 IRV, and 50 × 1 PSMD
  variants) — same voter-bloc structure and turnout as their neutral
  counterparts, but the districting ensemble itself is biased: Gingleator
  short-burst optimization (`optimize_for_bloc: "A"`) resamples the chain
  toward plans with more districts above a target Asian-VAP-share threshold
  (0.15 for the STV variant, 0.20 for the two 50 × 1 variants), instead of the
  neutral ReCom chain the other runs use.

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
with noise). Six figure types are shown for each completed run: a **by-mode
histogram** (distribution of citywide seats won by Asian-preferred candidates,
one series per voter model, against the Asian share-of-VAP and combined-support
reference lines), a **bubble plot** (the same distribution, sized by occurrence
count, one row per voter model plus a pooled "Combined" row), a **by-slate
panel** (the same by-mode histogram repeated once per candidate slate — White,
Black, Latino, Asian — each against its own share-of-VAP and combined-support
reference lines), a **coalition win-rate boxplot** (districts ranked by the
focal group's VAP share, low to high, and pooled across sampled plans, with
each box colored by how often that rank actually elected the group's preferred
candidate), the same **coalition win-rate boxplot restricted to districts
with an available candidate** (excluding districts where the focal slate was
apportioned zero candidates and so could never win, regardless of VAP share),
and a **coalition win-rate grid** (the unrestricted design repeated once per
racial group in a 2×2 layout).

### Comparison to 2019 Report - 10 X 5 STV


### Basic – 50×1 Plurality

<div class="figure-row">
  <img src="../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x50_PLURALITY_bymode.png" alt="Basic by-mode histogram">
  <img src="../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x50_bubbles_by_method.png" alt="Basic bubble plot">
</div>

![Basic by-slate panel](../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x50_PLURALITY_byslate.png)

<div style="page-break-after: always;"></div>

![Basic coalition win-rate boxplot](../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x1_A_slate_pl_coalition_boxplot.png)

![Basic coalition win-rate boxplot, districts with an available Asian candidate](../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x1_A_slate_pl_coalition_boxplot_available.png)

![Basic coalition win-rate grid](../figures/Basic%20-%2050%20X%201%20Plurality/Basic%20-%2050%20X%201%20Plurality_50x1_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 10 × 5 STV

<div class="figure-row">
  <img src="../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_FASTSTV_bymode.png" alt="10 X 5 STV by-mode histogram">
  <img src="../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_bubbles_by_method.png" alt="10 X 5 STV bubble plot">
</div>

![10 X 5 STV by-slate panel](../figures/10%20X%205%20STV/10%20X%205%20STV_10x50_FASTSTV_byslate.png)

<div style="page-break-after: always;"></div>

![10 X 5 STV coalition win-rate boxplot](../figures/10%20X%205%20STV/10%20X%205%20STV_10x5_A_slate_pl_coalition_boxplot.png)

![10 X 5 STV coalition win-rate boxplot, districts with an available Asian candidate](../figures/10%20X%205%20STV/10%20X%205%20STV_10x5_A_slate_pl_coalition_boxplot_available.png)

![10 X 5 STV coalition win-rate grid](../figures/10%20X%205%20STV/10%20X%205%20STV_10x5_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 10 × 3 STV

<div class="figure-row">
  <img src="../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_STV_bymode.png" alt="10 X 3 STV by-mode histogram">
  <img src="../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_bubbles_by_method.png" alt="10 X 3 STV bubble plot">
</div>

![10 X 3 STV by-slate panel](../figures/10%20X%203%20STV/10%20X%203%20STV_10x30_STV_byslate.png)

<div style="page-break-after: always;"></div>

![10 X 3 STV coalition win-rate boxplot](../figures/10%20X%203%20STV/10%20X%203%20STV_10x3_A_slate_pl_coalition_boxplot.png)

![10 X 3 STV coalition win-rate boxplot, districts with an available Asian candidate](../figures/10%20X%203%20STV/10%20X%203%20STV_10x3_A_slate_pl_coalition_boxplot_available.png)

![10 X 3 STV coalition win-rate grid](../figures/10%20X%203%20STV/10%20X%203%20STV_10x3_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### Low POC Turnout

<div class="figure-row">
  <img src="../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_FASTSTV_bymode.png" alt="Low POC Turnout by-mode histogram">
  <img src="../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_bubbles_by_method.png" alt="Low POC Turnout bubble plot">
</div>

![Low POC Turnout by-slate panel](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x50_FASTSTV_byslate.png)

<div style="page-break-after: always;"></div>

![Low POC Turnout coalition win-rate boxplot](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x5_A_slate_pl_coalition_boxplot.png)

![Low POC Turnout coalition win-rate boxplot, districts with an available Asian candidate](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x5_A_slate_pl_coalition_boxplot_available.png)

![Low POC Turnout coalition win-rate grid](../figures/Low%20POC%20Turnout/Low%20POC%20Turnout_10x5_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 10 × 5 STV - Asian Bloc Separate

<div class="figure-row">
  <img src="../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x50_FASTSTV_bymode.png" alt="Asian Bloc Separate by-mode histogram">
  <img src="../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x50_bubbles_by_method.png" alt="Asian Bloc Separate bubble plot">
</div>

![Asian Bloc Separate by-slate panel](../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x50_FASTSTV_byslate.png)

<div style="page-break-after: always;"></div>

![Asian Bloc Separate coalition win-rate boxplot](../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x5_A_slate_pl_coalition_boxplot.png)

![Asian Bloc Separate coalition win-rate boxplot, districts with an available Asian candidate](../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x5_A_slate_pl_coalition_boxplot_available.png)

![Asian Bloc Separate coalition win-rate grid](../figures/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate/10%20X%205%20STV%20-%20Asian%20Bloc%20Separate_10x5_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 10 × 5 STV - Larger Asian Districts

Gingleator short-burst optimized ensemble (`asian_optimized.json`), biasing the
10-district chain toward higher-Asian-VAP-share districts instead of the neutral
ReCom chain the other STV runs use.

<div class="figure-row">
  <img src="../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x50_STV_bymode.png" alt="10 X 5 STV Larger Asian Districts by-mode histogram">
  <img src="../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x50_bubbles_by_method.png" alt="10 X 5 STV Larger Asian Districts bubble plot">
</div>

![10 X 5 STV Larger Asian Districts by-slate panel](../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x50_STV_byslate.png)

<div style="page-break-after: always;"></div>

![10 X 5 STV Larger Asian Districts coalition win-rate boxplot](../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x5_A_slate_pl_coalition_boxplot.png)

![10 X 5 STV Larger Asian Districts coalition win-rate boxplot, districts with an available Asian candidate](../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x5_A_slate_pl_coalition_boxplot_available.png)

![10 X 5 STV Larger Asian Districts coalition win-rate grid](../figures/10%20X%205%20STV%20-%20Larger%20Asian%20Districts/10%20X%205%20STV%20-%20Larger%20Asian%20Districts_10x5_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 50 × 1 IRV

Single-member wards (neutral districting), elected by Instant-Runoff Voting
instead of plurality — otherwise the same setup as Basic.

<div class="figure-row">
  <img src="../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x50_IRV_bymode.png" alt="50 X 1 IRV by-mode histogram">
  <img src="../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x50_bubbles_by_method.png" alt="50 X 1 IRV bubble plot">
</div>

![50 X 1 IRV by-slate panel](../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x50_IRV_byslate.png)

<div style="page-break-after: always;"></div>

![50 X 1 IRV coalition win-rate boxplot](../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x1_A_slate_pl_coalition_boxplot.png)

![50 X 1 IRV coalition win-rate boxplot, districts with an available Asian candidate](../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x1_A_slate_pl_coalition_boxplot_available.png)

![50 X 1 IRV coalition win-rate grid](../figures/50%20X%201%20IRV/50%20X%201%20IRV_50x1_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 50 × 1 IRV - Larger Asian Districts

Single-member wards, IRV, with the same Gingleator opportunity-district
optimizer applied to the 50-district ensemble (`50-irv-asian-optimized.json`).

<div class="figure-row">
  <img src="../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x50_IRV_bymode.png" alt="50 X 1 IRV Larger Asian Districts by-mode histogram">
  <img src="../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x50_bubbles_by_method.png" alt="50 X 1 IRV Larger Asian Districts bubble plot">
</div>

![50 X 1 IRV Larger Asian Districts by-slate panel](../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x50_IRV_byslate.png)

<div style="page-break-after: always;"></div>

![50 X 1 IRV Larger Asian Districts coalition win-rate boxplot](../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x1_A_slate_pl_coalition_boxplot.png)

![50 X 1 IRV Larger Asian Districts coalition win-rate boxplot, districts with an available Asian candidate](../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x1_A_slate_pl_coalition_boxplot_available.png)

![50 X 1 IRV Larger Asian Districts coalition win-rate grid](../figures/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts/50%20X%201%20IRV%20-%20Larger%20Asian%20Districts_50x1_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>

### 50 × 1 PSMD - Larger Asian Districts

Single-member wards, Plurality (PSMD), with the Gingleator opportunity-district
optimizer applied (`50-psmd-asian-optimized.json`) — the plurality counterpart
to the IRV-optimized run above.

<div class="figure-row">
  <img src="../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x50_PLURALITY_bymode.png" alt="50 X 1 PSMD Larger Asian Districts by-mode histogram">
  <img src="../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x50_bubbles_by_method.png" alt="50 X 1 PSMD Larger Asian Districts bubble plot">
</div>

![50 X 1 PSMD Larger Asian Districts by-slate panel](../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x50_PLURALITY_byslate.png)

<div style="page-break-after: always;"></div>

![50 X 1 PSMD Larger Asian Districts coalition win-rate boxplot](../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x1_A_slate_pl_coalition_boxplot.png)

![50 X 1 PSMD Larger Asian Districts coalition win-rate boxplot, districts with an available Asian candidate](../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x1_A_slate_pl_coalition_boxplot_available.png)

![50 X 1 PSMD Larger Asian Districts coalition win-rate grid](../figures/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts/50%20X%201%20PSMD%20-%20Larger%20Asian%20Districts_50x1_slate_pl_coalition_boxplot_grid.png)

<div style="page-break-after: always;"></div>
