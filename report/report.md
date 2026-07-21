# Revisiting Reform Proposals for Chicago City Council

## Contents

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

<div id='abstract'>
  <h3> Abstract </h5>
  <span>In April 2019, the Metric Geometry and Gerrymandering Group published a study on reform proposals and alternative electoral systems for Chicago City Council, stating that many observers would agree that Chicago's City Council ward system is entrenched in problematic gerrymandering, segregation, and inefficiency - issues many would argue still persist today. The goal of the 2019 study was to apply mathematical models to analyze the then-active ward plan and propose reforms to address these problems. This report seeks to replicate and update the results of the 2019 study by using the more current ward plan, newer demographic data, and more refined techniques that the lab has since developed. We will additionally be examining the impacts of the included reform proposals on representation for Asian voters - a voting bloc that has consistently gone underrepresented in Chicago City Council.</span>
</div>

## 1. Background

The Chicago city council--both then and now--elects council members (alderpersons) from **50 single-member districts (wards)** using a runoff system that sees the top two vote-getters in a general election face each other in a runoff election if no candidate has secured a majority vote in the general. Since 2019, Chicago has had another city council election in 2023 in which [xx] new members were elected to city council. It was also the first city-wide election to use the new district maps drawn up after the latest decennial census in 2020. Combined with the shifting demographics and geography of the city, we thought it worthwhile to revisit the 2019 report and apply more mature methods to an analysis of Chicago city council elections.

This report employs new and updated tools like GerryChain and VoteKit in order to simulate a variety of electoral systems and scenarios - both from the original report and more novel configurations. Primarily, we look at simulations of multi-member districts elected by **Single Transferable Vote (STV)**, low person-of-color turnout, and optimizing for larger Asian bloc percentage-share in both 50 and 10 district plans.

## 2. Data

### 2.1 Data units, collection, preprocessing

In this report, we have updated our demographic data source to use the 2020 Decennial Census from the United State Census Bureau - the 2019 report utilized the 2010 Decennial Census. The census provides census block-level data. Out of the 98,230 blocks located in Cook County, 38,785 of these fall within Chicago city boundaries. Before we generate our mapping ensemble, we aggregate data from census blocks up into ward precincts to serve as the base geographic unit.

Shapefile data for Chicago's wards and precincts has been obtained from the [Chicago Open Data Portal](https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-Ward-Precincts-2025-/i8fv-xe4b/about_data). By December 1 in the year following the release of every Decennial Census, Chicago's ward boundaries must be redrawn to reflect the population as demonstrated in the census. We use the most recent ward and precinct boundaries, which were redrawn on May 16, 2022. It is notable that while the overall count of 50 wards across the city remains the same, the total number of [precincts has dropped](https://news.wttw.com/2022/08/29/chicago-board-elections-shrinks-number-precincts-nearly-40) from 2069 to 1291 - a significant decrease of nearly 40% [1].  

### 2.2 Racial demographics and population shifts

This report mirrors the 2019 study in how it manages distinctions between racial demographics: Black referring to Black non-Hispanic population, White for White non-Hispanic, Asian for Asian non-Hispanic, and Hispanic/Latino for all people designated with the Hispanic ethnicity in the census regardless of race.

| Race | 2000 (Census) | 2010 (Census) | 2009-2013 (ACS) | 2013-2017 (ACS) | 2020 (Census) |
|---|---|---|---|---|---|
| Black (non-Hispanic) | 36.4% | 32.4% | 31.9% | 30.1% | 28.7% |
| White (non-Hispanic) | 31.3% | 31.7% | 32.2% | 32.7% | 31.4% |
| Hispanic | 26.0% | 28.9% | 28.7% | 29.0% | 29.8% |
| Asian (non-Hispanic) | 4.3% | 5.4% | 5.7% | 6.2% | 6.9% |
| Two or More Races | 1.6% | 1.3% | 1.3% | 1.7% | 2.6% |
| Amer. Indian/Alaska Native | 0.1% | 0.2% | 0.1% | 0.1% | 0.1% |
| Some Other Race | 0.1% | 0.2% | 0.2% | 0.2% | 0.4% |
| Nat. Hawaiian/Pacific Islander | 0.03% | 0.02% | 0.02% | 0.02% | 0.02% |
| Total Population | 2,896,016 | 2,695,598 | 2,706,101 | 2,716,450 | 2,746,424 |

Notable here is the slight growth in share for both the Hispanic and Asian populations - and the sizeable decrease of the Black share of the population, around a 3.7% decrease since the 2010 census. While neighborhood and community demographic makeup in the city continues to change, Chicago remains highly segregated, with 287 voting precincts being more than 80% Black and 116 being more than 80% Hispanic. At the time time, 805 precincts are less than 20% Black, and 744 are less than 20% Hispanic.

| Precincts (1,291 total) | 0-20% | 20-40% | 40-60% | 60-80% | 80-100% |
|---|---|---|---|---|---|
| White | 588 | 165 | 211 | 269 | 57 |
| Black | 805 | 73 | 55 | 70 | 287 |
| Hispanic | 744 | 201 | 134 | 95 | 116 |
| Asian | 1,187 | 85 | 11 | 4 | 3 |

--- 
*[1] The Chicago Board of Elections cites that efficiency concerns, along with the continued popularity of mail-in ballots following the COVID-19 pandemic, are the primary driver in decreasing the overall number of precincts - and therefore polling places. We believe this is important to note, as polling place availability and accessibility (or lackthereof) is a known historical determinant in electoral disenfranchisement and representation.*

## 3. Methodology

### 3.1 Districting Plan Ensembles

To generate a sufficient number of distinct districting plans, we use GerryChain to run a 10,000-step ReCom chain and subsample 50 plans that will be used in our election simulations. We do this a total of four times - once for each of the following configurations:

- 50 x 1 ensemble - each plan has 50 single-member districts built from precincts
- 10 x *$m$* ensemble - each plan has 10 multi-member districts built from precincts
- 50 x 1 optimized ensemble - each plan has 50 single-member districts built from precincts, but we attempt to sample plans that have a higher number of districts with an Asian population over 20%. 
- 10 x *$m$* optimized ensemble - each plan has 10 multi-member districts built from precincts, but we attempt to sample plans that have a higher rnumber of districts with an Asian population over 15%. 

Given that Asian voters have been consistently underpresented within Chicago's city council, a goal of the report is to understand the conditions under which proportional representation could be achieved - including intentional redistricting to optimize for more wards with larger Asian populations. We accomplish this by using the Gingleator optimizer within the GerryChain library. The optimizer will perform "short bursts" of 100 steps to identify and select a plan according to a provided scoring function and target population threshold. We use the default score function that simply keeps track of plans with the highest number of districts that meet our threshold criteria. In the case of 50 district plans, we set that threshold at 20%, and in 10 district plans we set it at 15%. 

### 3.2 Voter Blocs and Candidate Slates

Mirroring the 2019 report, we consider the four largest racial demographic groups when identifying blocs of voters with shared preferences and slates of candidates with similar policies and positions. Slates are limited to and delineated by Black, Asian, Hispanic, and White candidates. Voter blocs follow this with a major exception: we made the decision to combine White and Asian voters into a single bloc. The reasoning for this is guided by evidence that Asian and White voters in Chicago demonstrate similar behaviors and preferences at the ballot box, particularly when looking at the last several mayoral and city council elections. While there currently exists no expansive data sets or analysis examining Asian voting behavior in Chicago elections, a handful of examples do exist: MGGG's previous application of Goodman's Ecological Regression on [add source here] and [Greater Cities Institute's](https://uofi.app.box.com/s/g2wlv9836atormomsn64alse2ysapjrd) application of ecological inference on voters in the 2023 mayoral election. Considering the similarities in behavior between the two blocs, we model them as a single bloc for the purposes of this analysis.

#### Cohesion matrices

Rows are voter blocs, columns are candidate slates; each row sums to 1.

**Standard 3-bloc** (`basic`, `low-poc-turnout`, `10x3-stv`, `10x5-stv`):

| bloc ↓ / slate → | White | Black | Hispanic | Asian |
|---|---|---|---|---|
| **White-Asian** | 0.46 | 0.14 | 0.14 | 0.26 |
| **Black** | 0.15 | 0.70 | 0.10 | 0.05 |
| **Hispanic** | 0.15 | 0.10 | 0.65 | 0.10 |

The `White-Asian` row is the VAP-weighted average (81% White / 19% Asian) of the
underlying White and Asian rows (below).

**Asian Bloc Separate** (`asian-seperate-bloc`):

| bloc ↓ / slate → | W | B | H | A |
|---|---|---|---|---|
| **W** | 0.50 | 0.15 | 0.15 | 0.20 |
| **A** | 0.30 | 0.10 | 0.10 | 0.50 |
| **B** | 0.15 | 0.70 | 0.10 | 0.05 |
| **H** | 0.15 | 0.10 | 0.65 | 0.10 |

The cohesion parameters here have been selected based on available estimates (from ecological regression) from the earlier 2019 MGGG study on Chicago city council reform, as well as a mayoral election analysis from the University of Illinois-Chicago. The details can be found in the accompanying writeup.

### 3.3 Candidate Availability and Pool Size

To simulate candidate availability per-ward for each voting bloc, we make a few decisions and assumptions guided by available evidence from previous Chicago general city council elections. The first is deciding how many total candidates will be on the ballot for a given ward. In 2023, the average number of candidates running across all 50 wards (not counting write-ins) was approximately 3.48, with a large number of wards featuring anywhere between one to four candidates in the election, and fewer featuring counts larger than five.

![2023 Candidate Counts by Ward](../assets/candidates-by-ward.png)

To model this in our simulation, we sample from the geometric distribution. For 50 district configurations, we use a probability value of $0.2$, which provides an expected value of $5$ - slightly above the average total candidates running per district in 2023. For 10 district configurations, we use a probability value of $0.1$, which provides an expected value of $10$. Generally, we'd expected plans with larger districts to see more candidates pursuing seats on council. We sample in this way for every single district in every subsample of the districting ensemble, allowing some variance in total available candidates across districts and plans. However, because generating values in this way could result in a candidate pool size that is large enough to be unrealistic (and computationally expensive,) we set a cap on the total candidates by making a calculation with the district VAP:

$$Max\ Candidates = \lceil log_{10}(District\ VAP) \rceil$$

The application of the logarithmic function with each district's VAP as input serves to mirror the maximum observed candidates in the 2023 general election - 11 candidates in both Ward 5 and Ward 6. Since each ward in the existing 50 district maps contains an average of 44,000 constituents in the voting age population, this calculation will result in 11 total candidates. Using the same formula, maps with 10 districts will be expected to see a cap of 13 candidates - the reasoning here that larger districts with more seats available would see a larger candidate pool with the size limited by the willingness or ability of would-be candidates to persist their campaigns until election day. Our "floor" minimum value is trival by comparison - here we set a minimum value *$m$* that is equal to the number of seats per district, so we never allow a sampled number to be lower than the per-district seats.

Next, we make an assumption that the racial composition of the slate pool will be roughly proportional to that of the VAP in each district. Using the bloc proportions to create an interval with the intent to sample candidates of different slates from this interval. However, before we do we first square each element, normalizing the "squared interval" over the sum of the squared values. This creates an "exaggeration" effect when we sample slate candidates. In other words, if a district has a large Black VAP, it's even more likely that the Black voter-preferred slate of candidates will be larger than the others. Similarly, if the Asian VAP is small it's much less likely that the Asian voter-preferred slate will have many candidates - if any, since we allow for slates to be empty. This is intended to model how community dynamics, segregation, or lack of institutional support may impact candidate availability across geography with respect to race.

### 3.4 Voter Profile and Ballot Generation


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

- **Voter models:** `slate_pl` (Plackett-Luce) and `slate_bt` (Bradley-Terry).
- **Voters per district:** 10,000 · 
- **Alphas (Dirichlet):** all 1 (uniform within-slate preferences).
- **Tiebreak:** random




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
