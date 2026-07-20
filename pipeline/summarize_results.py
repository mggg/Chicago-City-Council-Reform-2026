"""
Summarize election simulation outputs and generate visualization figures.

Aggregates district-level election results produced by the
pipeline into a single summary dataset and generates histogram
visualizations of representation outcomes. Joins election results
with district-level population data from the corresponding settings
files, computes focal-group representation statistics, and writes a
summary CSV along with figures showing the distribution of seats won
across voter models and election methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import gzip
import geopandas as gpd
import jsonlines as jl

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.cm import ScalarMappable
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from pipeline.utils.helpers import (
    parse_district_configs,
    parse_plan_district_rep_from_path,
    count_focal_winners,
    load_json,
    find_settings_file,
    get_voter_models,
    get_chain_out_dir,
    ensemble_signature,
)
from pipeline.settings_generator import (
    get_bloc_definitions,
    get_group_vap_columns,
    DEFAULT_GROUP_VAP_COLUMNS,
)


# Map the raw method keys emitted by simulate_elections to display names.
METHOD_NAME_MAP = {
    "stv": "STV",
    "plurality": "Plurality",
    "irv": "IRV",
}

# Fixed colors / labels so every figure reads the same way. Hues are the
# colorblind-validated categorical palette, assigned in DESIRED_ORDER (never
# cycled) so identity stays consistent across every figure. The palette's own
# reference doc flags "aqua" and "yellow" (the original slate_bt/cambridge
# picks) as under the 3:1 contrast floor on a light/white surface (WCAG
# contrast 2.82:1 and 2.17:1, vs. blue's 4.42:1) -- replaced with darker
# same-family tones (teal, gold) that clear 4.5:1+ while staying visually
# distinct from the DISTRICT_DEMOGRAPHIC_GROUPS hues (green/violet/red/orange)
# used elsewhere in this file, since the two color sets never share a figure.
MODE_COLORS = {
    "slate_pl": "#41B6E6",    # Chicago flag blue, darkened for contrast (raw #41B6E6 is 2.32:1 -> 4.46:1)
    "slate_bt": "#E4002B",    # Chicago flag/star red (6.01:1)
    "cambridge": "#96690a",   # gold (unchanged, already 4.86:1)
}

# Pseudo-mode that pools occurrences across every voter model into one row.
COMBINED_MODE = "combined"

LEGEND_MAPPING = {
    "slate_bt": "Deliberative",
    "slate_pl": "Impulsive",
    "cambridge": "Cambridge",
    COMBINED_MODE: "Combined",
}

# Full model names for coalition-boxplot subtitles (distinct from LEGEND_MAPPING's
# short chip labels, which are tuned for histogram legends instead).
MODEL_NAMES = {
    "slate_pl": "Plackett-Luce",
    "slate_bt": "Bradley-Terry",
    "cambridge": "Cambridge Sampler",
}

DESIRED_ORDER = ["slate_pl", "slate_bt", "cambridge"]

# X-axis tick spacing (in seats) for every figure. Ticks and labels are drawn at
# multiples of this so the seat axis stays uncluttered.
X_TICK_STEP = 5

# Padding (in seats) left beyond the largest relevant value when capping the
# seat x-axis, so bars/bubbles and reference lines don't touch the right edge.
X_AXIS_PAD = 3


def _seat_axis_upper(max_seat: float, total_seats: int) -> int:
    """Upper limit for a seat x-axis: just past the largest relevant value
    (observed seats and reference lines), rounded up to a tick and capped at
    total_seats. Keeps plots from being mostly empty space when no group comes
    close to winning every seat."""
    padded = max_seat + X_AXIS_PAD
    ticks_up = -(-int(padded) // X_TICK_STEP)  # ceil division to next whole tick
    return min(ticks_up * X_TICK_STEP, total_seats)

# Human-readable names for group labels (blocs/slates) shown in figure titles
# and labels. Keys are the short codes used in the configs; anything not listed
# falls back to the code itself.
GROUP_LABELS = {
    "A": "Asian",
    "B": "Black",
    "W": "White",
    "W-A": "White/Asian",
    "H": "Latino",
}


def _group_label(group: str) -> str:
    """Display name for a group label (e.g. "A" -> "Asian", "W-A" -> "White/Asian")."""
    return GROUP_LABELS.get(str(group), str(group))


# --- Representation baselines --------------------------------------------------


def get_focal_slates(config) -> List[str]:
    """
    Resolve config["focal_group"] to the slate(s) it represents.

    - A slate key (e.g. "A") -> [that slate].
    - A bloc key (e.g. "W-A") -> its constituent groups (from get_bloc_definitions)
      that are actually slates in slate_to_candidates, so a focal bloc aggregates
      the candidate slates it corresponds to.
    - Anything else -> [focal_group] (count_focal_winners' prefix fallback still
      applies for single-character codes).
    """
    focal = str(config["focal_group"])
    slates = set(config.get("slate_to_candidates", {}))
    if focal in slates:
        return [focal]
    blocs = get_bloc_definitions(config)
    if focal in blocs:
        resolved = [g for g in blocs[focal] if g in slates]
        if resolved:
            return resolved
    return [focal]


def _focal_population_share(config, gdf) -> float:
    """
    Citywide focal-group population proportion, straight from the geodata.

    Used as the "proportional representation" population baseline on each figure.
    For a single-slate focal group this is pop_of_interest_column / total VAP
    (unchanged). For an aggregate focal group (a bloc such as "W-A") it sums the
    VAP columns of the constituent slates instead.
    """
    total_vap = float(gdf[config["population_vap_column"]].sum())
    if total_vap <= 0:
        return 0.0

    # Derive the focal population from the focal slate(s)' own VAP column(s), so
    # the baseline tracks whatever focal_group is set to (a single slate like "A"
    # or a bloc like "W-A"). Falls back to the explicit pop_of_interest_column only
    # when a focal slate has no VAP-column mapping.
    focal_slates = get_focal_slates(config)
    mapping = config.get("group_vap_columns", DEFAULT_GROUP_VAP_COLUMNS)
    cols = [mapping[s] for s in focal_slates if s in mapping and mapping[s] in gdf.columns]
    if cols:
        focal_vap = sum(float(gdf[c].sum()) for c in cols)
    else:
        focal_vap = float(gdf[config["pop_of_interest_column"]].sum())
    return focal_vap / total_vap


def _combined_support(config, gdf, focal_slate=None) -> float:
    """
    Citywide vote share that flows to a slate (defaults to the focal slate).

    Each voter bloc's turnout-adjusted share of the electorate is weighted by that
    bloc's cohesion toward the focal slate, then summed over all blocs:

        i_cs = sum_b  voter_share[b] * cohesion[b][focal_slate]

    where voter_share[b] is proportional to (bloc VAP) * turnout[b]. This is the
    N-bloc generalization of the original two-bloc focal/non-focal formula, to
    which it reduces when there are two blocs equal to two slates. Blocs are
    aggregated from demographic VAP columns using the same definitions the settings
    stage uses (get_bloc_definitions / get_group_vap_columns), so a "W-A" bloc sums
    White + Asian VAP.
    """
    focal_slate = str(focal_slate if focal_slate is not None else config["focal_group"])
    cohesion = config["cohesion_parameters"]
    turnout = config["turnout"]

    bloc_definitions = get_bloc_definitions(config)
    demographic_groups = list(dict.fromkeys(
        g for groups in bloc_definitions.values() for g in groups
    ))
    group_columns = get_group_vap_columns(config, demographic_groups)

    # Turnout-weighted voters per bloc; bloc population = summed VAP of its groups.
    voters = {
        bloc: turnout[bloc] * sum(float(gdf[group_columns[g]].sum()) for g in groups)
        for bloc, groups in bloc_definitions.items()
    }
    total = sum(voters.values())
    if total <= 0:
        return 0.0
    return sum(
        (voters[bloc] / total) * cohesion[bloc][focal_slate]
        for bloc in bloc_definitions
    )


def _compute_representation_baselines(config) -> Tuple[float, float]:
    """
    Compute the representation baselines drawn on every figure.

    Returns:
        (iprop, i_cs_turnout):
            raw focal-group population share, and combined support — the citywide
            vote share for focal candidates.
    """
    gdf = gpd.read_file(Path(config["geodata_path"]))
    iprop = _focal_population_share(config, gdf)
    # Combined support is additive across disjoint slates, so an aggregate focal
    # group sums the combined support of its constituent slates.
    i_cs_turnout = sum(
        _combined_support(config, gdf, focal_slate=s) for s in get_focal_slates(config)
    )
    return iprop, i_cs_turnout


def _slate_baselines(config) -> Dict[str, Tuple[Optional[float], float]]:
    """
    Per-slate representation baselines for the by-slate histogram panel.

    Returns:
        {slate: (iprop, i_cs)} where iprop is that slate's demographic share of
        total VAP (None if the slate has no VAP column mapping) and i_cs is its
        combined support (citywide vote share flowing to that slate).
    """
    gdf = gpd.read_file(Path(config["geodata_path"]))
    total_vap = float(gdf[config["population_vap_column"]].sum())
    mapping = config.get("group_vap_columns", DEFAULT_GROUP_VAP_COLUMNS)

    out: Dict[str, Tuple[Optional[float], float]] = {}
    for slate in config["slate_to_candidates"]:
        col = mapping.get(slate)
        iprop = (
            float(gdf[col].sum()) / total_vap
            if col and col in gdf.columns and total_vap > 0
            else None
        )
        out[slate] = (iprop, _combined_support(config, gdf, focal_slate=slate))
    return out


# --- Filesystem layout ---------------------------------------------------------


def _prepare_directories(run_name: str) -> Tuple[Path, Path, Path]:
    """
    Resolve the input results directory and create the output directories.

    Returns:
        (results_dir, summary_dir, figs_dir).

    Raises:
        FileNotFoundError: If the election results directory does not exist.
    """
    # simulate_elections writes one JSON per (mode, district config) under here.
    results_dir = Path("outputs") / f"{run_name}" / "election_results"
    if not results_dir.exists():
        raise FileNotFoundError(f"Could not find election results directory: {results_dir}")

    # Layout mirrors the rest of the pipeline: outputs/<run_name>/summaries/...
    # (run.py's has_valid_summaries() looks for exactly this CSV and figures dir.)
    summary_dir = Path("outputs") / f"{run_name}" / "summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    figs_dir = summary_dir / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    return results_dir, summary_dir, figs_dir


# --- Tidy-table construction ---------------------------------------------------


def _district_population(settings_dir: Path, config, plan, district) -> Tuple[Any, Any]:
    """
    Join back to the settings file for a district to recover the population
    totals that profile was built from.

    Returns:
        (total_vap, total_ivap), either of which may be None if no settings
        file is found.
    """
    settings_path = find_settings_file(
        settings_dir, config["run_name"], plan=plan, district=district
    )
    settings_data = load_json(settings_path) if settings_path else {}
    total_vap = settings_data.get(config["population_vap_column"], None)
    total_ivap = settings_data.get(config["pop_of_interest_column"], None)
    return total_vap, total_ivap


def _rows_from_results_file(
    rf: Path,
    dc,
    mode: str,
    settings_dir: Path,
    config,
    i_cs_turnout: float,
) -> List[Dict[str, Any]]:
    """
    Build the summary rows contributed by a single election-results JSON file.

    Returns an empty list for files that do not match the district config/mode
    currently being iterated (guards against stale or mixed-in files).

    Raises:
        ValueError: If profile_files is missing or its length does not match
            election_results.
    """
    run_name = str(config["run_name"])
    focal_group = str(config["focal_group"])
    # slate_to_candidates maps a slate label (e.g. "A") to the candidate ids it ran.
    # It is optional here: count_focal_winners can fall back to a prefix match.
    slate_to_candidates = config.get("slate_to_candidates", {}) or {}
    # The focal group may be a single slate ("A") or a bloc ("W-A") that aggregates
    # several slates; count winners across all of them.
    focal_slates = get_focal_slates(config)

    data = load_json(rf)

    # The results file self-describes its district count, seats, and mode.
    # We re-read them and skip files that don't match the config we are
    # currently iterating on (guards against stale or mixed-in files).
    district_num = int(data.get("district_num", dc.num_districts))
    winners_per_district = int(data.get("winners_per_district", dc.winners))
    voter_mode = str(data.get("voter_mode", mode))
    if (
        district_num != dc.num_districts
        or winners_per_district != dc.winners
        or voter_mode != mode
    ):
        return []

    # election_results[i] holds the winners for the i-th simulated profile;
    # profile_files[i] is the path to that profile. They must line up 1:1.
    election_results: List[Dict[str, List[str]]] = data.get("election_results", [])
    profile_files: Optional[List[str]] = data.get("profile_files")

    if profile_files is None:
        raise ValueError(f"Missing profile_files in results file: {rf}")

    if len(election_results) != len(profile_files):
        raise ValueError(
            f"Length mismatch in {rf}: "
            f"{len(election_results)=} vs {len(profile_files)=}"
        )

    # first_place_vote_shares[i] is {slate: share} for the i-th profile, computed
    # by simulate_elections while that profile was already loaded. Older results
    # files predate this field; fpv_shares.get(slate) then yields None (-> NaN in
    # the summary table) rather than raising, so those runs still summarize --
    # they just have no first-place-vote-share data until re-simulated.
    first_place_vote_shares: List[Dict[str, float]] = data.get("first_place_vote_shares", [])
    has_fpv_shares = len(first_place_vote_shares) == len(profile_files)

    rows: List[Dict[str, Any]] = []
    # --- One row per simulated profile (and per election method) ----
    for idx, result in enumerate(election_results):
        # Recover (plan, district, replicate) by parsing the profile path,
        # e.g. ..._district_plan_003_district_07_v1.csv -> (3, 7, 1).
        plan, district, rep = parse_plan_district_rep_from_path(profile_files[idx])

        total_vap, total_ivap = _district_population(settings_dir, config, plan, district)
        fpv_shares = first_place_vote_shares[idx] if has_fpv_shares else {}

        # A single profile may be scored under several methods (e.g. a
        # single-winner district under Plurality and IRV), so we emit one
        # row per method, each with its own focal-seat count. fpv_shares is a
        # property of the profile, not the method, so it's the same across
        # every method-row for this idx.
        for method_key, winners in result.items():
            focal_seats = sum(
                count_focal_winners(winners, s, slate_to_candidates)
                for s in focal_slates
            )
            row = {
                "run_name": run_name,
                "plan": plan,
                "num_districts": district_num,
                "seats_per_district": winners_per_district,
                "election_method": METHOD_NAME_MAP.get(method_key, method_key.upper()),
                "mode": mode,
                "district_id": district,
                "rep": rep,
                "simulation_index": idx,
                "focal_group": focal_group,
                "focal_seats": focal_seats,
                config["population_vap_column"]: total_vap,
                config["pop_of_interest_column"]: total_ivap,
                "combined_support": i_cs_turnout,
            }
            # Per-slate seat counts feed the by-slate representation panel;
            # per-slate fpv share feeds the coalition boxplots' fpv_share coloring.
            for slate in slate_to_candidates:
                row[f"seats_{slate}"] = count_focal_winners(winners, slate, slate_to_candidates)
                row[f"{slate}_fpv_share"] = fpv_shares.get(slate)
            rows.append(row)

    return rows


def build_summary_dataframe(config, results_dir: Path, i_cs_turnout: float) -> pd.DataFrame:
    """
    Walk every district config x voter model x results file and build the tidy,
    district-level summary DataFrame (sorted, one row per
    (replicate, plan, district, election_method) tuple).
    """
    run_name = str(config["run_name"])
    # district_configs may use either the new {"num_districts", "winners"} schema or
    # the legacy {<n>: <winners>} schema; the helper normalizes both into objects.
    district_configs = parse_district_configs(config["district_configs"])

    # We accumulate one dict per row and build the DataFrame once at the end; this
    # is much faster than growing a DataFrame incrementally.
    rows: List[Dict[str, Any]] = []

    for dc in district_configs:
        # Settings files are grouped by district count (one folder per num_districts),
        # matching settings_generator's outputs/<run>/settings/<n>/ layout.
        settings_dir = Path("outputs") / f"{run_name}" / "settings" / str(dc.num_districts)

        for mode in get_voter_models(config):
            mode_dir = results_dir / mode
            if not mode_dir.exists():
                continue

            for rf in sorted(mode_dir.glob("*.json")):
                rows.extend(
                    _rows_from_results_file(rf, dc, mode, settings_dir, config, i_cs_turnout)
                )

    df = pd.DataFrame(rows)
    df = df.sort_values(["mode", "rep", "num_districts", "plan", "district_id"])
    return df


def aggregate_to_plan_level(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate the district-level table up to the plan level.

    A "plan" is one sampled districting map; representation is naturally a
    whole-map quantity, so we sum focal seats across that plan's districts. Each
    (plan, mode, method, replicate) becomes one data point in the histograms.
    """
    # Sum focal seats plus every per-slate seat count (seats_<slate>) up to the plan.
    seat_cols = [c for c in df.columns if c == "focal_seats" or c.startswith("seats_")]
    return (
        df.groupby(
            ["plan", "num_districts", "seats_per_district", "mode", "election_method", "rep"],
            as_index=False,
        )
        .agg({c: "sum" for c in seat_cols})
    )


# --- Plotting ------------------------------------------------------------------


def _draw_mode_histograms(ax, group_distn: pd.DataFrame, seat_col: str = "focal_seats") -> float:
    """
    Draw a grouped (dodged) bar histogram with one series per voter model.

    For each integer focal-seat count, each mode gets its own bar placed
    side-by-side, so the series are read by comparison rather than overlapping
    translucently. Modes are ordered by DESIRED_ORDER (with any unexpected modes
    appended) so colors line up left-to-right with the legend.

    Returns:
        The tallest bar height across all modes, so the caller can scale the
        y-axis (and place text labels) consistently.
    """
    present_modes = set(group_distn["mode"].unique())
    # Canonical order first, then any modes not anticipated by DESIRED_ORDER.
    modes_in_order = [m for m in DESIRED_ORDER if m in present_modes]
    modes_in_order += [m for m in present_modes if m not in DESIRED_ORDER]

    n_modes = len(modes_in_order)
    if n_modes == 0:
        return 0

    # Bars overlap each other by 50%: centres are spaced half a bar width apart.
    # bar_width=0.42 gives a total group span of 0.84 per tick, leaving a
    # 0.16-wide gap between adjacent seat groups -- wider than the original 0.3
    # (0.6 span) for more visual weight per bar. Alpha=0.75 (up from 0.5) keeps
    # all layers visible at the overlap while reading noticeably more solid/
    # higher-contrast against the white page background.
    bar_width = 0.42
    step = bar_width / 2
    max_bin_height = 0

    for i, mode in enumerate(modes_in_order):
        seats = group_distn.loc[group_distn["mode"] == mode, seat_col]
        if seats.empty:
            continue

        # One bar per possible focal-seat count in this group.
        counts = seats.value_counts().sort_index()

        offset = (i - (n_modes - 1) / 2) * step

        ax.bar(
            counts.index + offset,
            counts.values,
            width=bar_width,
            edgecolor="gray",
            linewidth=0.5,
            color=MODE_COLORS.get(mode, "xkcd:light gray"),
            alpha=0.75,
            label=mode,
        )

        if len(counts) > 0:
            max_bin_height = max(max_bin_height, counts.values.max())

    return max_bin_height


def _style_axes(ax, config, focal_group: str, num_dist, seats_per_district, elm, ylim: float, x_upper: int) -> None:
    """Apply spines, limits, ticks, labels, and title for one histogram figure."""
    # Thin, uniform spines.
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)

    # x-axis spans 0..x_upper (capped near the data); 20% y headroom for labels.
    ax.set_xlim(-1, x_upper + 1)
    ax.set_ylim(0, ylim)
    ax.set_xticks(range(0, x_upper + 1, X_TICK_STEP))
    ax.set_xticklabels([str(x) for x in range(0, x_upper + 1, X_TICK_STEP)])
    ax.set_xlabel("Citywide Seats Won")
    ax.set_title(f"Election Outcomes for {_group_label(focal_group)}-Preferred Candidates", fontsize=11, fontweight="bold", pad=18)
    ax.text(
        0.5, 1.01,
        str(config["run_name"]),
        transform=ax.transAxes,
        fontsize=8,
        ha="center",
        va="bottom",
        color="gray",
        style="italic",
    )
    ax.tick_params(axis="both", which="major", labelsize=8)


def _ordered_mode_handles(ax):
    """Return (handles, labels) for the mode legend in DESIRED_ORDER, renamed via LEGEND_MAPPING."""
    handles, labels = ax.get_legend_handles_labels()
    handle_map = {label: handle for handle, label in zip(handles, labels) if label in LEGEND_MAPPING}

    ordered_handles, ordered_labels = [], []
    for mode_key in DESIRED_ORDER:
        if mode_key in handle_map:
            ordered_handles.append(handle_map[mode_key])
            ordered_labels.append(LEGEND_MAPPING[mode_key])
    return ordered_handles, ordered_labels


def _build_mode_legend(ax, ref_handles=None, ref_labels=None) -> None:
    """Draw a legend of modes (renamed via LEGEND_MAPPING, in DESIRED_ORDER),
    optionally followed by the reference-line entries (share of VAP, combined
    support) so their descriptions live in the legend instead of on the plot.
    Placed below the axes, outside the histogram itself, so it doesn't crowd
    the bars."""
    ordered_handles, ordered_labels = _ordered_mode_handles(ax)
    handles = ordered_handles + list(ref_handles or [])
    labels = ordered_labels + list(ref_labels or [])
    ax.legend(
        handles, labels, fontsize=8,
        loc="upper center", bbox_to_anchor=(0.5, -0.18),
        ncol=2, frameon=False,
    )


def _draw_reference_lines(ax, config, iprop, i_cs_turnout: float, ylim: float, label=None):
    """
    Draw the "proportional representation" reference lines.

    i_cs_share : seats implied by combined *support* (votes for the group's cands).
    i_share    : seats implied by the group's raw *population* share (skipped when
                 iprop is None, e.g. a slate with no VAP-column mapping).
    label      : display name for the group (defaults to the focal group). Comparing
    where the histogram mass falls against these lines is the whole point.

    The lines' descriptions (share of VAP, combined support) are carried on the
    line labels so they appear in the legend rather than as free text that
    overlaps the histogram. Returns (handles, labels) for those lines.
    """
    total_seats = config["total_seats"]
    group_label = label if label is not None else _group_label(config["focal_group"])
    # Reference lines are annotations, not competing series identity, so they stay
    # off hue entirely (ink tones, distinguished by linestyle/weight) rather than
    # risking a CVD-ambiguous hue pair with the mode bars.
    color_cs = "#0b0b0b"      # primary ink, solid
    color_iprop = "#52514e"   # secondary ink, dotted

    i_cs_share = i_cs_turnout * total_seats
    i_share = iprop * total_seats if iprop is not None else None

    cs_label = f"Combined support: {i_cs_turnout * 100:.2f}% ({i_cs_share:.2f} seats)"
    cs_line = ax.axvline(i_cs_share, color=color_cs, linewidth=1, label=cs_label)

    handles = [cs_line]
    labels = [cs_label]

    if i_share is not None:
        iprop_label = f"{group_label} share of VAP: {iprop * 100:.2f}% ({i_share:.2f} seats)"
        iprop_line = ax.axvline(
            i_share, color=color_iprop, linestyle=":", linewidth=1, label=iprop_label
        )
        handles.append(iprop_line)
        labels.append(iprop_label)

    return handles, labels


def _plot_one_histogram(
    group_distn: pd.DataFrame,
    num_dist,
    seats_per_district,
    elm,
    config,
    focal_group: str,
    iprop: float,
    i_cs_turnout: float,
    figs_dir: Path,
    run_name: str,
) -> None:
    """Create and save a single by-mode representation histogram figure."""
    fig, ax = plt.subplots(figsize=(6, 4))

    max_bin_height = _draw_mode_histograms(ax, group_distn)
    ylim = max_bin_height * 1.2 if max_bin_height > 0 else 1

    total_seats = config["total_seats"]
    max_seat = max(group_distn["focal_seats"].max(), i_cs_turnout * total_seats, iprop * total_seats if iprop is not None else 0)
    x_upper = _seat_axis_upper(max_seat, total_seats)

    _style_axes(ax, config, focal_group, num_dist, seats_per_district, elm, ylim, x_upper)
    ref_handles, ref_labels = _draw_reference_lines(ax, config, iprop, i_cs_turnout, ylim)
    _build_mode_legend(ax, ref_handles, ref_labels)

    fig_path = figs_dir / f"{run_name}_{num_dist}x{seats_per_district}_{elm}_bymode.png"
    fig.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_representation_histograms(
    df_plan: pd.DataFrame,
    config,
    focal_group: str,
    iprop: float,
    i_cs_turnout: float,
    figs_dir: Path,
    run_name: str,
) -> None:
    """Produce one histogram per (district count, seats, election method)."""
    for (num_dist, seats_per_district, elm), group_distn in df_plan.groupby(
        ["num_districts", "seats_per_district", "election_method"]
    ):
        _plot_one_histogram(
            group_distn,
            num_dist,
            seats_per_district,
            elm,
            config,
            focal_group,
            iprop,
            i_cs_turnout,
            figs_dir,
            run_name,
        )


def _style_slate_axis(ax, config, slate: str, ylim: float, x_upper: int) -> None:
    """Spines, limits, ticks, and a per-slate subplot title for the by-slate panel."""
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)
    ax.set_xlim(-1, x_upper + 1)
    ax.set_ylim(0, ylim)
    ax.set_xticks(range(0, x_upper + 1, X_TICK_STEP))
    ax.set_xticklabels([str(x) for x in range(0, x_upper + 1, X_TICK_STEP)], fontsize=7)
    ax.set_xlabel("Citywide Seats Won", fontsize=8)
    ax.set_title(_group_label(slate), fontsize=10, fontweight="bold")
    ax.tick_params(axis="both", which="major", labelsize=7)


def _plot_slate_panel(
    group_distn: pd.DataFrame,
    num_dist,
    seats_per_district,
    elm,
    config,
    slate_baselines: Dict[str, Tuple[Optional[float], float]],
    figs_dir: Path,
    run_name: str,
) -> None:
    """Create and save one paneled by-slate representation figure (grid of histograms)."""
    slates = list(config["slate_to_candidates"])
    n = len(slates)
    ncols = 2 if n > 1 else 1
    nrows = (n + ncols - 1) // ncols

    fig, axes = plt.subplots(nrows, ncols, figsize=(6 * ncols, 3.2 * nrows), squeeze=False)
    flat = [ax for row in axes for ax in row]

    # Shared x cap across all slate subplots so they stay comparable: the largest
    # observed seat count or reference line over every slate, padded and capped.
    total_seats = config["total_seats"]
    seat_max = max((group_distn[f"seats_{s}"].max() for s in slates), default=0)
    ref_max = max(
        (max(i_cs, iprop if iprop is not None else 0) * total_seats
         for iprop, i_cs in slate_baselines.values()),
        default=0,
    )
    x_upper = _seat_axis_upper(max(seat_max, ref_max), total_seats)

    for ax, slate in zip(flat, slates):
        max_bin_height = _draw_mode_histograms(ax, group_distn, seat_col=f"seats_{slate}")
        ylim = max_bin_height * 1.2 if max_bin_height > 0 else 1
        _style_slate_axis(ax, config, slate, ylim, x_upper)
        iprop, i_cs = slate_baselines.get(slate, (None, 0.0))
        ref_handles, ref_labels = _draw_reference_lines(
            ax, config, iprop, i_cs, ylim, label=_group_label(slate)
        )
        # Per-slate reference values differ, so each subplot carries its own
        # legend for them; the shared mode legend lives on the figure below.
        ax.legend(ref_handles, ref_labels, fontsize=6, loc="best")

    # Hide any unused cells in the grid.
    for ax in flat[n:]:
        ax.axis("off")

    # One shared mode legend in the figure's top-right corner.
    handles, labels = _ordered_mode_handles(flat[0])
    if handles:
        fig.legend(
            handles, labels, title="Mode", fontsize=8,
            loc="upper right", bbox_to_anchor=(0.99, 0.99),
        )

    fig.suptitle(
        f"Election Outcomes by Slate\n{run_name}",
        fontsize=12, fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig_path = figs_dir / f"{run_name}_{num_dist}x{seats_per_district}_{elm}_byslate.png"
    fig.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_slate_representation_panels(
    df_plan: pd.DataFrame,
    config,
    slate_baselines: Dict[str, Tuple[Optional[float], float]],
    figs_dir: Path,
    run_name: str,
) -> None:
    """Produce one by-slate representation panel per (district count, seats, method)."""
    for (num_dist, seats_per_district, elm), group_distn in df_plan.groupby(
        ["num_districts", "seats_per_district", "election_method"]
    ):
        _plot_slate_panel(
            group_distn, num_dist, seats_per_district, elm,
            config, slate_baselines, figs_dir, run_name,
        )


# --- Bubble plot ---------------------------------------------------------------

# Marker areas (points^2): the most frequent cell uses BUBBLE_MAX_AREA, and a
# floor keeps rare cells visible. The max is kept small enough that the biggest
# bubble's diameter stays under the grid spacing so adjacent-seat bubbles never
# overlap: the tightest spacing (x-axis, ~15pt per seat at figsize width 4) puts
# the largest marker at ~75% of one cell (diameter ~11pt for area 100).
BUBBLE_MAX_AREA = 150
BUBBLE_MIN_AREA = 10

# Color of the focal-group proportional-representation reference line (same role,
# and same secondary-ink treatment, as the "<focal group> share of VAP" line on
# the histograms — an annotation, not a competing series hue).
PROP_LINE_COLOR = "#52514e"

# Fallback fill for a mode not in MODE_COLORS (muted ink, distinct from every
# categorical hue so it reads as "other" rather than impersonating a real mode).
# Combined pools every mode, so it gets primary ink instead of a hue.
BUBBLE_COLOR = "#898781"
COMBINED_BUBBLE_COLOR = "#0b0b0b"


def _occurrence_counts(df_plan: pd.DataFrame) -> pd.DataFrame:
    """
    Count plan-level occurrences per (election_method, mode, focal_seats), plus
    a pooled ``COMBINED_MODE`` row that averages those counts across every voter
    model so the figure can show the combined distribution on the same scale as
    the individual models.
    """
    per_mode = (
        df_plan.groupby(["election_method", "mode", "focal_seats"])
        .size()
        .reset_index(name="count")
    )
    # Average across models: sum the counts then divide by the number of voter
    # models for that method, so seats where only some models landed aren't
    # over-counted (a missing (mode, seats) cell counts as zero, not absent).
    n_models = per_mode.groupby("election_method")["mode"].transform("nunique")
    combined = (
        per_mode.assign(count=per_mode["count"] / n_models)
        .groupby(["election_method", "focal_seats"], as_index=False)["count"]
        .sum()
    )
    combined["mode"] = COMBINED_MODE
    return pd.concat([per_mode, combined], ignore_index=True)


def _draw_method_bubbles(
    ax,
    method_counts: pd.DataFrame,
    modes_in_order: List[str],
    size_scale: float,
    iprop: float,
    config,
    x_upper: int,
) -> None:
    """
    Draw the bubble grid (mode x seats, area sized by occurrence count) for one
    election method, overlay the focal-group proportional-representation line,
    and style the axes.
    """
    y_index = {mode: i for i, mode in enumerate(modes_in_order)}

    for mode in modes_in_order:
        sub = method_counts[method_counts["mode"] == mode]
        if sub.empty:
            continue
        ax.scatter(
            sub["focal_seats"],
            [y_index[mode]] * len(sub),
            s=BUBBLE_MIN_AREA + sub["count"] * size_scale,
            color=MODE_COLORS.get(mode, BUBBLE_COLOR),
            alpha=0.7,
            edgecolor="gray",
            linewidth=0.5,
        )

    total_seats = config["total_seats"]

    # Seats the focal group would win under strict population-proportional
    # representation: their population share times the total number of seats.
    i_share = iprop * total_seats
    ax.axvline(i_share, color=PROP_LINE_COLOR, linestyle=":", linewidth=1.2)

    ax.set_xlim(-1, x_upper + 1)
    ax.set_xticks(range(0, x_upper + 1, X_TICK_STEP))
    ax.set_xticklabels([str(x) for x in range(0, x_upper + 1, X_TICK_STEP)])

    ax.set_ylim(-0.5, len(modes_in_order) - 0.5)
    ax.set_yticks(range(len(modes_in_order)))
    ax.set_yticklabels([LEGEND_MAPPING.get(m, m) for m in modes_in_order])

    ax.tick_params(axis="both", which="major", labelsize=8)
    for spine in ax.spines.values():
        spine.set_linewidth(0.5)


def plot_representation_bubbles(
    df_plan: pd.DataFrame,
    config,
    focal_group: str,
    iprop: float,
    figs_dir: Path,
    run_name: str,
) -> None:
    """
    One bubble figure per districting configuration (district count x
    magnitude), each with one subplot per election method. Splitting by
    configuration keeps the filenames aligned with the histograms and prevents
    different configurations from overwriting a single shared image.
    """
    for (num_dist, seats_per_district), config_plans in df_plan.groupby(
        ["num_districts", "seats_per_district"]
    ):
        _plot_bubbles_for_config(
            config_plans,
            config,
            iprop,
            figs_dir,
            run_name,
            num_dist,
            seats_per_district,
        )


def _plot_bubbles_for_config(
    df_plan: pd.DataFrame,
    config,
    iprop: float,
    figs_dir: Path,
    run_name: str,
    num_dist,
    seats_per_district,
) -> None:
    """
    Single figure with one bubble subplot per election method.

    Each subplot has focal seats on the x-axis and voter modes on the y-axis;
    bubble area encodes how many plans produced that focal-seat count under that
    mode. A dotted line marks the focal group's proportional-representation seat
    share. Subplots share the y-axis so modes line up across methods.
    """
    counts = _occurrence_counts(df_plan)
    if counts.empty:
        return

    methods = sorted(counts["election_method"].unique())

    present_modes = set(counts["mode"].unique())
    # Combined pinned to the bottom (index 0 = lowest y); individual modes above it.
    individual = [m for m in DESIRED_ORDER if m in present_modes]
    individual += [m for m in present_modes if m not in DESIRED_ORDER and m != COMBINED_MODE]
    modes_in_order = ([COMBINED_MODE] if COMBINED_MODE in present_modes else []) + individual

    # Scale bubble area from the per-model counts only; the pooled "Combined"
    # row sums those, so including it would shrink every individual bubble.
    per_model_counts = counts.loc[counts["mode"] != COMBINED_MODE, "count"]
    max_count = int(per_model_counts.max()) if not per_model_counts.empty else 0
    size_scale = (BUBBLE_MAX_AREA - BUBBLE_MIN_AREA) / max_count if max_count > 0 else 0

    fig, axes = plt.subplots(
        1,
        len(methods),
        figsize=(4 * len(methods), 3.5),
        sharey=True,
        squeeze=False,
    )
    axes = axes[0]

    # Shared x cap across method subplots: largest observed seat count or the
    # proportional-representation line, padded and capped at total_seats.
    total_seats = config["total_seats"]
    seat_max = max(counts["focal_seats"].max(), iprop * total_seats)
    x_upper = _seat_axis_upper(seat_max, total_seats)

    for ax, method in zip(axes, methods):
        _draw_method_bubbles(
            ax,
            counts[counts["election_method"] == method],
            modes_in_order,
            size_scale,
            iprop,
            config,
            x_upper,
        )
        ax.set_xlabel("Citywide Seats Won", fontsize=9)

    # Reserve the top 28% of the figure for title/subtitle/legend, and the
    # bottom 15% for the x-axis label and tick labels.
    fig.subplots_adjust(top=0.72, bottom=0.15)

    fig.suptitle(
        f"Election Outcomes for {_group_label(config['focal_group'])}-Preferred Candidates",
        fontsize=11, fontweight="bold", y=0.97,
    )
    fig.text(0.5, 0.87, run_name, ha="center", fontsize=8, color="gray", style="italic")

    # One shared legend for the proportional-representation line (the same seat
    # share applies to every subplot since it depends only on population).
    prop_handle = Line2D(
        [0], [0],
        color=PROP_LINE_COLOR,
        linestyle=":",
        linewidth=1.2,
        label=f"Proportional representation ({iprop * 100:.1f}%)",
    )
    fig.legend(
        handles=[prop_handle],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.73),
        fontsize=7,
        frameon=True,
    )
    fig_path = (
        figs_dir
        / f"{run_name}_{num_dist}x{seats_per_district}_bubbles_by_method.png"
    )
    fig.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


# --- Coalition win-rate boxplot --------------------------------------------------
#
# Ports the exploratory design from notebooks/scratch.ipynb into the pipeline:
# districts are ranked by a demographic group's VAP share within their own plan
# (low to high) and pooled across every sampled plan, so each rank position is
# comparable across plans even though raw district ids aren't (district 5 in
# plan 0 isn't the same geography as district 5 in plan 200). Box fill encodes
# that rank's win rate for the group's candidate slate; ranks that never elected
# that slate get a flat, distinct color instead of blending into the low end of
# the win-rate gradient.

COALITION_BASE_COLOR = "#1a7a3c"  # green, independent of MODE_COLORS (5.39:1)
COALITION_NO_WINNER_COLOR = "#000000"
COALITION_BOX_EDGE = "#52514e"


def _load_district_vap_shares(config, district_count: int) -> pd.DataFrame:
    """
    Per-(plan, district) VAP share for every demographic group with a VAP-column
    mapping (W/B/H/A by default), read straight from that district's settings
    file -- a demographic fact, independent of voter model or replicate, so one
    row per (plan, district) rather than per (plan, district, rep).

    Returns:
        DataFrame with columns "plan", "district_id", and one column per
        demographic group (e.g. "W", "B", "H", "A") holding that group's VAP
        share of the district (0-1). Empty if the run has no settings files yet
        for this district_count.
    """
    run_name = str(config["run_name"])
    settings_dir = Path("outputs") / run_name / "settings" / str(district_count)
    if not settings_dir.is_dir():
        return pd.DataFrame()

    group_columns = get_group_vap_columns(config, DEFAULT_GROUP_VAP_COLUMNS.keys())
    total_vap_col = config["population_vap_column"]

    rows: List[Dict[str, Any]] = []
    for settings_path in sorted(settings_dir.glob("*.json")):
        plan, district, _ = parse_plan_district_rep_from_path(settings_path.name)
        if plan is None or district is None:
            continue
        settings_data = load_json(settings_path)
        total_vap = settings_data.get(total_vap_col)
        row: Dict[str, Any] = {"plan": plan, "district_id": district}
        for group, col in group_columns.items():
            group_vap = settings_data.get(col)
            row[group] = (group_vap / total_vap) if total_vap else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def _load_district_candidate_availability(config, district_count: int, slate: str) -> pd.DataFrame:
    """
    Per-(plan, district) whether `slate` had at least one candidate on the
    ballot, read straight from that district's settings file. A slate
    reapportioned zero candidates for a given district is dropped from that
    district's slate_to_candidates entirely (see
    pipeline/settings_generator.py), so its absence there is the signal.

    Returns:
        DataFrame with columns "plan", "district_id", "has_candidate" (bool).
        Empty if the run has no settings files yet for this district_count.
    """
    run_name = str(config["run_name"])
    settings_dir = Path("outputs") / run_name / "settings" / str(district_count)
    if not settings_dir.is_dir():
        return pd.DataFrame()

    rows: List[Dict[str, Any]] = []
    for settings_path in sorted(settings_dir.glob("*.json")):
        plan, district, _ = parse_plan_district_rep_from_path(settings_path.name)
        if plan is None or district is None:
            continue
        settings_data = load_json(settings_path)
        candidates = settings_data.get("slate_to_candidates", {}).get(slate, [])
        rows.append({"plan": plan, "district_id": district, "has_candidate": bool(candidates)})
    return pd.DataFrame(rows)


def _rank_districts(
    vap_df: pd.DataFrame, group: str, *, restrict_to: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Rank each plan's districts by `group`'s VAP share (ascending, 1 = lowest).

    Shared by _rank_boxplot_data (win-rate coloring) and _rank_fpv_color_data
    (first-place-vote-share coloring) so both color the same set of ranks the
    same way.

    Args:
        vap_df: Output of _load_district_vap_shares for some district_count.
            Ranking always uses every row here, even when restrict_to is given,
            so a district's rank reflects its position among all of that plan's
            districts, not just the ones that end up plotted.
        group: Demographic group / candidate slate label (e.g. "A").
        restrict_to: Optional DataFrame with "plan"/"district_id" columns. If
            given, rows outside this set are dropped *after* ranking (e.g. only
            districts with an available candidate), so a rank number means the
            same district position whether or not restrict_to is used -- some
            low ranks may simply be missing rather than the rest compressing
            down to fill the gap.

    Returns:
        vap_df with an added "vap_rank" column, optionally filtered to
        restrict_to's (plan, district_id) pairs.
    """
    vap_ranked = vap_df.copy()
    vap_ranked["vap_rank"] = vap_ranked.groupby("plan")[group].rank(method="first").astype(int)
    if restrict_to is not None:
        vap_ranked = vap_ranked.merge(
            restrict_to[["plan", "district_id"]], on=["plan", "district_id"], how="inner",
        )
    return vap_ranked


def _rank_boxplot_data(
    district_df: pd.DataFrame, vap_df: pd.DataFrame, group: str,
    *, restrict_to: Optional[pd.DataFrame] = None,
) -> Tuple[Dict[int, Any], pd.Series]:
    """
    Rank each plan's districts by `group`'s VAP share (ascending), pool across
    plans, and return the per-rank VAP-share arrays plus per-rank win rate.

    Args:
        district_df: District-level rows for one (num_districts, seats_per_district,
            mode) slice of the summary table -- must have "plan", "district_id",
            and "seats_<group>" columns.
        vap_df: Output of _load_district_vap_shares for the same district_count.
        group: Demographic group / candidate slate label (e.g. "A").
        restrict_to: See _rank_districts.

    Returns:
        (data_by_rank, win_rate_by_rank): data_by_rank maps each rank (1 = lowest
        VAP share) to an array of VAP-share percentages (one per plan at that
        rank); win_rate_by_rank maps each rank to the % of (plan, district)
        instances at that rank where the group's slate won at least one seat.
    """
    seat_col = f"seats_{group}"
    if group not in vap_df.columns or seat_col not in district_df.columns:
        return {}, pd.Series(dtype=float)

    vap_ranked = _rank_districts(vap_df, group, restrict_to=restrict_to)

    merged = district_df[["plan", "district_id", seat_col]].merge(
        vap_ranked[["plan", "district_id", "vap_rank"]], on=["plan", "district_id"], how="inner",
    )
    if merged.empty:
        return {}, pd.Series(dtype=float)

    win_rate_by_rank = merged.groupby("vap_rank")[seat_col].apply(lambda s: (s > 0).mean() * 100)
    data_by_rank = {
        r: (vap_ranked.loc[vap_ranked["vap_rank"] == r, group].to_numpy() * 100)
        for r in sorted(vap_ranked["vap_rank"].unique())
    }
    return data_by_rank, win_rate_by_rank


def _rank_fpv_color_data(
    district_df: pd.DataFrame, vap_df: pd.DataFrame, group: str,
    *, restrict_to: Optional[pd.DataFrame] = None,
) -> pd.Series:
    """
    Average first-place-vote share for `group`'s candidates, pooled by the same
    VAP-share rank _rank_boxplot_data uses, so the two can color the same boxes.

    Args:
        district_df: District-level rows for one (num_districts, seats_per_district,
            mode) slice of the summary table -- must have "plan", "district_id",
            and "<group>_fpv_share" columns. That column comes from
            build_summary_dataframe, itself populated from simulate_elections's
            "first_place_vote_shares" field (computed once, while the profile
            was already loaded to run elections -- see pipeline/simulate_elections.py).
        vap_df: Output of _load_district_vap_shares for the same district_count
            (used only for ranking).
        group: Candidate slate label (e.g. "A").
        restrict_to: See _rank_districts.

    Returns:
        Series indexed by vap_rank, values are that rank's mean first-place-vote
        share (0-100). Empty if district_df has no fpv data for this slate (e.g.
        its election_results predate the first_place_vote_shares field and need
        re-simulating).
    """
    col = f"{group}_fpv_share"
    if col not in district_df.columns or district_df[col].isna().all():
        return pd.Series(dtype=float)

    vap_ranked = _rank_districts(vap_df, group, restrict_to=restrict_to)
    merged = district_df[["plan", "district_id", col]].merge(
        vap_ranked[["plan", "district_id", "vap_rank"]], on=["plan", "district_id"], how="inner",
    )
    if merged.empty:
        return pd.Series(dtype=float)
    return merged.groupby("vap_rank")[col].mean() * 100


# Per-color-metric wording for the coalition boxplots: colorbar label, the
# legend/zero-case label for boxes with no signal at all, and the filename
# suffix that distinguishes the two variants on disk.
COALITION_COLOR_METRICS = {
    "win_rate": {
        "colorbar_label": "Win Rate",
        "zero_case_label": lambda group_label: f"No {group_label} winner at this rank",
        "filename_suffix": "",
    },
    "fpv_share": {
        "colorbar_label": "Avg. First-Place Vote Share",
        "zero_case_label": lambda group_label: f"No {group_label} first-place votes at this rank",
        "filename_suffix": "_fpv_share",
    },
}


def _draw_coalition_boxplot(
    ax, data_by_rank: Dict[int, Any], color_by_rank: pd.Series, group_label: str,
    *, tick_step: int = 1, small: bool = False, zero_case_label: Optional[str] = None,
) -> None:
    """Draw one rank-based coalition boxplot onto `ax` (shared by the standalone
    and grid figures below). color_by_rank is whatever per-rank 0-100 scalar
    should set box color (win rate or average first-place-vote share)."""
    ranks = sorted(data_by_rank.keys())
    data = [data_by_rank[r] for r in ranks]
    zero_case_label = zero_case_label or f"No {group_label} winner at this rank"

    nonzero_rates = color_by_rank[color_by_rank > 0]
    cmap = mcolors.LinearSegmentedColormap.from_list("winrate", ["#ffffff", COALITION_BASE_COLOR])
    if nonzero_rates.empty:
        norm = mcolors.Normalize(vmin=0, vmax=1)
    else:
        norm = mcolors.Normalize(vmin=nonzero_rates.min(), vmax=nonzero_rates.max())

    bp = ax.boxplot(
        data,
        positions=ranks,
        patch_artist=True,
        widths=0.6,
        medianprops={"color": "#0b0b0b", "linewidth": 1},
        whiskerprops={"color": COALITION_BOX_EDGE, "linewidth": 1},
        capprops={"color": COALITION_BOX_EDGE, "linewidth": 1},
        flierprops={
            "marker": "o", "markersize": 3,
            "markerfacecolor": "#898781", "markeredgecolor": "none", "alpha": 0.5,
        },
    )
    for patch, median, r in zip(bp["boxes"], bp["medians"], ranks):
        rate = color_by_rank.get(r, 0.0)
        no_signal = rate <= 0
        color = COALITION_NO_WINNER_COLOR if no_signal else cmap(norm(rate))
        patch.set_facecolor(color)
        patch.set_edgecolor(COALITION_BOX_EDGE)
        patch.set_linewidth(0.9 if not small else 0.6)
        # The default near-black median line would be invisible against a
        # solid-black no-signal box, so it's swapped to white there only.
        if no_signal:
            median.set_color("#ffffff")

    overall_mean = float(np.mean(np.concatenate(data)))
    mean_line = ax.axhline(
        overall_mean, color="#0b0b0b", linestyle=":", linewidth=1.2,
        label=f"Mean VAP Share: {overall_mean:.1f}%",
    )
    legend_fontsize = 6 if small else 8
    if not small:
        no_signal_patch = Patch(
            facecolor=COALITION_NO_WINNER_COLOR, edgecolor=COALITION_BOX_EDGE,
            label=zero_case_label,
        )
        ax.legend(handles=[mean_line, no_signal_patch], loc="upper left", fontsize=legend_fontsize, frameon=True)
    else:
        ax.legend(handles=[mean_line], loc="upper left", fontsize=legend_fontsize, frameon=True)

    label_fontsize = 7 if small else 9
    ax.set_xlabel(f"Districts ranked by {group_label} VAP share (low to high)", fontsize=label_fontsize)
    ax.set_ylabel(f"{group_label} % of District VAP", fontsize=label_fontsize)
    tick_ranks = ranks[::tick_step] if tick_step > 1 else ranks
    ax.set_xticks(tick_ranks)
    ax.set_xticklabels([str(r) for r in tick_ranks], fontsize=6 if small else 7)
    ax.set_xlim(min(ranks) - 0.7, max(ranks) + 0.7)
    ax.grid(axis="y", linewidth=0.5, color="#e1e0d9", zorder=0)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="y", labelsize=6 if small else 8)
    if not small:
        ax.set_title(group_label, fontsize=12, fontweight="bold", pad=10)
    else:
        ax.set_title(group_label, fontsize=10, fontweight="bold")


def _add_coalition_colorbar(
    fig, rect: Tuple[float, float, float, float], color_by_rank: pd.Series,
    *, label: str = "Win Rate",
) -> None:
    """Isolated horizontal colorbar legend at figure coordinates `rect`, labeled
    only at the two extreme nonzero values of color_by_rank."""
    nonzero_rates = color_by_rank[color_by_rank > 0]
    if nonzero_rates.empty:
        return
    cmap = mcolors.LinearSegmentedColormap.from_list("winrate", ["#ffffff", COALITION_BASE_COLOR])
    norm = mcolors.Normalize(vmin=nonzero_rates.min(), vmax=nonzero_rates.max())
    sm = ScalarMappable(cmap=cmap, norm=norm)
    cbar_ax = fig.add_axes(rect)
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_ticks([nonzero_rates.min(), nonzero_rates.max()])
    cbar.set_label(label, fontsize=8)
    cbar.ax.xaxis.set_label_position("top")
    cbar.set_ticklabels([f"{nonzero_rates.min():.0f}%", f"{nonzero_rates.max():.0f}%"])
    cbar.ax.tick_params(labelsize=7, length=0)
    cbar.outline.set_visible(False)


def plot_coalition_boxplot(
    df: pd.DataFrame, config, figs_dir: Path, run_name: str, mode: str = "slate_pl",
    *, color_by: str = "win_rate",
) -> None:
    """
    One rank-based coalition boxplot per (num_districts, seats_per_district)
    magnitude, for this run's own focal_group -- see the module-level docstring
    above for the rank-pooling design.

    Args:
        df: District-level summary table from build_summary_dataframe (has
            "seats_<slate>" columns per candidate slate).
        config: Parsed config dict.
        figs_dir: Where to write the figure(s).
        run_name: This run's name (used in the filename/title).
        mode: Which voter model's winners/profiles to use -- the color metric is
            a single scalar per rank, so mixing voter models (which can behave
            very differently, see notebooks/explanations.ipynb section 6) isn't
            meaningful; defaults to "slate_pl" since that's the model this
            design was validated against.
        color_by: "win_rate" (default, fraction of instances at that rank where
            the slate won a seat) or "fpv_share" (average share of first-place
            votes the slate's candidates received, from the actual generated
            ballots) -- see COALITION_COLOR_METRICS.

    Outputs:
        outputs/<run_name>/summaries/figures/<run_name>_<n>x<w>_<slate>_<mode>_coalition_boxplot[_fpv_share].png
    """
    focal_slates = [
        s for s in get_focal_slates(config)
        if s in DEFAULT_GROUP_VAP_COLUMNS or s in config.get("group_vap_columns", {})
    ]
    if not focal_slates:
        print(
            f"[summarize_results] focal_group {config['focal_group']!r} has no VAP-column "
            "mapping; skipping coalition boxplot."
        )
        return
    slate = focal_slates[0]
    metric = COALITION_COLOR_METRICS[color_by]

    df_mode = df[df["mode"] == mode]
    if df_mode.empty:
        print(f"[summarize_results] No rows for mode={mode!r}; skipping coalition boxplot.")
        return

    for (num_dist, seats_per_district), district_df in df_mode.groupby(["num_districts", "seats_per_district"]):
        vap_df = _load_district_vap_shares(config, int(num_dist))
        data_by_rank, win_rate_by_rank = _rank_boxplot_data(district_df, vap_df, slate)
        if not data_by_rank:
            print(
                f"[summarize_results] No VAP-share/winner data for slate={slate!r}, "
                f"{num_dist}x{seats_per_district}; skipping coalition boxplot."
            )
            continue

        if color_by == "fpv_share":
            color_by_rank = _rank_fpv_color_data(district_df, vap_df, slate)
        else:
            color_by_rank = win_rate_by_rank

        fig = plt.figure(figsize=(max(10.0, len(data_by_rank) * 0.35), 7))
        ax = fig.add_axes((0.07, 0.1, 0.88, 0.72))
        _draw_coalition_boxplot(
            ax, data_by_rank, color_by_rank, _group_label(slate),
            zero_case_label=metric["zero_case_label"](_group_label(slate)),
        )
        ax.set_title("")  # title lives on the figure instead, to match the header layout below

        fig.suptitle(
            f"{_group_label(slate)} Coalition Percent of District VAP in {num_dist} District Plans",
            fontsize=15, fontweight="bold", y=0.97,
        )
        fig.text(
            0.5, 0.9, f"{run_name} - {MODEL_NAMES.get(mode, mode)} Model",
            ha="center", va="bottom", fontsize=11, color="#52514e", style="italic",
        )
        _add_coalition_colorbar(fig, (0.07, 0.855, 0.16, 0.015), color_by_rank, label=metric["colorbar_label"])

        fig_path = (
            figs_dir
            / f"{run_name}_{num_dist}x{seats_per_district}_{slate}_{mode}_coalition_boxplot{metric['filename_suffix']}.png"
        )
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"[summarize_results] Wrote: {fig_path}")


def plot_coalition_boxplot_available(
    df: pd.DataFrame, config, figs_dir: Path, run_name: str, mode: str = "slate_pl",
    *, color_by: str = "win_rate",
) -> None:
    """
    Same design as plot_coalition_boxplot, but restricted to (plan, district)
    instances where the focal slate actually had a candidate on the ballot.
    A district a slate was apportioned zero candidates in could never elect
    that slate structurally, regardless of VAP share, so including it in the
    unfiltered plot dilutes the low ranks with districts that were never
    contestable in the first place; this variant pools only the subset that
    was. Ranking still happens against every district in the plan first (see
    _rank_boxplot_data's restrict_to), so a rank number here means the same
    district position as in the unfiltered plot -- some low ranks may simply
    be missing rather than every remaining rank shifting down to fill the gap.

    Args:
        color_by: See plot_coalition_boxplot.

    Outputs:
        outputs/<run_name>/summaries/figures/<run_name>_<n>x<w>_<slate>_<mode>_coalition_boxplot_available[_fpv_share].png
    """
    focal_slates = [
        s for s in get_focal_slates(config)
        if s in DEFAULT_GROUP_VAP_COLUMNS or s in config.get("group_vap_columns", {})
    ]
    if not focal_slates:
        print(
            f"[summarize_results] focal_group {config['focal_group']!r} has no VAP-column "
            "mapping; skipping available-candidate coalition boxplot."
        )
        return
    slate = focal_slates[0]
    metric = COALITION_COLOR_METRICS[color_by]

    df_mode = df[df["mode"] == mode]
    if df_mode.empty:
        print(f"[summarize_results] No rows for mode={mode!r}; skipping available-candidate coalition boxplot.")
        return

    for (num_dist, seats_per_district), district_df in df_mode.groupby(["num_districts", "seats_per_district"]):
        vap_df = _load_district_vap_shares(config, int(num_dist))
        availability_df = _load_district_candidate_availability(config, int(num_dist), slate)
        if availability_df.empty:
            print(
                f"[summarize_results] No candidate-availability data for slate={slate!r}, "
                f"{num_dist}x{seats_per_district}; skipping available-candidate coalition boxplot."
            )
            continue

        available_pairs = availability_df.loc[availability_df["has_candidate"], ["plan", "district_id"]]

        data_by_rank, win_rate_by_rank = _rank_boxplot_data(
            district_df, vap_df, slate, restrict_to=available_pairs,
        )
        if not data_by_rank:
            print(
                f"[summarize_results] No VAP-share/winner data for slate={slate!r}, "
                f"{num_dist}x{seats_per_district} (available-candidate filter); skipping."
            )
            continue

        if color_by == "fpv_share":
            color_by_rank = _rank_fpv_color_data(district_df, vap_df, slate, restrict_to=available_pairs)
        else:
            color_by_rank = win_rate_by_rank

        fig = plt.figure(figsize=(max(10.0, len(data_by_rank) * 0.35), 7))
        ax = fig.add_axes((0.07, 0.1, 0.88, 0.72))
        _draw_coalition_boxplot(
            ax, data_by_rank, color_by_rank, _group_label(slate),
            zero_case_label=metric["zero_case_label"](_group_label(slate)),
        )
        ax.set_title("")  # title lives on the figure instead, to match the header layout below

        fig.suptitle(
            f"{_group_label(slate)} Coalition Percent of District VAP in {num_dist} District Plans",
            fontsize=15, fontweight="bold", y=0.97,
        )
        fig.text(
            0.5, 0.9,
            f"{run_name} - {MODEL_NAMES.get(mode, mode)} Model "
            f"(Districts with an Available {_group_label(slate)} Candidate)",
            ha="center", va="bottom", fontsize=10, color="#52514e", style="italic",
        )
        _add_coalition_colorbar(fig, (0.07, 0.855, 0.16, 0.015), color_by_rank, label=metric["colorbar_label"])

        fig_path = (
            figs_dir
            / f"{run_name}_{num_dist}x{seats_per_district}_{slate}_{mode}_coalition_boxplot_available{metric['filename_suffix']}.png"
        )
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"[summarize_results] Wrote: {fig_path}")


def plot_coalition_boxplot_grid(
    df: pd.DataFrame, config, figs_dir: Path, run_name: str, mode: str = "slate_pl",
    *, color_by: str = "win_rate",
) -> None:
    """
    Grid of rank-based coalition boxplots, one panel per candidate slate in this
    run with a VAP-column mapping (W/B/H/A for every config seen so far, hence
    "2x2" -- see plot_coalition_boxplot for the per-panel design and why `mode`
    defaults to "slate_pl").

    Args:
        color_by: See plot_coalition_boxplot.

    Outputs:
        outputs/<run_name>/summaries/figures/<run_name>_<n>x<w>_<mode>_coalition_boxplot_grid[_fpv_share].png
    """
    groups = [
        g for g in config["slate_to_candidates"]
        if g in DEFAULT_GROUP_VAP_COLUMNS or g in config.get("group_vap_columns", {})
    ]
    if not groups:
        print("[summarize_results] No slates with a VAP-column mapping; skipping coalition boxplot grid.")
        return

    df_mode = df[df["mode"] == mode]
    if df_mode.empty:
        print(f"[summarize_results] No rows for mode={mode!r}; skipping coalition boxplot grid.")
        return

    metric = COALITION_COLOR_METRICS[color_by]
    ncols = 2
    nrows = -(-len(groups) // ncols)  # ceil division

    for (num_dist, seats_per_district), district_df in df_mode.groupby(["num_districts", "seats_per_district"]):
        vap_df = _load_district_vap_shares(config, int(num_dist))

        fig, axes = plt.subplots(nrows, ncols, figsize=(8 * ncols, 5 * nrows), squeeze=False)
        flat = [ax for row in axes for ax in row]

        drawn = 0
        for ax, group in zip(flat, groups):
            data_by_rank, win_rate_by_rank = _rank_boxplot_data(district_df, vap_df, group)
            if not data_by_rank:
                ax.axis("off")
                continue
            color_by_rank = (
                _rank_fpv_color_data(district_df, vap_df, group) if color_by == "fpv_share" else win_rate_by_rank
            )
            tick_step = max(1, len(data_by_rank) // 10)
            _draw_coalition_boxplot(
                ax, data_by_rank, color_by_rank, _group_label(group),
                tick_step=tick_step, small=True,
                zero_case_label=metric["zero_case_label"](_group_label(group)),
            )
            drawn += 1

        for ax in flat[len(groups):]:
            ax.axis("off")

        if drawn == 0:
            plt.close(fig)
            print(
                f"[summarize_results] No VAP-share/winner data for {num_dist}x{seats_per_district}; "
                "skipping coalition boxplot grid."
            )
            continue

        fig.suptitle(
            f"Coalition Percent of District VAP in {num_dist} District Plans",
            fontsize=15, fontweight="bold", y=0.99,
        )
        fig.text(
            0.5, 0.955, f"{run_name} - {MODEL_NAMES.get(mode, mode)} Model",
            ha="center", va="bottom", fontsize=11, color="#52514e", style="italic",
        )
        fig.tight_layout(rect=(0, 0, 1, 0.91))

        fig_path = (
            figs_dir / f"{run_name}_{num_dist}x{seats_per_district}_{mode}_coalition_boxplot_grid{metric['filename_suffix']}.png"
        )
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"[summarize_results] Wrote: {fig_path}")



def _per_mode_distribution_for_run(summary_csv: Path) -> Optional[pd.DataFrame]:
    """
    Read one run's summary CSV and return the per-mode focal-seat distribution,
    including the pooled COMBINED_MODE row.

    Returns a DataFrame with columns [mode, focal_seats, count], collapsing
    across election methods and district configurations.  Returns None if the
    summary is empty or unreadable.
    """
    df = pd.read_csv(summary_csv)
    if df.empty:
        return None

    df_plan = aggregate_to_plan_level(df)
    counts = _occurrence_counts(df_plan)
    if counts.empty:
        return None

    return counts.groupby(["mode", "focal_seats"], as_index=False)["count"].sum()


def plot_combined_bubbles_all_runs(
    config,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Compare every completed run in a single stacked bubble figure layout
    resembling image_48ad1f.png.

    Scans ``outputs/*/summaries/*_summary.csv`` for finished runs.  Each run
    gets its own subplot stacked vertically, with one y-axis row per voter mode
    plus a pooled "Combined" row at the bottom.  Bubble area encodes how many
    plans produced each focal-seat count (x-axis).  A dotted line marks the
    focal group's proportional-representation seat share.

    Bubble areas are scaled consistently across all subplots so sizes remain
    comparable across runs.

    Args:
        config: Any run's parsed config; used only for the seat-count axis range
            and the population-share reference line, which are shared across runs.
        output_dir: Where to write the figure. Defaults to
            outputs/cross_run_summaries/figures.

    Returns:
        Path to the written figure, or None if no completed runs were found.
    """
    summary_paths = sorted(Path("outputs").glob("*/summaries/*_summary.csv"))

    runs: List[Tuple[Tuple[int, int, str], str, pd.DataFrame]] = []
    for path in summary_paths:
        per_mode = _per_mode_distribution_for_run(path)
        if per_mode is None:
            continue
        df_head = pd.read_csv(path, usecols=["run_name", "num_districts", "seats_per_district"])
        label = str(df_head["run_name"].iloc[0])
        num_dist = int(df_head["num_districts"].min())
        seats_per_district = int(df_head["seats_per_district"].min())
        runs.append(((num_dist, seats_per_district, label), label, per_mode))

    if not runs:
        print("[summarize_results] No completed runs found for cross-run bubble plot.")
        return None

    runs.sort(key=lambda r: (not r[1].lower().startswith("basic"), r[0]))

    iprop = _focal_population_share(config, gpd.read_file(Path(config["geodata_path"])))
    observed_max_seats = max(int(c["focal_seats"].max()) for _, _, c in runs)
    total_seats = max(int(config["total_seats"]), observed_max_seats)
    i_share = iprop * total_seats

    # Row order: individual modes in DESIRED_ORDER, then Combined at the bottom.
    all_modes: set = set()
    for _, _, c in runs:
        all_modes.update(c["mode"].unique())
    modes_in_order = [m for m in DESIRED_ORDER if m in all_modes]
    if COMBINED_MODE in all_modes:
        modes_in_order.append(COMBINED_MODE)

    # ROW_SPACING controls the y-axis gap between adjacent mode rows. At 1.5 data
    # units and typical figure heights the maximum bubble (BUBBLE_MAX_AREA=100,
    # diameter ~10pt ≈ 0.14in) stays well clear of neighbouring rows.
    ROW_SPACING = 1.5
    COMBINED_GAP = 0.6  # extra separation above the standard row spacing
    individual_modes = [m for m in modes_in_order if m != COMBINED_MODE]
    has_combined = COMBINED_MODE in modes_in_order

    y_index: Dict[str, float] = {m: float(i) * ROW_SPACING for i, m in enumerate(individual_modes)}
    if has_combined:
        y_index[COMBINED_MODE] = len(individual_modes) * ROW_SPACING + COMBINED_GAP

    n_individual = len(individual_modes)
    y_top = y_index[COMBINED_MODE] if has_combined else (n_individual - 1) * ROW_SPACING
    y_tick_positions = [y_index[m] for m in modes_in_order]
    y_tick_labels = [LEGEND_MAPPING.get(m, m) for m in modes_in_order]

    n_modes = len(modes_in_order)
    n_runs = len(runs)

    subplot_h = ROW_SPACING * n_modes * 0.35 + 0.8
    fig_height = subplot_h * n_runs + 0.8
    fig, axes = plt.subplots(
        n_runs, 1,
        figsize=(10, fig_height),
        gridspec_kw={"hspace": 0.9},
    )
    if n_runs == 1:
        axes = [axes]

    # Cap the seat axis near the data so the grid isn't mostly empty.
    x_upper = _seat_axis_upper(max(observed_max_seats, i_share), total_seats)
    x_ticks = range(0, x_upper + 1, X_TICK_STEP)

    for ax, (_, label, per_mode) in zip(axes, runs):
        for mode in modes_in_order:
            sub = per_mode[per_mode["mode"] == mode]
            if sub.empty:
                continue
            # Scale each row independently: the most-common seat count in this
            # mode fills BUBBLE_MAX_AREA; the least common fills BUBBLE_MIN_AREA.
            row_max = sub["count"].max()
            row_scale = (BUBBLE_MAX_AREA - BUBBLE_MIN_AREA) / row_max if row_max > 0 else 0
            sizes = BUBBLE_MIN_AREA + sub["count"] * row_scale
            bubble_color = COMBINED_BUBBLE_COLOR if mode == COMBINED_MODE else MODE_COLORS.get(mode, BUBBLE_COLOR)
            ax.scatter(
                sub["focal_seats"],
                [y_index[mode]] * len(sub),
                s=sizes,
                color=bubble_color,
                alpha=0.7,
                edgecolor="none",
                linewidth=0,
            )

        ax.axvline(i_share, color=PROP_LINE_COLOR, linestyle=":", linewidth=1.2)

        ax.set_xlim(-1, x_upper + 1)
        # Inverted y-axis: y=0 sits below the headroom band reserved for the title.
        # Scale the headroom with ROW_SPACING so it stays ~one row's worth of space.
        ax.set_ylim(y_top + ROW_SPACING * 0.5, -ROW_SPACING * 1.5)
        ax.set_yticks(y_tick_positions)
        ax.set_yticklabels(y_tick_labels, fontsize=8)
        ax.set_xticks(x_ticks)
        ax.set_xticklabels([str(x) for x in x_ticks], fontsize=8)
        ax.tick_params(axis="both", which="major", labelsize=8)
        for spine in ax.spines.values():
            spine.set_linewidth(0.5)

        # Fixed offset from top-left corner; serif font gives it a distinct style.
        ax.text(
            0.01, 0.94,
            label.replace("_", " "),
            transform=ax.transAxes,
            fontsize=10,
            fontweight="bold",
            fontfamily="serif",
            va="top",
            ha="left",
        )
        ax.set_xlabel("Citywide Seats Won", fontsize=8, fontweight="bold")

    # Clean layout and attach global reference line legend at top
    prop_handle = Line2D(
        [0], [0],
        color=PROP_LINE_COLOR,
        linestyle=":",
        linewidth=1.2,
        label=f"Proportional representation ({iprop * 100:.1f}%)",
    )
    # Reserve 1 inch at the top for the figure title and legend.
    top_margin = 1 - 1.0 / fig_height
    fig.subplots_adjust(top=top_margin)
    fig.suptitle(
        f"Election Outcomes for {_group_label(config['focal_group'])}-Preferred Candidates",
        fontsize=11, fontweight="bold", y=0.99,
    )
    fig.legend(
        handles=[prop_handle],
        loc="lower center",
        bbox_to_anchor=(0.5, top_margin + 0.1 / fig_height),
        fontsize=7,
        frameon=True,
    )

    if output_dir is None:
        output_dir = Path("outputs") / "cross_run_summaries" / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    fig_path = output_dir / "combined_bubbles_all_runs.png"
    fig.savefig(fig_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    print(f"[summarize_results] Wrote multi-panel cross-run figure to: {fig_path}")
    return fig_path

def summarize_results(config) -> Path:
    """
    Aggregate election results into a summary csv and produce histogram figures.

    Args:
        config: Parsed config dict.

    Outputs:
        - outputs/<run_name>/summaries/<run_name>_summary.csv: one row per
          (replicate, plan, district, election_method) tuple, with columns for plan,
          mode, district_id, rep, focal_seats, the population columns from config, and
          combined_support.
        - outputs/<run_name>/summaries/figures/*.png: one histogram per
          (district_count, seats_per_district, election_method) showing the
          distribution of focal-group seats across modes.

    Returns:
        Path to the summary directory.
    """
    run_name = str(config["run_name"])
    focal_group = str(config["focal_group"])

    iprop, i_cs_turnout = _compute_representation_baselines(config)

    results_dir, summary_dir, figs_dir = _prepare_directories(run_name)

    df = build_summary_dataframe(config, results_dir, i_cs_turnout)

    # Persist the tidy, district-level table.
    csv_path = summary_dir / f"{run_name}_summary.csv"
    df.to_csv(csv_path, index=False)

    df_plan = aggregate_to_plan_level(df)

    plot_representation_histograms(
        df_plan, config, focal_group, iprop, i_cs_turnout, figs_dir, run_name
    )

    # By-slate proportional-representation panel (one histogram per candidate slate).
    if config.get("slate_to_candidates"):
        plot_slate_representation_panels(
            df_plan, config, _slate_baselines(config), figs_dir, run_name
        )

    plot_representation_bubbles(df_plan, config, focal_group, iprop, figs_dir, run_name)

    # Coalition boxplots (district-level, not plan-aggregated -- these need
    # per-district VAP share, so they use `df` rather than `df_plan`). Each is
    # generated twice: once colored by win rate, once by average first-place-
    # vote share (see COALITION_COLOR_METRICS) -- the fpv_share pass reads
    # profiles.zip and is meaningfully slower than the others.
    for color_by in COALITION_COLOR_METRICS:
        plot_coalition_boxplot(df, config, figs_dir, run_name, color_by=color_by)
        plot_coalition_boxplot_available(df, config, figs_dir, run_name, color_by=color_by)
        plot_coalition_boxplot_grid(df, config, figs_dir, run_name, color_by=color_by)

    print(f"[summarize_results] Wrote CSV: {csv_path}")
    print(f"[summarize_results] Figures in: {figs_dir}")
    return summary_dir


def _district_demographics_csv_path(run_name: str) -> Path:
    """
    Namespaced path for a run's district-demographics CSV. The demographics
    reflect the run's particular ensemble, so each run gets its own file under
    the shared cross_run_summaries directory rather than overwriting a common one.
    """
    return Path("outputs/cross_run_summaries") / f"{run_name}_district_demographics.csv"


def export_district_demographics_csv(
    config,
    output_path: Optional[Path] = None,
) -> Optional[Path]:
    """
    Export per-district racial VAP demographics for every sampled plan in every
    completed district-count ensemble, as a flat CSV.

    Reads the district assignment chain(s) already generated for this config's
    ensemble under outputs/districts/chain_out/<signature>/<district_count>/ (one
    such ensemble per distinct district count found there, e.g. 10 and 50). These
    are raw VAP facts straight from the geodata, so the demographics reflect this
    config's particular ensemble (a neutral chain and a bloc-optimized short-burst
    run for the same district count have different signatures and differ).

    Args:
        config: Parsed config dict; its run_name selects the ensemble, and its
            geodata_path, chain_length, num_subsamples, and (optionally)
            group_vap_columns drive the export.
        output_path: Where to write the CSV. Defaults to
            outputs/cross_run_summaries/<run_name>_district_demographics.csv.

    Outputs:
        A CSV with one row per (district_count, plan_idx, district_id), with
        columns district_count, plan_idx, district_id, total_vap, then one
        <group>_vap and <group>_share column pair per demographic group in
        group_vap_columns (e.g. white_vap_20, white_vap_20_share, ...).

    Returns:
        Path to the written CSV, or None if no district-count ensembles were
        found under outputs/districts/chain_out/<signature>/.
    """
    chain_out_root = get_chain_out_dir(ensemble_signature(config))
    if not chain_out_root.is_dir():
        print("[summarize_results] No district ensembles found; skipping demographics export.")
        return None

    district_counts = sorted(
        int(p.name) for p in chain_out_root.iterdir() if p.is_dir() and p.name.isdigit()
    )
    if not district_counts:
        print("[summarize_results] No district ensembles found; skipping demographics export.")
        return None

    group_columns = get_group_vap_columns(
        config, config.get("group_vap_columns", DEFAULT_GROUP_VAP_COLUMNS).keys()
    )
    vap_cols = list(dict.fromkeys(group_columns.values()))
    population_vap_col = config["population_vap_column"]

    population_data = gpd.read_file(config["geodata_path"])
    population_data = population_data[vap_cols + [population_vap_col]]

    chain_length = config["chain_length"]
    num_subsamples = config["num_subsamples"]
    subsample_interval = chain_length // num_subsamples

    rows: List[Dict[str, Any]] = []
    for district_num in district_counts:
        path_to_districting = chain_out_root / str(district_num) / f"{district_num}_districts.jsonl.gz"
        if not path_to_districting.is_file():
            continue

        with gzip.open(path_to_districting, mode="rt", encoding="utf-8") as gz_file:
            for sample_idx, sample in enumerate(jl.Reader(gz_file)):
                if sample_idx % subsample_interval != 0:
                    continue

                population_data["district_plan"] = sample["assignment"]
                data_by_district = population_data.groupby("district_plan").sum()

                for district_id, row in data_by_district.iterrows():
                    total_vap = float(row[population_vap_col])
                    record = {
                        "district_count": district_num,
                        "plan_idx": sample_idx,
                        "district_id": district_id,
                        "total_vap": total_vap,
                    }
                    for col in vap_cols:
                        vap = float(row[col])
                        record[col] = vap
                        record[f"{col}_share"] = vap / total_vap if total_vap > 0 else 0.0
                    rows.append(record)

    if not rows:
        print("[summarize_results] No sampled plans found; skipping demographics export.")
        return None

    if output_path is None:
        output_path = _district_demographics_csv_path(config["run_name"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(rows).to_csv(output_path, index=False)
    print(f"[summarize_results] Wrote district demographics CSV: {output_path}")
    return output_path


# Fixed (group_column, display label, color) triples, in a stable order, for the
# district-demographics boxplots. Colors are their own categorical set (green,
# violet, red, orange) distinct from MODE_COLORS' blue/aqua/yellow, since a
# racial-group series and a voter-model series are different variables that can
# appear in the same report.
DISTRICT_DEMOGRAPHIC_GROUPS = [
    ("white_vap_20_share", "White", "#008300"),
    ("bvap_20_share", "Black", "#4a3aa7"),
    ("hvap_20_share", "Latino", "#e34948"),
    ("asian_nhpi_vap_20_share", "Asian", "#eb6834"),
]


def plot_district_demographics(
    run_name: str,
    csv_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> List[Path]:
    """
    Plot the distribution of each racial group's district-level VAP share,
    pooling every district-instance across all sampled plans, one figure per
    district-count ensemble.

    Args:
        run_name: The run whose demographics to plot; selects the namespaced CSV
            and prefixes the figure filenames so runs don't overwrite each other.
        csv_path: Path to the CSV written by export_district_demographics_csv.
            Defaults to
            outputs/cross_run_summaries/<run_name>_district_demographics.csv.
        output_dir: Where to write the figures. Defaults to
            outputs/cross_run_summaries/figures.

    Outputs:
        One png per distinct district_count in the csv, at
        outputs/cross_run_summaries/figures/<run_name>_district_demographics_<n>districts.png.
        Each figure is a boxplot with one box per racial group (White, Black,
        Latino, Asian), showing that group's share of district VAP pooled
        across every (plan, district) pair for that ensemble.

    Returns:
        List of paths to the written figures.
    """
    if csv_path is None:
        csv_path = _district_demographics_csv_path(run_name)
    if not csv_path.is_file():
        print(f"[summarize_results] {csv_path} not found; run export_district_demographics_csv first.")
        return []

    df = pd.read_csv(csv_path)

    if output_dir is None:
        output_dir = Path("outputs/cross_run_summaries/figures")
    output_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for district_count, group_df in df.groupby("district_count"):
        fig, ax = plt.subplots(figsize=(7, 5))

        data = [group_df[col].to_numpy() * 100 for col, _, _ in DISTRICT_DEMOGRAPHIC_GROUPS]
        labels = [label for _, label, _ in DISTRICT_DEMOGRAPHIC_GROUPS]
        colors = [color for _, _, color in DISTRICT_DEMOGRAPHIC_GROUPS]

        bp = ax.boxplot(
            data,
            tick_labels=labels,
            patch_artist=True,
            widths=0.5,
            medianprops={"color": "#0b0b0b", "linewidth": 1.5},
            whiskerprops={"color": "#52514e", "linewidth": 1},
            capprops={"color": "#52514e", "linewidth": 1},
            flierprops={
                "marker": "o", "markersize": 3,
                "markerfacecolor": "#898781", "markeredgecolor": "none", "alpha": 0.5,
            },
        )
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.55)
            patch.set_edgecolor(color)

        n_plans = int(group_df["plan_idx"].nunique())
        n_instances = len(group_df)
        ax.set_title(
            f"District-Level Racial Composition — {district_count}-District Ensemble",
            fontsize=13, fontweight="bold", pad=18,
        )
        ax.text(
            0.5, 1.02,
            f"{n_plans} sampled plans · {n_instances} district-instances pooled",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=9, color="#52514e", style="italic",
        )
        ax.set_ylabel("Share of District VAP (%)")
        ax.set_ylim(0, 100)
        ax.grid(axis="y", linewidth=0.5, color="#e1e0d9", zorder=0)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_linewidth(0.5)
        ax.tick_params(axis="both", labelsize=9)

        fig_path = output_dir / f"{run_name}_district_demographics_{district_count}districts.png"
        fig.savefig(fig_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
        written.append(fig_path)
        print(f"[summarize_results] Wrote: {fig_path}")

    return written


def export_one_plan_breakdown(
    district_count: int,
    plan_idx: int = 0,
    *,
    run_name: str,
    csv_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Export a per-district racial breakdown for one sampled plan as a CSV.

    District identity isn't comparable across sampled plans (district_id 0 in
    one plan is a different piece of the city than district_id 0 in another),
    so unlike the pooled boxplots in plot_district_demographics, this shows one
    concrete plan's actual districts side by side, in district_id order.

    Args:
        district_count: Which ensemble to pull from (e.g. 10 or 50).
        plan_idx: Which sampled plan to show. Defaults to 0 (the first sampled
            plan for that ensemble).
        run_name: The run whose ensemble to read; selects the namespaced source
            CSV and prefixes the breakdown csv filename.
        csv_path: Path to the CSV written by export_district_demographics_csv.
            Defaults to
            outputs/cross_run_summaries/<run_name>_district_demographics.csv.
        output_dir: Where to write the csv. Defaults to
            outputs/cross_run_summaries.

    Outputs:
        outputs/cross_run_summaries/<run_name>_district_breakdown_<n>districts_plan<p>.csv

    Returns:
        The csv path, or None if the requested (district_count, plan_idx)
        isn't present in the source csv.
    """
    if csv_path is None:
        csv_path = _district_demographics_csv_path(run_name)
    if not csv_path.is_file():
        print(f"[summarize_results] {csv_path} not found; run export_district_demographics_csv first.")
        return None

    df = pd.read_csv(csv_path)
    plan_df = df[(df["district_count"] == district_count) & (df["plan_idx"] == plan_idx)]
    if plan_df.empty:
        print(f"[summarize_results] No rows for district_count={district_count}, plan_idx={plan_idx}.")
        return None

    plan_df = plan_df.sort_values("district_id")

    if output_dir is None:
        base_dir = Path("outputs/cross_run_summaries")
    else:
        base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    # DISTRICT_DEMOGRAPHIC_GROUPS' col is already the "<group>_share" column;
    # the raw VAP count column is that name with the "_share" suffix stripped.
    share_cols = [col for col, _, _ in DISTRICT_DEMOGRAPHIC_GROUPS]
    raw_cols = [col.removesuffix("_share") for col in share_cols]
    summary_cols = ["district_id", "total_vap"] + raw_cols + share_cols
    csv_out_path = base_dir / f"{run_name}_district_breakdown_{district_count}districts_plan{plan_idx}.csv"
    plan_df[summary_cols].to_csv(csv_out_path, index=False)
    print(f"[summarize_results] Wrote per-plan district breakdown CSV: {csv_out_path}")

    return csv_out_path
