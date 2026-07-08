"""
Generate VoteKit settings files from sampled district plans.

Reads district assignments produced by the district-generation step,
aggregates population counts by district, computes turnout-adjusted
bloc proportions, and writes one settings JSON file per sampled plan
and district.
"""

import json
import gzip
import geopandas as gpd
from pathlib import Path
import jsonlines as jl
from tqdm import tqdm

# Default mapping from bloc label -> VAP column in the geodata (matches the
# schema written by data_generator). Override per-run with a "group_vap_columns"
# entry in the config if your blocs or column names differ.
DEFAULT_GROUP_VAP_COLUMNS = {
    "W": "white_vap_20",
    "B": "bvap_20",
    "H": "hvap_20",
    "A": "asian_nhpi_vap_20",
}


def get_group_vap_columns(config):
    """
    Return the {bloc: vap_column} mapping for every bloc modeled in this run.

    The set of blocs is taken from slate_to_candidates (the blocs VoteKit will
    build profiles for). Columns come from config["group_vap_columns"] when
    present, otherwise DEFAULT_GROUP_VAP_COLUMNS.

    Args:
        config: Parsed config dict.

    Returns:
        Dict mapping each bloc label to its VAP column name.
    """
    mapping = config.get("group_vap_columns", DEFAULT_GROUP_VAP_COLUMNS)
    blocs = list(config["slate_to_candidates"].keys())
    missing = [g for g in blocs if g not in mapping]
    if missing:
        raise KeyError(
            f"No VAP column mapping for bloc(s) {missing}. Add them to "
            "'group_vap_columns' in the config or to DEFAULT_GROUP_VAP_COLUMNS."
        )
    return {g: mapping[g] for g in blocs}


def _build_district_settings(row, config, group_columns):
    """
    Compute turnout-adjusted bloc proportions and population values for a district.

    Every bloc in group_columns gets a proportion: its share of the modeled VAP,
    weighted by turnout and normalized so the proportions sum to 1. This is the
    N-bloc generalization of the original two-bloc turnout adjustment.

    Args:
        row: Row from the district population dataframe.
        config: Parsed config dict.
        group_columns: Dict mapping each bloc label to its VAP column name.

    Returns:
        Dict containing bloc_proportions (one entry per bloc) and per-bloc plus
        total VAP counts for the district.
    """
    turnout = config['turnout']
    blocs = list(group_columns)

    # Turnout-weighted VAP per bloc, then normalize across the modeled blocs.
    weighted = {g: float(row[group_columns[g]]) * turnout[g] for g in blocs}
    denom = sum(weighted.values())
    if denom > 0:
        bloc_proportions = {g: weighted[g] / denom for g in blocs}
    else:
        # District with no modeled VAP: fall back to equal shares.
        bloc_proportions = {g: 1.0 / len(blocs) for g in blocs}

    settings = {"bloc_proportions": bloc_proportions}
    for g in blocs:
        settings[group_columns[g]] = float(row[group_columns[g]])
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
    """
    group_columns = get_group_vap_columns(config)

    population_data = gpd.read_file(config['geodata_path'])
    needed_columns = list(dict.fromkeys(
        list(group_columns.values()) + [config['population_vap_column']]
    ))
    population_data = population_data[needed_columns]

    # subsample evenly spaced plans from the chain
    chain_length = config['chain_length']
    num_subsamples = config['num_subsamples']
    subsample_interval = chain_length // num_subsamples   

    # pull only the relevant keys from config to pass downstream
    district_params = ['num_voters', 'slate_to_candidates', 'cohesion_parameters', 'alphas']
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
                    district_settings = _build_district_settings(row, config, group_columns)
                    settings = output_settings | district_settings
                    with open(
                        f"{settings_folder}/{run_name}_{district_num}_sample_settings_district_plan_{sample_idx:03d}_district_{district:02d}.json",
                        "w",
                    ) as out_file:
                        json.dump(settings, out_file, indent=2)