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
import geopandas as gpd

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from pipeline.utils.helpers import (
    parse_district_configs,
    parse_plan_district_rep_from_path,
    count_focal_winners,
    load_json,
    find_settings_file,
    get_voter_models,
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

# Fixed colors / labels so every figure reads the same way.
MODE_COLORS = {
    "cambridge": "#E32636",
    "slate_bt": "#FFBF00",
    "slate_pl": "#8DB600",
}

# Pseudo-mode that pools occurrences across every voter model into one row.
COMBINED_MODE = "combined"

LEGEND_MAPPING = {
    "slate_bt": "Deliberative",
    "slate_pl": "Impulsive",
    "cambridge": "Cambridge",
    COMBINED_MODE: "Combined",
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

    rows: List[Dict[str, Any]] = []
    # --- One row per simulated profile (and per election method) ----
    for idx, result in enumerate(election_results):
        # Recover (plan, district, replicate) by parsing the profile path,
        # e.g. ..._district_plan_003_district_07_v1.csv -> (3, 7, 1).
        plan, district, rep = parse_plan_district_rep_from_path(profile_files[idx])

        total_vap, total_ivap = _district_population(settings_dir, config, plan, district)

        # A single profile may be scored under several methods (e.g. a
        # single-winner district under Plurality and IRV), so we emit one
        # row per method, each with its own focal-seat count.
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
            # Per-slate seat counts feed the by-slate representation panel.
            for slate in slate_to_candidates:
                row[f"seats_{slate}"] = count_focal_winners(winners, slate, slate_to_candidates)
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
    # bar_width=0.3 gives a total group span of 0.6 per tick, leaving a 0.4-wide
    # gap between adjacent seat groups. Alpha=0.5 keeps all layers visible.
    bar_width = 0.3
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
            alpha=0.5,
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
    support) so their descriptions live in the legend instead of on the plot."""
    ordered_handles, ordered_labels = _ordered_mode_handles(ax)
    handles = ordered_handles + list(ref_handles or [])
    labels = ordered_labels + list(ref_labels or [])
    ax.legend(handles, labels, fontsize=8)


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
    color_cs = "xkcd:brownish grey"
    color_iprop = "xkcd:purplish brown"

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

# Color of the focal-group proportional-representation reference line (matches
# the "<focal group> share of VAP" line on the histograms).
PROP_LINE_COLOR = "orangered"

# Single fill color for individual-mode bubbles; Combined uses a distinct dark color.
BUBBLE_COLOR = "#4C72B0"
COMBINED_BUBBLE_COLOR = "#222222"


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

    print(f"[summarize_results] Wrote CSV: {csv_path}")
    print(f"[summarize_results] Figures in: {figs_dir}")
    return summary_dir
