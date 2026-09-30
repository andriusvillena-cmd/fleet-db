"""End-to-end checks on generated data, so CI exercises the whole pipeline.

The real signal CSVs are not redistributed, so test_db.py skips in CI and the
loader and the queries would go untested. These tests generate demo runs, load
them, and run the published queries from queries.sql — not copies of them, the
file itself, so a query that breaks fails here.

Only the standard library plus pytest.

    python -m pytest test_demo.py -v
"""

from __future__ import annotations

import filecmp
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from run_queries import split_queries

HERE = Path(__file__).parent
QUERIES = split_queries((HERE / "queries.sql").read_text(encoding="utf-8"))


def build(where: Path) -> sqlite3.Connection:
    """Generate the demo runs into `where`, load them, return a connection."""
    for step in (
        [sys.executable, str(HERE / "make_demo_runs.py"), "--out", str(where)],
        [sys.executable, str(HERE / "build_db.py"), "--data", str(where),
         "--metadata", "demo_metadata.csv", "--db", str(where / "demo.db")],
    ):
        done = subprocess.run(step, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return sqlite3.connect(where / "demo.db")


BUILT: Path | None = None


@pytest.fixture(scope="module", autouse=True)
def con(tmp_path_factory) -> sqlite3.Connection:
    global BUILT
    where = tmp_path_factory.mktemp("demo")
    connection = build(where)
    BUILT = where / "demo.db"
    yield connection
    connection.close()


def ask(con: sqlite3.Connection, name: str) -> list[tuple]:
    return con.execute(QUERIES[name]).fetchall()


def test_generator_is_deterministic(tmp_path):
    """Same seed, same bytes. A test fixture that drifts is not a fixture."""
    a, b = tmp_path / "a", tmp_path / "b"
    for where in (a, b):
        subprocess.run(
            [sys.executable, str(HERE / "make_demo_runs.py"), "--out", str(where)],
            check=True, capture_output=True,
        )
    names = sorted(p.name for p in a.glob("*.csv"))
    assert names, "the generator wrote nothing"
    for name in names:
        assert filecmp.cmp(a / name, b / name, shallow=False), name


def test_all_samples_load(con):
    total, runs = ask(con, "row_count")[0]
    assert runs == 3
    assert total == 3 * 1200


def test_no_orphan_samples(con):
    (orphans,) = con.execute(
        "SELECT COUNT(*) FROM samples WHERE run_id NOT IN (SELECT run_id FROM runs)"
    ).fetchone()
    assert orphans == 0


def test_outlier_query_finds_the_planted_sample(con):
    """The check that caught 411 bar in the real data has to catch it here."""
    rows = ask(con, "pressure_outliers")
    assert len(rows) == 1, rows
    run_id, t, bar, *_ = rows[0]
    assert (run_id, t, bar) == ("demo01", 7.42, 411.0)


def test_held_peak_ignores_the_outlier(con):
    """Requiring two consecutive samples has to bring every run back to plateau."""
    for run_id, held in ask(con, "pressure_robust"):
        assert 128 <= held <= 132, (run_id, held)


def test_mean_decel_matches_mu_times_g(con):
    """Cross-check on the units: the column is in g, so the query scales by 9.81.

    The demo imposes a = mu * g, so the query's answer must come back as that,
    which is only true if the g-to-m/s2 conversion in queries.sql is right.
    """
    expected = {"wet": 0.56 * 9.81, "split-mu": 0.60 * 9.81}
    rows = {r[1]: r[6] for r in ask(con, "braking_summary")}
    assert set(rows) == set(expected), rows
    for surface, want in expected.items():
        assert rows[surface] == pytest.approx(want, abs=0.05), surface


def test_summary_excludes_dry_and_low_pressure(con):
    rows = ask(con, "braking_summary")
    assert "dry" not in {r[1] for r in rows}
    assert all(r[5] > 80 for r in rows)


def test_split_mu_is_visible_in_the_wheels(con):
    """The surface label is metadata; the slip columns are the evidence."""
    # slip_fl, slip_fr, slip_rl, slip_rr, front_asymmetry
    rows = {r[1]: r[2:] for r in ask(con, "wheel_slip")}
    for surface in ("dry", "wet"):
        fl, fr, rl, rr, asym = rows[surface]
        assert abs(asym) < 0.005, f"{surface} should be symmetric, got {asym}"
    fl, fr, rl, rr, asym = rows["split-mu"]
    assert asym > 0.03, asym
    assert rl > fl, "the unloaded rear of the low-grip side should slip most"


def test_validity_window_keeps_the_impossible_out():
    """Why the window exists, asserted instead of written down.

    Slip is (v - wheel) / v. Once the car has stopped the denominator is noise
    and the quotient lands anywhere: in the demo it ranges from -214 to +369,
    and 505 of the 1200 samples fall outside the only range slip can occupy,
    0 to 1. Inside the window, not one does.

    Note what does NOT work as a check: the average. Errors of both signs
    cancel, so the mean over the whole file comes back at a plausible 0.16 and
    hides all of it. Counting impossible samples is what catches it.
    """
    con = sqlite3.connect(BUILT)
    impossible = (
        "SUM(CASE WHEN (vehicle_speed - wheel_speed_rl) / vehicle_speed "
        "NOT BETWEEN 0 AND 1 THEN 1 ELSE 0 END)"
    )
    (whole_file,) = con.execute(
        f"SELECT {impossible} FROM samples WHERE run_id = 'demo03'"
    ).fetchone()
    (in_window,) = con.execute(
        f"SELECT {impossible} FROM samples WHERE run_id = 'demo03' "
        "AND brake_pressure > 10 AND vehicle_speed > 5"
    ).fetchone()
    con.close()

    assert whole_file > 100, (
        f"the tail artefact is missing from the demo data ({whole_file} samples)"
    )
    assert in_window == 0, f"{in_window} impossible samples inside the window"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
