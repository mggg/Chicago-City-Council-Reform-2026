"""
Generate voter preference profiles from district-level settings files.

Reads VoteKit settings JSON files, generates synthetic voter profiles for
each district, voter model, and replicate, and bundles the resulting profiles
into a single zip archive per run for downstream election simulations.
"""

from votekit.ballot_generator import (
    BlocSlateConfig,
    slate_pl_profile_generator,
    slate_bt_profile_generator,
    cambridge_profile_generator,
)

from glob import glob
from joblib import Parallel, delayed
from joblib_progress import joblib_progress
from pathlib import Path
from pipeline.utils.helpers import load_json, get_voter_models
import json
import time
import zipfile

# maps mode name to votekit profile generator function. slate_bt uses the MCMC
# sampler (O(voters), no ballot-type enumeration) — far faster than the exact
# generator, at the cost of an approximate ballot-type distribution.
generator_name_to_function = {
    "slate_pl": slate_pl_profile_generator,
    "slate_bt": slate_bt_profile_generator,
    "cambridge": cambridge_profile_generator,
}

def process_settings_file(settings_file, mode, duplicate_indx):
    """
    Generate a voter profile for a single district using the given voter model.

    Runs entirely in memory (no filesystem write) so it can be called from a
    parallel worker and have its result written into the run's shared zip
    archive by the caller, avoiding concurrent writes to one zip file.

    Args:
        settings_file: Path to a votekit settings json file for one district.
        mode: Voter model name; one of "slate_pl", "slate_bt", or "cambridge".
        duplicate_indx: Replicate index, appended as _v<n> in the output filename.

    Returns:
        (filename, csv_text): filename is the settings file's stem with
        "sample_settings" replaced by "profile" and "_v<duplicate_indx>.csv"
        appended; csv_text is the profile's CSV content (per votekit's
        PreferenceProfile.to_csv()).
    """
    settings = load_json(settings_file)

    config = BlocSlateConfig(
        n_voters = settings['num_voters'],
        slate_to_candidates=settings["slate_to_candidates"],
        bloc_proportions=settings["bloc_proportions"],
        cohesion_mapping=settings["cohesion_parameters"],
    )

    config.set_dirichlet_alphas(settings["alphas"])
    setting_file_stem = Path(settings_file).stem

    filename = f"{setting_file_stem.replace('sample_settings', 'profile')}_v{duplicate_indx}.csv"
    profile = generator_name_to_function[mode](config)
    csv_text = profile.to_csv()

    return filename, csv_text


def generate_profiles(config):
    """
    Generate voter profiles for all districts, modes, and replicates in the config,
    bundling them into a single zip archive per run.

    Args:
        config: Parsed config dict.

    Outputs:
        outputs/<run_name>/profiles.zip, containing one csv entry per
        (mode, district_num, settings file, replicate) at
        "<mode>/<district_num>/<...>_v<duplicate_indx>.csv".
    """

    num_reps = config['num_reps']
    run_name = config['run_name']

    models = get_voter_models(config)
    unknown = [m for m in models if m not in generator_name_to_function]
    if unknown:
        raise ValueError(
            f"Unknown voter_models {unknown}. Valid models: "
            f"{sorted(generator_name_to_function)}."
        )
    if "cambridge" in models and len(config["slate_to_candidates"]) != 2:
        raise ValueError(
            "The 'cambridge' model supports exactly 2 slates, but this run has "
            f"{len(config['slate_to_candidates'])}. Remove 'cambridge' from "
            "voter_models or reduce to 2 slates."
        )

    zip_path = Path(f"outputs/{run_name}/profiles.zip")
    zip_path.parent.mkdir(exist_ok=True, parents=True)

    # Opened once for the whole run: workers only compute (filename, csv_text)
    # pairs in parallel, and every actual write to the shared archive happens
    # here, sequentially, in the main process.
    #
    # return_as="generator_unordered" makes Parallel yield each worker's result
    # as soon as it's ready, instead of collecting the whole batch (up to
    # num_districts x num_subsamples settings files, e.g. 2,500 for a 50x1
    # config) into memory before any of it is written. Peak memory is bounded
    # by whatever's in flight across the worker pool, not the full batch size,
    # so a handful of unusually large profiles (e.g. a district whose slates
    # are heavily concentrated onto one demographic group) no longer forces
    # the entire batch to be held in memory at once.
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # repeat for each replicate
        for duplicate_indx in range(num_reps):
            rep_start = time.perf_counter()
            print(f"[rep {duplicate_indx + 1}/{num_reps}] Start at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            district_nums =  [d_config['num_districts'] for d_config in config['district_configs']]
            for district_num in district_nums:
                for mode in models:
                    settings_folder = Path(f"outputs/{run_name}/settings/{district_num}")
                    all_settings_files = glob(f"{settings_folder}/*.json")

                    with joblib_progress(
                        description=f"[rep {duplicate_indx + 1:03d}/{num_reps}] Generating VK profiles for {district_num:02d} districts and voter model {mode}",
                        total=len(all_settings_files),
                    ):
                        results = Parallel(n_jobs=-1, return_as="generator_unordered")(
                            delayed(process_settings_file)(settings_file, mode, duplicate_indx)
                            for settings_file in all_settings_files
                        )

                        for filename, csv_text in results:
                            archive.writestr(f"{mode}/{district_num}/{filename}", csv_text)
            rep_elapsed = time.perf_counter() - rep_start
            print(f"[rep {duplicate_indx + 1}/{num_reps}] Done in {rep_elapsed:.1f}s")

