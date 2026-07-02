"""
Lightweight wall-clock profiling for pipeline stages.

Usage:
    from pipeline.utils.profiling import profile_stage, print_profile_summary

    with profile_stage("Generating Profiles", run_name):
        generate_profiles(config)
    ...
    print_profile_summary(run_name)  # prints a per-stage table and writes a CSV

Times are wall-clock (time.perf_counter), which is what matters here since the
heavy stages fan out across processes with joblib and are I/O + CPU bound.
"""

from __future__ import annotations

import csv
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class _StageRecord:
    name: str
    seconds: float


# run_name -> ordered list of stage records
_records: Dict[str, List[_StageRecord]] = {}


@contextmanager
def profile_stage(name: str, run_name: str = "_"):
    """Time the wrapped block and record it under run_name.

    Args:
        name: Human-readable stage name (e.g. "Simulating Elections").
        run_name: Config run name the stage belongs to; used to group records.
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        _records.setdefault(run_name, []).append(_StageRecord(name, elapsed))
        print(f"[profile] {run_name} :: {name}: {elapsed:.2f}s")


def reset_profile(run_name: Optional[str] = None) -> None:
    """Clear recorded timings for one run, or all runs if run_name is None."""
    if run_name is None:
        _records.clear()
    else:
        _records.pop(run_name, None)


def print_profile_summary(run_name: str, write_csv: bool = True) -> None:
    """Print a per-stage timing table for a run and optionally persist it as CSV.

    Args:
        run_name: The run whose stage timings should be summarized.
        write_csv: If True, also write outputs/<run_name>/profile/stage_times.csv.
    """
    records = _records.get(run_name, [])
    if not records:
        return

    total = sum(r.seconds for r in records)
    width = max((len(r.name) for r in records), default=10)

    print("\n" + "=" * (width + 26))
    print(f"Stage timing summary: {run_name}")
    print("=" * (width + 26))
    print(f"{'Stage'.ljust(width)}   {'Seconds':>10}   {'% total':>8}")
    print("-" * (width + 26))
    for r in records:
        pct = (r.seconds / total * 100) if total else 0.0
        print(f"{r.name.ljust(width)}   {r.seconds:>10.2f}   {pct:>7.1f}%")
    print("-" * (width + 26))
    print(f"{'TOTAL'.ljust(width)}   {total:>10.2f}   {100.0:>7.1f}%")
    print("=" * (width + 26) + "\n")

    if write_csv:
        out_dir = Path("outputs") / run_name / "profile"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "stage_times.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["stage", "seconds", "pct_total"])
            for r in records:
                pct = (r.seconds / total * 100) if total else 0.0
                writer.writerow([r.name, f"{r.seconds:.4f}", f"{pct:.2f}"])
            writer.writerow(["TOTAL", f"{total:.4f}", "100.00"])
        print(f"[profile] Wrote stage timings -> {out_path}")
