"""Cross-check the database against pandas reading the same CSV files.

The loader is the part that can silently lie: a column mapped to the wrong
name, a row dropped, a float parsed as text. So every number the database
gives is asked again of the CSV, by a different library, and the two have to
agree.

    python -m pytest test_db.py -v
    python test_db.py            # same checks, plain output

Runs are skipped when the CSV files are not present. They are not
redistributed, so this is the normal state on a machine that is not Andres's.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DB = HERE / "fleet.db"
METADATA = HERE / "runs_metadata.csv"

pd = pytest.importorskip("pandas")


def metadata_rows() -> list[dict]:
    return pd.read_csv(METADATA).to_dict("records")


def csv_files_present() -> bool:
    return all((HERE / r["source_file"]).exists() for r in metadata_rows())


pytestmark = pytest.mark.skipif(
    not METADATA.exists() or not csv_files_present(),
    reason="signal CSV files are not redistributed",
)


@pytest.fixture(scope="module")
def con() -> sqlite3.Connection:
    """Build the database from scratch, then hand out a connection."""
    subprocess.run(
        [sys.executable, str(HERE / "build_db.py"), "--data", str(HERE),
         "--db", str(DB)],
        check=True,
        capture_output=True,
    )
    connection = sqlite3.connect(DB)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def frames() -> dict:
    return {
        r["run_id"]: pd.read_csv(HERE / r["source_file"]) for r in metadata_rows()
    }


def test_row_count_matches_csv(con, frames):
    for run_id, df in frames.items():
        (n,) = con.execute(
            "SELECT COUNT(*) FROM samples WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert n == len(df), f"{run_id}: {n} rows in the database, {len(df)} in the CSV"


def test_peak_pressure_matches_csv(con, frames):
    for run_id, df in frames.items():
        (value,) = con.execute(
            "SELECT MAX(brake_pressure) FROM samples WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert value == pytest.approx(df["Brake_Pressure"].max(), abs=1e-6)


def test_peak_decel_matches_csv(con, frames):
    for run_id, df in frames.items():
        (value,) = con.execute(
            "SELECT MIN(long_accel) FROM samples WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert value == pytest.approx(df["Long_Accel"].min(), abs=1e-6)


def test_time_axis_is_continuous(con):
    """No sample lost and no sample duplicated: 100 Hz means 10 ms steps."""
    for (run_id,) in con.execute("SELECT run_id FROM runs").fetchall():
        rows = con.execute(
            "SELECT time FROM samples WHERE run_id = ? ORDER BY time", (run_id,)
        ).fetchall()
        steps = [b[0] - a[0] for a, b in zip(rows, rows[1:])]
        assert max(steps) == pytest.approx(0.01, abs=1e-6), run_id
        assert min(steps) == pytest.approx(0.01, abs=1e-6), run_id


def test_every_sample_has_a_run(con):
    """The whole point of two tables is that they stay joined."""
    (orphans,) = con.execute(
        "SELECT COUNT(*) FROM samples "
        "WHERE run_id NOT IN (SELECT run_id FROM runs)"
    ).fetchone()
    assert orphans == 0


def test_summary_query_excludes_dry_and_low_pressure(con):
    """The 5.7 query has to filter on both tables at once."""
    import re

    sql = (HERE / "queries.sql").read_text(encoding="utf-8")
    block = re.split(r"^--\s*name:\s*braking_summary\s*$", sql, flags=re.MULTILINE)[1]
    block = re.split(r"^--\s*name:", block, flags=re.MULTILINE)[0]

    rows = con.execute(block).fetchall()
    surfaces = {row[1] for row in rows}
    assert "dry" not in surfaces
    assert surfaces, "the summary query returned nothing"
    for row in rows:
        assert row[5] > 80, f"{row[0]} came back with peak {row[5]} bar"


def test_planted_pressure_outlier_is_still_there(con):
    """Regression test on a finding, not on the code.

    run01 carries one sample of 411.000000 bar with the car already stopped.
    If a future loader, filter or regenerated file makes it disappear, that is
    a change worth knowing about, not a cleanup.
    """
    rows = con.execute(
        "SELECT run_id, time, brake_pressure FROM samples WHERE brake_pressure > 135"
    ).fetchall()
    assert rows == [("run01", 7.42, 411.0)]


def test_sql_decel_matches_the_speed_slope(con, frames):
    """The SQL mean and the MATLAB method have to agree.

    MATLAB got the deceleration by fitting the slope of the speed channel.
    The database takes the mean of the acceleration channel over the braking
    event. Two different signals, two different methods: if they disagree the
    units are wrong or the window is wrong.
    """
    import numpy as np

    for run_id, df in frames.items():
        v = df["Vehicle_Speed"] / 3.6
        window = (v < 0.9 * v.iloc[0]) & (v > 0.3 * v.iloc[0])
        slope = -np.polyfit(df["time"][window], v[window], 1)[0]

        (sql_decel,) = con.execute(
            "SELECT AVG(long_accel) * -9.81 FROM samples "
            "WHERE run_id = ? AND brake_pressure > 10 AND vehicle_speed > 5",
            (run_id,),
        ).fetchone()

        assert sql_decel == pytest.approx(slope, rel=0.01), (
            f"{run_id}: SQL {sql_decel:.2f} m/s2 against slope {slope:.2f} m/s2"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
