"""
Generate VoteKit settings files from sampled district plans.

Reads district assignments produced by the district-generation step,
aggregates population counts by district, computes turnout-adjusted
bloc proportions, and writes one settings JSON file per sampled plan
and district.
"""

import json
import gzip
import random
import geopandas as gpd
from pathlib import Path
import jsonlines as jl
from tqdm import tqdm

# Default mapping from demographic-group label -> VAP column in the geodata
# (matches the schema written by data_generator). Override per-run with a
# "group_vap_columns" entry in the config if your groups or column names differ.
DEFAULT_GROUP_VAP_COLUMNS = {
    "W": "white_vap_20",
    "B": "bvap_20",
    "H": "hvap_20",
    "A": "asian_nhpi_vap_20",
}


def get_bloc_definitions(config):
    """
    Return {bloc: [demographic_group, ...]} defining which demographic groups
    each voter bloc aggregates.

    Voter blocs (who votes) and candidate slates (who runs) are independent axes.
    By default every slate gets a matching single-group bloc of the same name
    (the original blocs == slates behavior). Set a "blocs" entry in the config to
    combine demographic groups into one bloc, e.g.
    {"W-A": ["W", "A"], "B": ["B"], "H": ["H"]} — a 3-bloc electorate that still
    faces the 4 slates in slate_to_candidates.

    Args:
        config: Parsed config dict.

    Returns:
        Dict mapping each bloc label to the list of demographic groups it covers.
    """
    if "blocs" in config:
        return {bloc: list(groups) for bloc, groups in config["blocs"].items()}
    return {slate: [slate] for slate in config["slate_to_candidates"].keys()}


def get_group_vap_columns(config, demographic_groups):
    """
    Return the {demographic_group: vap_column} mapping for the given groups.

    Columns come from config["group_vap_columns"] when present, otherwise
    DEFAULT_GROUP_VAP_COLUMNS.

    Args:
        config: Parsed config dict.
        demographic_groups: Iterable of demographic-group labels to resolve.

    Returns:
        Dict mapping each demographic group to its VAP column name.
    """
    mapping = config.get("group_vap_columns", DEFAULT_GROUP_VAP_COLUMNS)
    missing = [g for g in demographic_groups if g not in mapping]
    if missing:
        raise KeyError(
            f"No VAP column mapping for demographic group(s) {missing}. Add them "
            "to 'group_vap_columns' in the config or to DEFAULT_GROUP_VAP_COLUMNS."
        )
    return {g: mapping[g] for g in demographic_groups}


def _validate_bloc_config(config, bloc_definitions):
    """
    Check turnout, cohesion_parameters, and alphas are keyed consistently with the
    blocs (rows) and slates (columns) this run uses.

    Raises:
        KeyError: with a specific message if any bloc or slate entry is missing.
    """
    blocs = set(bloc_definitions)
    slates = set(config["slate_to_candidates"])

    missing_turnout = blocs - set(config["turnout"])
    if missing_turnout:
        raise KeyError(f"turnout is missing entries for bloc(s) {sorted(missing_turnout)}.")

    for name in ("cohesion_parameters", "alphas"):
        matrix = config[name]
        missing_rows = blocs - set(matrix)
        if missing_rows:
            raise KeyError(f"{name} is missing row(s) for bloc(s) {sorted(missing_rows)}.")
        for bloc in blocs:
            missing_cols = slates - set(matrix[bloc])
            if missing_cols:
                raise KeyError(
                    f"{name}['{bloc}'] is missing column(s) for slate(s) {sorted(missing_cols)}."
                )


def _apportion_counts(proportions, total):
    """
    Apportion `total` whole units across the keys of `proportions` in proportion
    to their shares, using the largest-remainder (Hamilton) method.

    A key whose share is small enough relative to the others may be apportioned
    zero units — there is no guaranteed floor. Ties in the remainder are broken
    by dict order, which is insertion order and therefore deterministic given a
    fixed config.

    Args:
        proportions: Dict mapping key -> share of the total (sums to ~1).
        total: Total whole units to distribute; must be >= 0.

    Returns:
        Dict mapping each key to its apportioned integer count (some may be 0),
        summing to total.
    """
    if total < 0:
        raise ValueError(f"total_candidates ({total}) must be non-negative.")

    keys = list(proportions)
    quotas = {k: proportions[k] * total for k in keys}
    counts = {k: int(quotas[k]) for k in keys}

    leftover = total - sum(counts.values())
    remainder_order = sorted(keys, key=lambda k: quotas[k] - counts[k], reverse=True)
    for k in remainder_order[:leftover]:
        counts[k] += 1

    return counts


def _apply_candidate_noise(counts, noise_probability):
    """
    Independently perturb each slate's candidate count by at most one candidate.

    For each slate: with probability `noise_probability`, remove one candidate;
    with probability `noise_probability`, add one candidate; otherwise (probability
    1 - 2 * noise_probability) leave the count unchanged. Counts are clamped at 0.

    Args:
        counts: Dict mapping slate -> apportioned candidate count.
        noise_probability: Float in [0, 0.5]. 0 disables noise entirely.

    Returns:
        Dict mapping each slate to its (possibly perturbed) count.
    """
    if noise_probability == 0:
        return dict(counts)

    if not (0 <= noise_probability <= 0.5):
        raise ValueError(
            f"candidate_noise_probability ({noise_probability}) must be between 0 and 0.5."
        )

    perturbed = {}
    for slate, count in counts.items():
        roll = random.random()
        if roll < noise_probability:
            delta = -1
        elif roll < 2 * noise_probability:
            delta = 1
        else:
            delta = 0
        perturbed[slate] = max(0, count + delta)
    return perturbed


def _build_slate_to_candidates(row, slate_columns, total_candidates, noise_probability=0):
    """
    Build a district-specific slate_to_candidates mapping sized proportionally
    to each slate's share of modeled VAP in this district.

    Slates apportioned zero candidates (whether from the base apportionment or
    from noise knocking a count to 0) are omitted entirely — VoteKit's
    BlocSlateConfig rejects a slate with an empty candidate list, so a slate
    with negligible population share simply doesn't run in that district.

    Args:
        row: Row from the district population dataframe.
        slate_columns: Dict mapping each slate to its VAP column name.
        total_candidates: Total number of candidates to distribute across slates
            before noise is applied.
        noise_probability: Float in [0, 0.5] giving the independent per-slate
            probability of adding or removing one candidate after apportionment.
            Defaults to 0 (no noise).

    Returns:
        Dict mapping each slate with a nonzero (post-noise) count to a list of
        candidate ids, e.g. {"W": ["W1", "W2"]}.
    """
    slates = list(slate_columns)
    weighted = {s: float(row[slate_columns[s]]) for s in slates}
    denom = sum(weighted.values())
    if denom > 0:
        proportions = {s: weighted[s] / denom for s in slates}
    else:
        proportions = {s: 1.0 / len(slates) for s in slates}

    counts = _apportion_counts(proportions, total_candidates)
    counts = _apply_candidate_noise(counts, noise_probability)
    return {
        s: [f"{s}{i}" for i in range(1, counts[s] + 1)]
        for s in slates
        if counts[s] > 0
    }


def _filter_cohesion_to_slates(cohesion_parameters, active_slates):
    """
    Restrict each bloc's cohesion row to the active slates and renormalize so
    each row still sums to 1.

    Dropping a slate from a district removes the candidate-facing column
    entirely (VoteKit requires cohesion_df columns to match slate_to_candidates
    exactly), so the cohesion mass a bloc had assigned to the dropped slate is
    redistributed proportionally across the slates still running.

    Args:
        cohesion_parameters: Dict mapping bloc -> {slate: cohesion value}.
        active_slates: Iterable of slate labels with a nonzero candidate count.

    Returns:
        Dict with the same bloc keys, each row restricted to active_slates and
        renormalized to sum to 1.

    Raises:
        ValueError: If a bloc's cohesion mass is entirely on dropped slates,
            leaving nothing to renormalize.
    """
    active_slates = list(active_slates)
    result = {}
    for bloc, row in cohesion_parameters.items():
        restricted = {s: row[s] for s in active_slates}
        total = sum(restricted.values())
        if total <= 0:
            raise ValueError(
                f"cohesion_parameters['{bloc}'] has no remaining mass once slates "
                f"outside {active_slates} are dropped; cannot renormalize."
            )
        result[bloc] = {s: v / total for s, v in restricted.items()}
    return result


def _filter_alphas_to_slates(alphas, active_slates):
    """
    Restrict each bloc's Dirichlet alpha row to the active slates.

    Unlike cohesion parameters, alphas aren't required to sum to 1, so dropped
    slates are simply removed with no renormalization needed.

    Args:
        alphas: Dict mapping bloc -> {slate: alpha value}.
        active_slates: Iterable of slate labels with a nonzero candidate count.

    Returns:
        Dict with the same bloc keys, each row restricted to active_slates.
    """
    active_slates = list(active_slates)
    return {bloc: {s: row[s] for s in active_slates} for bloc, row in alphas.items()}


def _build_district_settings(row, config, group_columns, bloc_definitions):
    """
    Compute turnout-adjusted bloc proportions and population values for a district.

    Each voter bloc's weight is its turnout times the summed VAP of the
    demographic groups it aggregates; weights are normalized so the proportions
    sum to 1. Blocs and slates are independent — bloc_definitions says which
    demographic groups make up each bloc, so a "W-A" bloc sums White + Asian VAP.

    Args:
        row: Row from the district population dataframe.
        config: Parsed config dict.
        group_columns: Dict mapping each demographic group to its VAP column name.
        bloc_definitions: Dict mapping each bloc to its demographic groups.

    Returns:
        Dict containing bloc_proportions (one entry per bloc) and per-group plus
        total VAP counts for the district.
    """
    turnout = config['turnout']
    blocs = list(bloc_definitions)

    # Turnout-weighted VAP per bloc (sum over the bloc's demographic groups),
    # then normalize across the blocs.
    weighted = {
        bloc: turnout[bloc] * sum(float(row[group_columns[g]]) for g in bloc_definitions[bloc])
        for bloc in blocs
    }
    denom = sum(weighted.values())
    if denom > 0:
        bloc_proportions = {bloc: weighted[bloc] / denom for bloc in blocs}
    else:
        # District with no modeled VAP: fall back to equal shares.
        bloc_proportions = {bloc: 1.0 / len(blocs) for bloc in blocs}

    settings = {"bloc_proportions": bloc_proportions}
    # Record the raw per-demographic-group VAP counts that fed the proportions.
    for col in dict.fromkeys(group_columns.values()):
        settings[col] = float(row[col])
    settings[config["population_vap_column"]] = float(row[config["population_vap_column"]])
    return settings

def generate_settings(config):
    """
    For each sampled district plan, compute per-district bloc proportions and write
    votekit settings json files.

    Args:
        config: Parsed config dict.

    Outputs:
        One json settings file per (district count, sampled plan, district) triple at
        outputs/settings/<run_name>_settings/<district_count>/<run_name>_<district_count>_sample_settings_district_plan_<plan_idx>_district_<district_id>.json.
        where <plan_idx> is the zero-based chain sample index and <district_id> is the district label.
        bloc_proportions in each file are turnout-adjusted focal group proportions.
        slate_to_candidates in each file is resized per-district: total_candidates (default:
        the candidate count from config's slate_to_candidates) is apportioned across slates
        in proportion to each slate's share of modeled VAP in that district, then perturbed
        by candidate_noise_probability (default 0). A slate whose count reaches zero (from
        apportionment or noise) is dropped from slate_to_candidates, and its column is
        dropped (with cohesion_parameters renormalized) from that district's
        cohesion_parameters and alphas.
    """
    random.seed(config["seed"])
    noise_probability = config.get("candidate_noise_probability", 0)

    bloc_definitions = get_bloc_definitions(config)
    _validate_bloc_config(config, bloc_definitions)
    # The demographic groups we need VAP for are the union across all blocs.
    demographic_groups = list(dict.fromkeys(
        g for groups in bloc_definitions.values() for g in groups
    ))
    group_columns = get_group_vap_columns(config, demographic_groups)
    slate_columns = get_group_vap_columns(config, config["slate_to_candidates"].keys())
    total_candidates = config.get(
        "total_candidates",
        sum(len(v) for v in config["slate_to_candidates"].values()),
    )

    population_data = gpd.read_file(config['geodata_path'])
    needed_columns = list(dict.fromkeys(
        list(group_columns.values()) + list(slate_columns.values()) + [config['population_vap_column']]
    ))
    population_data = population_data[needed_columns]

    # subsample evenly spaced plans from the chain
    chain_length = config['chain_length']
    num_subsamples = config['num_subsamples']
    subsample_interval = chain_length // num_subsamples   

    # pull only the relevant keys from config to pass downstream
    # (slate_to_candidates, cohesion_parameters, and alphas are computed
    # per-district below, not passed through as-is)
    district_params = ['num_voters']
    output_settings = {k:config[k] for k in config if k in district_params}
    run_name = config['run_name']

    for district_num in [d_config['num_districts'] for d_config in config['district_configs']]:
        settings_folder = Path(f'outputs/{run_name}/settings/{district_num}')
        settings_folder.mkdir(exist_ok=True, parents=True)

        path_to_districting = Path(f'outputs/districts/chain_out/{district_num}/{district_num}_districts.jsonl.gz')
        
        with gzip.open(path_to_districting, mode="rt", encoding="utf-8") as gz_file:
            file = jl.Reader(gz_file)
            for sample_idx, sample in tqdm(
                enumerate(file),
                total=chain_length,
                desc=f"Generating VK settings for {district_num:02d} districts",
            ):
                if sample_idx % subsample_interval != 0:
                    continue

                district_plan = sample["assignment"]
                population_data["district_plan"] = district_plan
                data_by_district = population_data.groupby("district_plan").sum()

                for _, row in data_by_district.iterrows():
                    district = row.name
                    district_settings = _build_district_settings(row, config, group_columns, bloc_definitions)
                    slate_to_candidates = _build_slate_to_candidates(
                        row, slate_columns, total_candidates, noise_probability
                    )
                    active_slates = list(slate_to_candidates)
                    cohesion_parameters = _filter_cohesion_to_slates(config["cohesion_parameters"], active_slates)
                    alphas = _filter_alphas_to_slates(config["alphas"], active_slates)
                    settings = output_settings | district_settings | {
                        "slate_to_candidates": slate_to_candidates,
                        "cohesion_parameters": cohesion_parameters,
                        "alphas": alphas,
                    }
                    with open(
                        f"{settings_folder}/{run_name}_{district_num}_sample_settings_district_plan_{sample_idx:03d}_district_{district:02d}.json",
                        "w",
                    ) as out_file:
                        json.dump(settings, out_file, indent=2)