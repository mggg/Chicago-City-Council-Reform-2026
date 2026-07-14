"""
Run elections on generated voter profiles and record the winners.

Reads voter profiles bundled in the run's profiles.zip archive and runs the
appropriate election rule (STV for multi-seat districts, plurality and IRV for
single-seat districts), and writes aggregated election results to JSON files.
"""

import json
import zipfile
import csv
import os
import inspect
import tempfile
from pathlib import Path
from joblib import Parallel, delayed
from votekit import RankProfile, ScoreProfile, elections
from typing import List, Iterable, Any, get_args
from dataclasses import dataclass
from pipeline.utils.helpers import get_voter_models



# Optional progress bar for joblib.
try:
    from joblib_progress import joblib_progress 
except Exception: 
    joblib_progress = None 


@dataclass(frozen=True)
class DistrictConfig:
    """One district configuration: number of districts and seats won per district."""
    num_districts: int
    winners: int


def _required_profile(cls):
    annotation = inspect.signature(cls.__init__).parameters["profile"].annotation
    expected_types = get_args(annotation)
    return expected_types if expected_types else (annotation,)  # tuple of acceptable profile types

def _import_voting_rules_from_vote_kit(rules: str) -> dict:
    classes = {rule: getattr(elections, rule) for rule in rules}
    return classes


def _build_election_plan(voting_configs: dict) -> List[tuple]:
    """
    Resolve each configured voting rule to its VoteKit election class and the
    profile class it requires, once.

    This work only depends on voting_configs (not on any profile), so doing it a
    single time up front avoids repeating class lookups and signature
    introspection for every profile file.

    Args:
        voting_configs: Election and voting settings from the config file.

    Returns:
        List of (rule, election_class, profile_class) tuples in config order.
    """
    plan: List[tuple] = []
    for rule, election_class in _import_voting_rules_from_vote_kit(voting_configs.keys()).items():
        profile_types = _required_profile(election_class)
        profile_class = RankProfile if RankProfile in profile_types else ScoreProfile
        plan.append((rule, election_class, profile_class))
    return plan


def _candidate_list_from_elected(elected: Iterable[set]) -> List[str]:
    """
    Flatten votekit election output (iterable of singleton sets) into a list of strings.

    Args:
        elected: Iterable of singleton sets, as returned by votekit election methods.

    Returns:
        List of candidate id strings in election order. Empty sets are skipped silently.
    """
    winners: List[str] = []
    for s in elected:
        if s:
            winners.append(str(next(iter(s))))
    return winners

def _load_profile_from_zip(zip_path: str | Path, member_name: str, profile_class):
    """
    Extract a single profile csv from the run's profiles.zip and load it into the
    given profile class.

    Neither RankProfile nor ScoreProfile can read a zip member (or any in-memory
    stream) directly, so we extract to a unique temp file and delete it after
    loading. Using NamedTemporaryFile avoids collisions between parallel workers
    processing different profiles concurrently; each worker opens its own ZipFile
    handle onto the same archive on disk, which is safe for concurrent reads.

    Args:
        zip_path: Path to the run's profiles.zip archive.
        member_name: Name of the profile csv entry within the archive.
        profile_class: RankProfile or ScoreProfile.

    Returns:
        The loaded profile instance.
    """
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as tmp:
        temp_path = Path(tmp.name)
        with zipfile.ZipFile(zip_path) as archive:
            with archive.open(member_name) as infile:
                tmp.write(infile.read())

    try:
        return profile_class.from_csv(temp_path)
    finally:
        os.remove(temp_path)


def _process_profile(
    zip_path: str | Path,
    member_name: str,
    election_plan: List[tuple],
    voting_configs: dict,
) -> dict:
    """
    Load a voter profile csv from the run's profiles.zip and run each configured
    election to determine winners.

    Args:
        zip_path: Path to the run's profiles.zip archive.
        member_name: Name of the profile csv entry within the archive.
        election_plan: Precomputed (rule, election_class, profile_class) tuples
            from _build_election_plan; avoids per-file class lookup/introspection.
        voting_configs: Election and voting settings specified in configuration files.

    Returns:
        {[type]: [winner_ids]} e.g. { "stv": ["A2", "B1", "B3"] }

    TO-DO: Figure out how to use RankProfile OR ScoreProfile for BlockPlurality if desired.
        Current default is RankProfile.
    """
    results = {}

    # Parse each distinct profile type from the csv at most once and reuse it
    # across rules that need it (e.g. IRV and Plurality both use RankProfile),
    # instead of re-reading the same file per rule.
    profile_cache: dict = {}

    for rule, election_class, profile_class in election_plan:
        profile = profile_cache.get(profile_class)
        if profile is None:
            profile = _load_profile_from_zip(zip_path, member_name, profile_class)
            profile_cache[profile_class] = profile

        # The parameters used in the class constructors are specified in the
        # configuration files, under voting_configs. We use keyword argument spreading
        # to give us flexibility in execution

        elected = election_class(profile, **voting_configs[rule]).get_elected()
        results[rule] = _candidate_list_from_elected(elected)

    return results

def _parse_district_configs(raw: Any) -> List[DistrictConfig]:
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


def simulate_elections(config) -> None:
    """
    Run elections in parallel over all voter profiles.

    Args:
        config: Parsed config dict.

    Outputs:
        One json file per (mode, district_count, winners) combination at
        outputs/election_results/<run_name>_election_results/<mode>/
        <run_name>_<n>_districts_<w>_winners_for_voter_mode_<mode>.json.
        Each file contains a "election_results" list where each entry corresponds
        to one profile file:
          - multi-seat: {"stv": [...]}
          - single-seat: {"plurality": [...], "irv": [...]}

    Returns:
        None.
    """
    run_name = str(config["run_name"])
    district_configs = _parse_district_configs(config["district_configs"])

    # Using our voting rule configs, create an election plan that includes
    # all voting rules included within the config
    election_plan = _build_election_plan(config["voting_configs"])

    modes = get_voter_models(config)

    # Use all available cores by default. Set SIMULATE_ELECTIONS_N_JOBS=1 to run
    # serially in the main process so breakpoints inside _process_profile are hit
    # under the debugger (joblib worker subprocesses are not debugged otherwise).
    n_jobs = -1

    out_root = Path("outputs") / f'{run_name}' / "election_results"
    out_root.mkdir(parents=True, exist_ok=True)

    zip_path = Path(f"outputs/{run_name}/profiles.zip")
    with zipfile.ZipFile(zip_path) as archive:
        all_members = archive.namelist()

    # run elections for each voter model
    for mode in modes:
        output_dir = out_root / mode
        output_dir.mkdir(parents=True, exist_ok=True)

        for dc in district_configs:
            prefix = f"{mode}/{dc.num_districts}/"
            all_profile_files = [n for n in all_members if n.startswith(prefix) and n.endswith(".csv")]

            desc = f"Running elections for {dc.num_districts} districts, {dc.winners} winner(s), mode={mode}"
            if joblib_progress is not None:
                ctx = joblib_progress(description=desc, total=len(all_profile_files))
            else:
                ctx = None

            if ctx is not None:
                with ctx:
                    results_list = Parallel(n_jobs=n_jobs)(
                        delayed(_process_profile)(zip_path, pf, election_plan, config["voting_configs"]) for pf in all_profile_files
                    )

            else:
                print(f"[simulate_elections] {desc} (no joblib_progress installed)")
                results_list = Parallel(n_jobs=n_jobs)(
                    delayed(_process_profile)(zip_path, pf, election_plan, config["voting_configs"]) for pf in all_profile_files
                )

            # write all winners for this district/mode combo to one json file
            out_path = output_dir / (
                f"{run_name}_{dc.num_districts}_districts_{dc.winners}_winners_for_voter_mode_{mode}.json"
            )
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "run_name": run_name,
                        "voter_mode": mode,
                        "district_num": dc.num_districts,
                        "winners_per_district": dc.winners,
                        "profile_files": all_profile_files,
                        "election_results": results_list,
                    },
                    f,
                    indent=2,
                )

            print(f"[simulate_elections] Wrote: {out_path}")
