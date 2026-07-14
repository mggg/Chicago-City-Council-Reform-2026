"""
Compute and persist candidate distance ("preference") matrices for ranked profiles.

Wraps votekit's candidate_distance_matrix so the pipeline can, during election
simulation, save one matrix per voter profile to a JSON file. The functions here
are deliberately standalone (they take a RankProfile and paths, not a config) so
they can be reused from notebooks or other scripts.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional, Sequence

from votekit import RankProfile
from votekit.matrices import candidate_distance_matrix


def compute_preference_matrix(
    profile: RankProfile,
    candidates: Optional[Sequence[str]] = None,
) -> dict:
    """
    Compute the candidate distance ("preference") matrix for a ranked profile.

    Delegates to votekit.candidate_distance_matrix: the (i, j) entry is the
    average ranking gap between candidates i and j over the ballots that rank i
    at or above j (weighted by ballot weight). The matrix is non-symmetric and
    uses NaN for pairs that never co-occur in that order; those NaNs are rendered
    as JSON null so the payload is valid JSON.

    Args:
        profile: The ranked voter profile to analyze.
        candidates: Candidate ids fixing the row/column order of the matrix.
            Defaults to profile.candidates.

    Returns:
        A JSON-serializable dict:
            {
              "candidates": [<candidate id>, ...],   # row/column order
              "matrix": [[float | None, ...], ...],  # NaN entries as null
            }
    """
    candidates = list(profile.candidates) if candidates is None else list(candidates)

    matrix = candidate_distance_matrix(profile, candidates)

    return {
        "candidates": candidates,
        "matrix": [
            [None if math.isnan(value) else float(value) for value in row]
            for row in matrix
        ],
    }


def preference_matrix_json(
    profile: RankProfile,
    candidates: Optional[Sequence[str]] = None,
    *,
    indent: int = 2,
) -> str:
    """
    Compute the preference matrix for a profile and serialize it to a JSON string.

    Useful when the caller wants the bytes to write itself (e.g. into a shared zip
    archive from the main process) rather than a file on disk.

    Args:
        profile: The ranked voter profile to analyze.
        candidates: Optional explicit candidate ordering (see
            compute_preference_matrix).
        indent: json.dumps indentation.

    Returns:
        The matrix payload as a JSON string.
    """
    return json.dumps(compute_preference_matrix(profile, candidates), indent=indent)


def write_preference_matrix(
    profile: RankProfile,
    output_path: str | Path,
    candidates: Optional[Sequence[str]] = None,
) -> Path:
    """
    Compute the preference matrix for a profile and write it to a JSON file,
    creating parent directories as needed.

    Args:
        profile: The ranked voter profile to analyze.
        output_path: Destination .json path.
        candidates: Optional explicit candidate ordering (see
            compute_preference_matrix).

    Returns:
        The output Path that was written.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    payload = compute_preference_matrix(profile, candidates)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return output_path


def preference_matrix_arcname(profile_member: str) -> str:
    """
    Build the preference-matrix entry name for a profile, mirroring the layout of
    the profiles archive (<mode>/<district_count>/<file>).

    Returns a forward-slash relative name suitable both as a zip entry name and,
    joined onto a root, as a filesystem path (see preference_matrix_path).

    Args:
        profile_member: The profile's path within profiles.zip, e.g.
            "slate_pl/10/<run>_..._district_00_v0.csv".

    Returns:
        "<mode>/<district_count>/<profile_stem>.json".
    """
    parts = Path(profile_member).parts
    mode, district_count = parts[0], parts[1]
    stem = Path(parts[-1]).stem
    return f"{mode}/{district_count}/{stem}.json"


def preference_matrix_path(root: str | Path, profile_member: str) -> Path:
    """
    Build the preference-matrix output path for a profile under a filesystem root,
    mirroring the profiles archive layout (<mode>/<district_count>/<file>).

    Args:
        root: The preference_matrices root directory, e.g.
            outputs/<run_name>/preference_matrices.
        profile_member: The profile's path within profiles.zip.

    Returns:
        Path root/<mode>/<district_count>/<profile_stem>.json.
    """
    return Path(root) / preference_matrix_arcname(profile_member)
