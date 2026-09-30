"""Checks on the sim_runs table: the 45 Simulink calibration runs.

These need only sim_runs.csv, which is published, so they run in CI as well.
Kept apart from test_db.py, whose checks are all skipped when the signal CSVs
are absent — a module-level skip would take these with it.

    python -m pytest test_sim_runs.py -v
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

pytestmark = pytest.mark.skipif(
    not (HERE / "sim_runs.csv").exists(), reason="sim_runs.csv is missing"
)


def sim_con() -> sqlite3.Connection:
    subprocess.run(
        [sys.executable, str(HERE / "build_db.py"), "--data", str(HERE),
         "--db", str(HERE / "fleet.db")],
        check=True, capture_output=True,
    )
    return sqlite3.connect(HERE / "fleet.db")


def test_every_sim_run_declares_where_it_came_from():
    con = sim_con()
    bad = con.execute(
        "SELECT COUNT(*) FROM sim_runs "
        "WHERE source NOT IN ('mat_traces', 'readme_meas', 'readme_recon')"
    ).fetchone()[0]
    assert bad == 0
    con.close()


def test_reconstructed_distances_match_the_published_range():
    """The README states 42.4 m to 88.5 m over the 21 runs of the ramp sweep.

    Those two numbers were not used to reconstruct the distances, so they are an
    independent check on the reconstruction.
    """
    con = sim_con()
    lo, hi, n = con.execute(
        "SELECT MIN(distance_m), MAX(distance_m), COUNT(*) FROM sim_runs "
        "WHERE session_date = '2026-09-25' AND slip_target = 0.20"
    ).fetchone()
    assert n == 21
    assert lo == pytest.approx(42.4, abs=0.05)
    assert hi == pytest.approx(88.5, abs=0.05)
    con.close()


def test_runs_with_traces_carry_their_scalars():
    """A run whose time histories were kept has no excuse for a missing number."""
    con = sim_con()
    gaps = con.execute(
        "SELECT COUNT(*) FROM sim_runs WHERE traces = 1 AND "
        "(decel_ms2 IS NULL OR distance_m IS NULL OR t_abs_s IS NULL)"
    ).fetchone()[0]
    assert gaps == 0
    con.close()

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
