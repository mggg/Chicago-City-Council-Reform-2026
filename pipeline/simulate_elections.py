"""
Run elections on generated voter profiles and record the winners.

Reads voter profile CSV files and runs the appropriate election 
rule (STV for multi-seat districts, plurality and IRV for single-seat districts), 
and writes aggregated election results to JSON files.
"""

from __future__ import annotations
import json
from glob import glob
from pathlib import Path
from joblib import Parallel, delayed
from votekit import RankProfile
from votekit.elections import FastSTV as STV, Plurality
from typing import List, Iterable

# Optional progress bar for joblib.
try:
    from joblib_progress import joblib_progress 
except Exception: 
    joblib_progress = None 

def _process_profile(profile_file: str | Path, n_seats: int) -> List[str]:
    """
    Load a voter profile csv and run an election to determine winners.
    uses stv for multi-seat races and plurality for single-seat races.

    Args:
        profile_file: Path to the voter profile csv.
        n_seats: Number of seats to fill in this election.

    Returns:
        For n_seats > 1: {"stv": [winner ids]}
        For n_seats == 1: {"plurality": [winner ids], "irv": [winner ids]}
    """
    profile_path = Path(profile_file)
    profile: RankProfile = RankProfile.from_csv(profile_path)

    if n_seats > 1:
        elected_stv = STV(profile, m=n_seats, simultaneous=False, tiebreak='random').get_elected()
        return {"stv": _candidate_list_from_elected(elected_stv)}
    else:
        elected_plurality = Plurality(profile, m=1, tiebreak='random').get_elected()
        elected_irv = STV(profile, m=n_seats, simultaneous=False, tiebreak='random').get_elected()
        return {"stv": _candidate_list_from_elected(elected_plurality), "irv": _candidate_list_from_elected(elected_irv)}

def _import_voting_rule_modules(voting_rule: str) -> None:
    voting_rule = voting_rule.lower()

