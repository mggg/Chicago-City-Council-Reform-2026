from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Dict, Optional
import re
import json

@dataclass(frozen=True)
class DistrictConfig:
    """One district configuration: number of districts and seats won per district."""
    num_districts: int
    winners: int

def load_json(path: Path) -> Dict[str, Any]:
    """
    Load and return the contents of a json file.

    Args:
        path: Path to the json file.

    Returns:
        Parsed json contents as a dict.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# Voter models generated/simulated/summarized when a config does not specify its
# own "voter_models" list. Kept for backward compatibility with older configs.
DEFAULT_VOTER_MODELS = ["slate_pl", "slate_bt", "cambridge"]


def get_voter_models(config) -> List[str]:
    """
    Return the ordered list of voter models for this run.

    Read from config["voter_models"] when present (a list of model-name strings),
    otherwise fall back to DEFAULT_VOTER_MODELS. This is the single source of truth
    for which models the profile, election, and summary stages iterate over.

    Args:
        config: Parsed config dict.

    Returns:
        List of voter-model name strings.

    Raises:
        ValueError: If "voter_models" is present but not a list of strings.
    """
    models = config.get("voter_models", DEFAULT_VOTER_MODELS)
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError('config["voter_models"] must be a list of strings.')
    return list(models)


def get_non_focal_group(config):
    """
    Determine the non focal group using the turnout parameter and focal group parameter specified in the configuration file.

    Args:
        config: Parsed config dict.

    Returns:
        Name of the non-focal group as a string.
    """
    non_focal_group = next(iter(config["turnout"].keys() - {config["focal_group"]}))
    return non_focal_group

def parse_district_configs(raw: Any) -> List[DistrictConfig]:
    """
    Parse the district_configs field from the config file into DistrictConfig objects.
    accepts two schemas:
      - newer: [{"num_districts": 5, "winners": 2}, ...]
      - older: [{<num_districts>: <winners>}, ...] e.g. [{80: 1}, {20: 4}]

    Args:
        raw: The raw district_configs value from the config (expected to be a list).

    Returns:
        List of DistrictConfig(num_districts, winners).

    Raises:
        ValueError: If raw is not a list or entries don't match either schema.
    """
    if not isinstance(raw, list):
        raise ValueError("district_configs must be a list")

    parsed: List[DistrictConfig] = []
    for item in raw:
        if isinstance(item, dict) and "num_districts" in item and "winners" in item:
            parsed.append(DistrictConfig(int(item["num_districts"]), int(item["winners"])))
        elif isinstance(item, dict) and len(item) == 1:
            (k, v), = item.items()
            parsed.append(DistrictConfig(int(k), int(v)))
        else:
            raise ValueError(
                "Each district_configs entry must be either "
                '{"num_districts": <int>, "winners": <int>} or {<int>: <int>}.'
            )
    return parsed


def parse_plan_district_rep_from_path(p: str | Path):
    """
    Parse the plan index, district id, and replicate number from a profile file path.

    Args:
        p: Path to a profile csv file, expected to contain substrings like
           "district_plan_000", "district_02", and "v0" (replicate index is 0-based).

    Returns:
        Tuple (plan, district, rep) where each is an int parsed directly from the
        path (not normalized to any index base), or None if the pattern is not found.
    """
    s = str(p)

    # plan: match "district_plan_000" OR "plan_000"
    m_plan = re.search(r"(?:district[_-]?plan[_-]?|plan[_-]?)(\d+)", s, flags=re.IGNORECASE)
    plan = int(m_plan.group(1)) if m_plan else None

    # district: collect all occurrences like "district_00" and take the last one
    districts = re.findall(r"district[_-]?(\d+)", s, flags=re.IGNORECASE)
    district = int(districts[-1]) if districts else None

    # replicate/version: files use v0, v1... so parse "v0"
    m_v = re.search(r"(?:^|[_-])v(\d+)(?:\D|$)", s, flags=re.IGNORECASE)
    rep = int(m_v.group(1)) if m_v else None

    return plan, district, rep


def is_focal_candidate(candidate: str, focal_group: str, slate_to_candidates: Dict[str, List[str]]) -> bool:
    """
    Check whether a candidate belongs to the focal group.
    a candidate matches if they appear in the explicit slate list, or if the focal
    group is a single character and the candidate id starts with that character.

    Args:
        candidate: Candidate id string.
        focal_group: Name of the focal group (e.g., "A").
        slate_to_candidates: Mapping from group name to list of candidate ids.

    Returns:
        True if the candidate is focal, false otherwise.
    """
    focal_list = set(map(str, slate_to_candidates.get(focal_group, [])))
    c = str(candidate)

    if c in focal_list:
        return True
    if len(focal_group) == 1 and c.startswith(focal_group):
        return True
    return False


def count_focal_winners(
    winners: Iterable[str],
    focal_group: str,
    slate_to_candidates: Dict[str, List[str]],
) -> int:
    """
    Count the number of election winners belonging to the focal group.

    Args:
        winners: Iterable of winning candidate id strings.
        focal_group: Name of the focal group.
        slate_to_candidates: Mapping from group name to list of candidate ids.

    Returns:
        Integer count of focal-group winners.
    """
    return sum(1 for w in winners if is_focal_candidate(str(w), focal_group, slate_to_candidates))


def ensemble_signature(config) -> str:
    """
    A readable identifier for the district ensemble a config produces, based on
    the generation strategy rather than the run name: "optimized" when the config
    sets optimize_for_bloc, otherwise "neutral". Runs that generate an equivalent
    ensemble (e.g. neutral 10x3 vs. 10x5 STV) share one signature — and therefore
    one generated chain — while a bloc-optimized run gets its own.

    The other chain determinants (seed, chain length, epsilon, geodata, population
    column, and which bloc is optimized) are not in the name; the stored chain
    metadata records them and has_valid_district_outputs regenerates on a mismatch.

    Args:
        config: Parsed config dict.

    Returns:
        "optimized" or "neutral".
    """
    return "optimized" if config.get("optimize_for_bloc") else "neutral"


def get_chain_out_dir(signature: str, num_districts: Optional[int] = None) -> Path:
    """
    Return the district-chain output directory for an ensemble signature (see
    ensemble_signature). Keyed by the ensemble's determinants rather than a run
    name, so runs that produce the same chain share it (no redundant regeneration)
    while genuinely different ensembles stay separate.

    Args:
        signature: The ensemble signature from ensemble_signature(config).
        num_districts: If given, return the per-district-count subdirectory;
            otherwise return the signature's chain_out root.

    Returns:
        Path outputs/districts/chain_out/<signature>[/<num_districts>].
    """
    base = Path("outputs") / "districts" / "chain_out" / signature
    return base if num_districts is None else base / str(num_districts)


def get_district_images_dir(run_name: str, num_districts: int) -> Path:
    """
    Return (creating if needed) the directory where district-plan images for a run
    are saved: outputs/{run_name}/district_images/{num_districts}.

    Args:
        run_name: The run name from the config.
        num_districts: Number of districts in the plans being exported.

    Returns:
        Path to the (existing) image directory.
    """
    images_dir = Path(f"outputs/{run_name}/district_images/{num_districts}")
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def plot_district_plan(
    gdf,
    assignment: Iterable[int],
    *,
    title: Optional[str] = None,
    cmap: str = "tab20",
    figsize: tuple = (8, 8),
    boundary_color: str = "black",
    boundary_linewidth: float = 0.6,
):
    """
    Plot a single district plan: precincts filled by their district assignment,
    with dissolved district boundaries drawn on top for legibility.

    The assignment is positional — assignment[i] is the district of gdf row i —
    which matches the sorted-by-node assignment written by the district generator
    when the graph is built directly from this same gdf.

    Args:
        gdf: GeoDataFrame of precincts, one row per assignment entry, in the same
            row order the district graph was built from.
        assignment: Iterable of district ids, one per precinct/row.
        title: Optional title drawn above the map.
        cmap: Matplotlib categorical colormap name for the district fills.
        figsize: Figure size in inches.
        boundary_color: Color of the overlaid district boundary lines.
        boundary_linewidth: Line width of the district boundaries.

    Returns:
        Tuple (fig, ax) of the rendered matplotlib figure and axes.
    """
    import matplotlib.pyplot as plt

    plot_gdf = gdf.copy()
    plot_gdf["district"] = list(assignment)

    fig, ax = plt.subplots(figsize=figsize)
    plot_gdf.plot(column="district", categorical=True, cmap=cmap, linewidth=0, ax=ax)
    plot_gdf.dissolve(by="district").boundary.plot(
        ax=ax, color=boundary_color, linewidth=boundary_linewidth
    )

    ax.set_axis_off()
    if title:
        ax.set_title(title)
    fig.tight_layout()
    return fig, ax


def save_district_plan_png(
    gdf,
    assignment: Iterable[int],
    output_path: str | Path,
    *,
    title: Optional[str] = None,
    dpi: int = 150,
    **plot_kwargs: Any,
) -> Path:
    """
    Render a district plan (see plot_district_plan) and save it as a PNG, closing
    the figure afterward so callers can export many plans in a loop without leaking
    matplotlib figures.

    Args:
        gdf: GeoDataFrame of precincts, one row per assignment entry.
        assignment: Iterable of district ids, one per precinct/row.
        output_path: Destination PNG path; parent directories are created.
        title: Optional title drawn above the map.
        dpi: Output resolution in dots per inch.
        **plot_kwargs: Forwarded to plot_district_plan (cmap, figsize, etc.).

    Returns:
        The output Path that was written.
    """
    import matplotlib.pyplot as plt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, _ = plot_district_plan(gdf, assignment, title=title, **plot_kwargs)
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output_path


def _format_dict_inline(d: Dict[str, Any]) -> str:
    """Render a flat dict as "k1=v1, k2=v2, ..." for a single settings-box row."""
    return ", ".join(f"{k}={v}" for k, v in d.items())


def build_config_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the standard label -> value settings shown by add_config_settings_box:
    ensemble size, turnout, and one row per bloc's cohesion parameters.
    Centralized here so every visualization that displays a settings box is
    describing a given config the same way, rather than each one re-deriving
    its own subset of fields.

    Args:
        config: Parsed config dict.

    Returns:
        Ordered dict of display label -> formatted value.
    """
    settings = {
        "Ensemble size": f"{config['num_subsamples']} plans ({config['chain_length']:,}-step chain)",
        "Turnout": _format_dict_inline(config["turnout"]),
    }
    for bloc, params in config["cohesion_parameters"].items():
        settings[f"Cohesion — {bloc}"] = _format_dict_inline(params)
    return settings


def add_config_settings_box(
    fig,
    settings: Dict[str, Any],
    *,
    title: str = "Configuration",
    x: float = 0.02,
    y: float = 1.09,
    fontsize: float = 8.5,
) -> None:
    """
    Draw a small boxed panel of key/value settings in the upper-left corner of a
    figure, in one fixed style so every visualization that includes a settings
    box looks the same: a bold centered title over a left-aligned settings list,
    same font/background as the rest of the figure, black border. Draws in
    figure-fraction coordinates above the axes (y can exceed 1) -- callers need
    enough top margin (taller figsize, and/or suptitle/subtitle placed below the
    box) for it not to be clipped.

    Args:
        fig: The matplotlib Figure to annotate.
        settings: Ordered mapping of label -> display value, one row each
            (see build_config_settings).
        title: Bold header line centered above the settings rows.
        x: Figure-fraction x position of the box's left edge.
        y: Figure-fraction y position of the box's top edge.
        fontsize: Font size for the settings rows (title is one point larger).
    """
    import matplotlib.patches as mpatches

    body = "\n".join(f"{label}:  {value}" for label, value in settings.items())
    pad_x, pad_y, gap = 0.012, 0.012, 0.012

    # Proportional (non-monospace) fonts can't be centered by padding with
    # spaces the way a fixed-width grid can, so the title is measured and
    # positioned as its own artist rather than baked into the body string.
    title_text = fig.text(
        x + pad_x, y - pad_y, title,
        transform=fig.transFigure, ha="left", va="top", fontsize=fontsize + 1, fontweight="bold",
    )
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    title_bbox = title_text.get_window_extent(renderer).transformed(fig.transFigure.inverted())

    body_text = fig.text(
        x + pad_x, title_bbox.y0 - gap, body,
        transform=fig.transFigure, ha="left", va="top", fontsize=fontsize, linespacing=1.6,
    )
    fig.canvas.draw()
    body_bbox = body_text.get_window_extent(renderer).transformed(fig.transFigure.inverted())

    box_width = max(title_bbox.width, body_bbox.width) + 2 * pad_x
    box_top = y
    box_bottom = body_bbox.y0 - pad_y
    box = mpatches.FancyBboxPatch(
        (x, box_bottom), box_width, box_top - box_bottom,
        transform=fig.transFigure, boxstyle="round,pad=0.006",
        facecolor=fig.get_facecolor(), edgecolor="black", linewidth=1,
        zorder=title_text.get_zorder() - 1,
    )
    fig.add_artist(box)
    title_text.set_x(x + box_width / 2)
    title_text.set_horizontalalignment("center")


def find_settings_file(
    settings_dir: Path,
    run_name: str,
    *,
    plan: Optional[int],
    district: Optional[int],
) -> Optional[Path]:
    """
    Locate the settings json file for a given (plan, district) pair.
    tries an exact filename match first, then falls back to glob patterns,
    then returns the only file in the directory if exactly one exists.

    Args:
        settings_dir: Directory containing settings json files.
        run_name: Unused; reserved for future use in filename matching.
        plan: Plan index (zero-based sample index from the chain).
        district: District id within the plan.

    Returns:
        Path to the matching settings file, or None if not found.
    """
    if not settings_dir.exists():
        return None

    # 1) Exact match for the known generator format
    if plan is not None and district is not None:
        exact = settings_dir / f"sample_vk_sample_settings_district_plan_{plan:03d}_district_{district:02d}.json"
        if exact.exists():
            return exact

    # 2) Best-effort matching (tolerant of minor naming variations)
    patterns: List[str] = []
    if plan is not None and district is not None:
        patterns.extend([
            f"*district_plan_{plan:03d}*district_{district:02d}.json",
            f"*plan_{plan:03d}*district_{district:02d}.json",
            f"*plan*{plan}*district*{district:02d}*.json",
            f"*plan*{plan}*district*{district}*.json",
        ])
    elif plan is not None:
        patterns.extend([
            f"*district_plan_{plan:03d}*.json",
            f"*plan_{plan:03d}*.json",
            f"*plan*{plan}*.json",
        ])
    elif district is not None:
        patterns.extend([
            f"*district_{district:02d}.json",
            f"*district*{district:02d}*.json",
        ])

    for pat in patterns:
        hits = sorted(settings_dir.glob(pat))
        if hits:
            return hits[0]

    # 3) If there is exactly one file, return it (useful for quick debugging)
    all_files = sorted(settings_dir.glob("*.json"))
    if len(all_files) == 1:
        return all_files[0]
    return None