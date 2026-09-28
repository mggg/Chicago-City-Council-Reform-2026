# Revisiting Reform Proposals for Chicago City Council

Code, configurations, and report for a 2026 update of MGGG's 2019 study of
alternative electoral systems for the Chicago City Council. The pipeline builds
precinct-level demographic data from the 2020 Census, samples ensembles of
districting plans with [GerryChain](https://github.com/mggg/GerryChain),
simulates ranked-choice ballots and elections with
[VoteKit](https://github.com/mggg/VoteKit), and summarizes seat outcomes by
racial slate.

- **Report:** [`report/report.pdf`](report/report.pdf) (source:
  [`report/report.md`](report/report.md)); web version:
  [`index.html`](index.html)
- **2019 study:** [github.com/mggg/chicago](https://github.com/mggg/chicago)

## Repository layout

| Path | Contents |
|---|---|
| `run.py` | Main entry point: builds the data, then runs the pipeline for every config in `configs/` and the cross-run summaries. |
| `main.py`, `setup.py` | Alternative entry point: pick (or build) a single config interactively and run the pipeline for it. |
| `pipeline/` | Pipeline stages (see [Pipeline stages](#pipeline-stages)) and shared helpers in `pipeline/utils/`. |
| `configs/` | One JSON config per simulation run in the report. |
| `pipeline-config/` | Browser-based config builder ([instructions](pipeline-config/instructions.md)). |
| `data/` | The Chicago precinct shapefile (committed) plus Census downloads and derived files (generated, gitignored). |
| `outputs/` | All pipeline outputs (generated, gitignored). |
| `figures/` | The subset of pipeline figures used in the report. |
| `assets/` | Static images used in the report and documentation. |
| `notebooks/` | The 2019 comparison figure and diagnostics of Asian-candidate support. |
| `documentation/` | Reference notes on the district generator, voting rules, and cohesion parameters. |
| `report/` | Report source, stylesheet, and PDF. |

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/mggg/Chicago-City-Council-Reform-2026.git
cd Chicago-City-Council-Reform-2026
uv sync
```

The data step downloads from the Census API, which needs a free API key
([sign up here](https://api.census.gov/data/key_signup.html)). Put it in a
`.env` file in the repository root:

```
CENSUS_API_KEY=your-key-here
```

`.env` is gitignored, so your key is never committed. The pipeline reads the
key on startup even when the Census downloads are already cached in `data/`,
so every run needs it.

## Data

`pipeline/data_generator.py` builds `data/chicago_precincts_vap_cvap.gpkg`, the
geodata file every config points to:

1. **Precincts:** `data/chicago-precincts.shp` (committed), the Chicago voting
   precincts with ward assignments.
2. **Blocks:** 2020 TIGER/Line block geometries for Cook and DuPage counties.
3. **Voting-age population:** 2020 Decennial PL 94-171 tables P1, P3, and P4 at
   the block level.
4. **Citizenship:** ACS 5-year (2024) table B05003 at the tract level, used to
   estimate block-level CVAP.

Each block is assigned to the precinct containing its interior point, and block
VAP/CVAP is summed up to precincts. Downloads are cached in `data/`, so later
runs skip the Census API calls.

## Running the pipeline

```bash
uv run python run.py
```

This builds the data (if not already cached), then runs every config in
`configs/`. After that it draws the cross-run comparison figure and the
district demographic exports. Each run writes to `outputs/<run_name>/`.
District ensembles are shared between runs with the same districting settings
and are written to `outputs/districts/`. The pipeline checks what already
exists and resumes from the first incomplete stage, so an interrupted run can
be restarted with the same command.

To run a single config interactively instead:

```bash
uv run python main.py
```

Answer `y` at the first prompt and give the path to a config file. The
interactive option to build a new config from scratch doesn't yet ask for every
field the pipeline needs (`blocs`, `voting_configs`, `voter_models`,
`epsilon`, `population_vap_column`, `candidate_geometric_p`). To create a new
config, use the config builder instead:

```bash
uv run python pipeline-config/server.py   # then open http://localhost:8000
```

### Pipeline stages

| Stage | Module | Output |
|---|---|---|
| Data | `pipeline/data_generator.py` | `data/chicago_precincts_vap_cvap.gpkg` |
| District ensemble | `pipeline/district_generator.py` | `outputs/districts/chain_out/<n>/` |
| District settings | `pipeline/settings_generator.py` | `outputs/<run>/settings/` |
| Ballot profiles | `pipeline/profile_generator.py` | `outputs/<run>/profiles.zip` |
| Elections | `pipeline/simulate_elections.py` | `outputs/<run>/election_results/` |
| Summaries & figures | `pipeline/summarize_results.py` | `outputs/<run>/summaries/` |

### Configs and report sections

| Config | Run name | Report section |
|---|---|---|
| `configs/10x5-stv.json` | 10 X 5 STV | 4.1 |
| `configs/10x3-stv.json` | 10 X 3 STV | 4.1 |
| `configs/basic.json` | 50 X 1 Plurality | 4.2 |
| `configs/50-irv.json` | 50 X 1 IRV | 4.2 |
| `configs/low-poc-turnout.json` | Low POC Turnout | 4.3 |
| `configs/asian_optimized.json` | 10 X 5 STV - Larger Asian Districts | 4.4 |
| `configs/50-irv-asian-optimized.json` | 50 X 1 IRV - Larger Asian Districts | 4.4 |
| `configs/50-psmd-asian-optimized.json` | 50 X 1 PSMD - Larger Asian Districts | 4.4 |
| `configs/asian-seperate-bloc.json` | 10 X 5 STV - Asian Bloc Separate | Not shown in the report |

Config fields are described in
[`documentation/district-generator-reference.md`](documentation/district-generator-reference.md),
[`documentation/voting-rule-reference.md`](documentation/voting-rule-reference.md),
and [`documentation/cohesion-parameters.md`](documentation/cohesion-parameters.md).

## Notebooks

### 2019 comparison

[`notebooks/mggg_2019_comparison_10x5.ipynb`](notebooks/mggg_2019_comparison_10x5.ipynb)
produces Figure 2 of the report (`figures/comparison.png`). It needs the
`10 X 5 STV` run outputs and a checkout of the 2019 study next to this repo:

```bash
git clone https://github.com/mggg/chicago.git ../chicago
uv run --with jupyterlab jupyter lab notebooks/mggg_2019_comparison_10x5.ipynb
```

Set `MGGG_CHICAGO_REPO` to use a checkout somewhere else.

### Asian-candidate diagnostics

[`notebooks/diagnostics.ipynb`](notebooks/diagnostics.ipynb) looks at the five
sampled districts with the highest Asian VAP share in the `10 X 5 STV` run. For
each one it counts how often ballots ranked the Asian-slate candidates 1st, 2nd,
and 3rd, then regenerates the ballots bloc by bloc to show which voter blocs
that support comes from. It needs the `10 X 5 STV` run outputs, including
`outputs/cross_run_summaries/10 X 5 STV_district_demographics.csv`.

## Building the report

The report is written in Markdown at `report/report.md`. Its images point at
`../figures/` and `../assets/`.

1. **Update figures.** After re-running the pipeline, copy each figure the
   report uses from `outputs/<run_name>/summaries/figures/` (and
   `outputs/cross_run_summaries/figures/`) into the matching folder under `figures/`.
   Regenerate `figures/comparison.png` with the notebook above.
2. **Export the PDF.** Open the repo in VS Code with the
   [Markdown PDF](https://marketplace.visualstudio.com/items?itemName=yzane.markdown-pdf)
   extension installed. Open `report/report.md` and run **Markdown PDF: Export
   (pdf)** from the command palette. The committed `.vscode/settings.json`
   applies `report/report.css` (MGGG article styling), Letter paper,
   1.25-inch margins, and page numbers. The extension writes `report.pdf` next
   to the Markdown file. It needs a network connection to load the web fonts.
3. **Update the web version.** `index.html` is a standalone page (Bootstrap +
   MathJax) maintained by hand. Copy any text or figure changes from
   `report.md` into it.
