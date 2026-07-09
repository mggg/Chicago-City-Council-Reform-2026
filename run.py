"""
Run pipeline step by step.
"""
from pathlib import Path
from glob import glob
import json
import sys
import gzip
from pipeline.district_generator import generate_districts
from pipeline.settings_generator import generate_settings
from pipeline.profile_generator import generate_profiles
from pipeline.simulate_elections import simulate_elections
from pipeline.summarize_results import summarize_results, plot_combined_bubbles_all_runs
from pipeline.data_generator import generate_data
from pipeline.summarize_results import summarize_results
from pipeline.utils.profiling import profile_stage, print_profile_summary
from pipeline.utils.helpers import get_voter_models

def load_all_config_files(config_dir="configs"):
    all_config_files = [load_config(path) for path in glob(f"{config_dir}/*.json")]
    return all_config_files


def load_config(config_path: str) -> dict:
    """Load config from JSON file."""
    with open(config_path) as f:
        return json.load(f)


def has_valid_district_outputs(config) -> bool:
    run = config["run_name"]
    n = config["chain_length"]
    n_district = config["district_configs"][0]["num_districts"]
    base = Path("outputs") / "districts" / "chain_out" / f"{n_district}"


    if not base.is_dir():
        print("District files do not exist. Running entire pipeline.")
        return False
    
    metadata_file = base / f"{n_district}_districts.json"
    
    if not metadata_file.exists():
        return False
    
    with open(metadata_file) as f:
        config_md = json.load(f)

    keys = [
        "geodata_path",
        "population_column",
        "chain_length",
        "epsilon",
        "seed",
    ]

    if any(config[k] != config_md[k] for k in keys):
        return False
    
    for d in config["district_configs"]:
        f = base / f"{d['num_districts']}_districts.jsonl.gz"
        if not f.is_file():
            print(f"{d['num_districts']} district configuration files do not exist. Running entire pipeline.")
            return False
        try:
            with gzip.open(f, "rt", encoding="utf-8") as g:
                if sum(1 for _ in g) != n:
                    print("Incomplete districting file. Running entire pipeline.")
                    return False
        except Exception:
            return False
    return True

def has_valid_settings(config):
    run = config["run_name"]
    base = Path("outputs") / run / "settings"
    if not base.is_dir():
        print("Settings do not exist. Running pipeline from settings stage.")
        return False
    district_nums = [d["num_districts"] for d in config["district_configs"]]
    for num_districts in district_nums:
        count = sum(1 for f in (base / str(num_districts)).rglob("*.json") if f.stat().st_size > 0)
        expected_per_num_district = config["num_subsamples"] * num_districts
        if count != expected_per_num_district:
            print(f"Missing valid settings for {num_districts} districts. Running pipeline from settings stage.")
            return False
    return True

def has_valid_profiles(config):
    run = config["run_name"]
    base = Path("outputs") / run / "profiles"
    if not base.is_dir():
        print("Profiles do not exist. Running pipeline from profiles stage.")
        return False
    expected_per_mode = (
        config["num_subsamples"]
        * sum(d["num_districts"] for d in config["district_configs"])
        * config["num_reps"]
    )
    for mode in get_voter_models(config):
        count = sum(1 for f in (base / mode).rglob("*.csv.gz") if f.stat().st_size > 0)
        if count != expected_per_mode:
            print(f"Missing valid settings for {mode} mode. Running pipeline from profiles stage.")
            return False
    return True

def has_valid_election_results(config):
    run = config["run_name"]
    base = Path("outputs") / run / "election_results"
    if not base.is_dir():
        print("Election results do not exist. Running pipeline from election simulation stage.")
        return False
    for mode in get_voter_models(config):
        mode_dir = base / mode
        if not mode_dir.is_dir():
            print(f"Election results for {mode} mode do not exist. Running pipeline from election simulation stage.")
            return False
        for d in config["district_configs"]:
            n = d["num_districts"]
            files = list(mode_dir.glob(f"{run}_{n}_districts_*_voter_mode_{mode}.json"))
            if len(files) != 1:
                print(f"Election results for {mode} mode and {d} number of districts do not exist. Running pipeline from election simulation stage.")
                return False
            try:
                with open(files[0], "r", encoding="utf-8") as f:
                    data = json.load(f)
                expected_len = config["num_subsamples"] * n * config["num_reps"]
                if len(data.get("profile_files", [])) != expected_len:
                    print(f"Election results for {mode} mode and {d} number of districts have incorrect length. Running pipeline from election simulation stage.")
                    return False
            except Exception:
                return False
    return True

def has_valid_summaries(config):
    run = config["run_name"]
    base = Path("outputs") / run / "summaries"
    figs = base / "figures"
    csv = base / f"{run}_summary.csv"
    if not base.is_dir() or not figs.is_dir() or not csv.is_file():
        print("Summaries do not exist. Running pipeline from summary stage.")
        return False
    expected_figs = sum(2 if d["winners"] == 1 else 1 for d in config["district_configs"])
    actual_figs = sum(1 for _ in figs.glob("*.png"))
    if actual_figs != expected_figs:
        print("Incorrect number of figures.")
    return actual_figs == expected_figs

def pipeline(config):

    print("==============================================\n")
    print(f"Run name: {config["run_name"]}")
    print(f"Districts: {config['district_configs']}")
    print(f"Chain length: {config['chain_length']}")
    print("==============================================\n")

    run_name = config["run_name"]

    # Generate District Map Ensemble
    print("=== Generating District Map Ensemble ===")
    with profile_stage("Generate District Ensemble", run_name):
        generate_districts(config)

    # Generate Settings Files
    print("=== Generating District Settings Files ===")
    with profile_stage("Generate Settings", run_name):
        generate_settings(config)

    # Generate Profiles
    print("=== Generating Preference Profiles ===")
    with profile_stage("Generate Profiles", run_name):
        generate_profiles(config)

    # Simulate Elections
    print("=== Simulating Elections ===")
    with profile_stage("Simulate Elections", run_name):
        simulate_elections(config)

    # Results Summary
    print("=== Summarizing Results and Visualizations ===")
    with profile_stage("Summarize Results", run_name):
        summarize_results(config)


def run_pipeline(config):

    run_name = config["run_name"]
    run_dir = Path("outputs") / run_name
   
    if has_valid_district_outputs(config):
        print("[District generator] District MC already exist. Omitting District generator.")
        if has_valid_settings(config):
            if has_valid_profiles(config):
                if has_valid_election_results(config):
                    with profile_stage("Summarize Results", run_name):
                        summarize_results(config)
                else:
                    with profile_stage("Simulate Elections", run_name):
                        simulate_elections(config)
                    with profile_stage("Summarize Results", run_name):
                        summarize_results(config)
            else:
                with profile_stage("Generate Profiles", run_name):
                    generate_profiles(config)
                with profile_stage("Simulate Elections", run_name):
                    simulate_elections(config)
                with profile_stage("Summarize Results", run_name):
                    summarize_results(config)
        else:
            with profile_stage("Generate Settings", run_name):
                generate_settings(config)
            with profile_stage("Generate Profiles", run_name):
                generate_profiles(config)
            with profile_stage("Simulate Elections", run_name):
                simulate_elections(config)
            with profile_stage("Summarize Results", run_name):
                summarize_results(config)
    else:
        print("District files do not exist. Running entire pipeline.")
        pipeline(config)
 

    print_profile_summary(run_name)


def main():
    configurations = load_all_config_files(config_dir="configs")

    # Create GPKG and Graph Files
    print("==== Generating GPKG and Graph Data ===")
    generate_data()

    # Run pipeline for all configurations in configs/
    for config in configurations:
        run_pipeline(config)

    plot_combined_bubbles_all_runs(config)

if __name__ == "__main__":
    
    # Kick off our program
    main()