"""Load the ABS braking runs into a SQLite database.

Two tables, on purpose:

    samples  one row per 10 ms sample per run, the signals as recorded
    runs     one row per run, the context that is nowhere in the CSV

The CSV holds the signals and nothing else. Surface, tyre, ambient
temperature and the origin of the data live in the second table, and every
question worth asking crosses the two.

Usage:
    python build_db.py                  # reads ./ and writes fleet.db
    python build_db.py --data DIR --db PATH
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

# CSV header -> column name in the database. Same names, lowercased, so a
# column in a query can be traced back to a column in the file.
SIGNAL_COLUMNS = {
    "time": "time",
    "Vehicle_Speed": "vehicle_speed",
    "Wheel_Speed_FL": "wheel_speed_fl",
    "Wheel_Speed_FR": "wheel_speed_fr",
    "Wheel_Speed_RL": "wheel_speed_rl",
    "Wheel_Speed_RR": "wheel_speed_rr",
    "Brake_Pressure": "brake_pressure",
    "Yaw_Rate": "yaw_rate",
    "Steering_Angle": "steering_angle",
    "Long_Accel": "long_accel",
    "Lat_Accel": "lat_accel",
}

SCHEMA = """
DROP TABLE IF EXISTS samples;
DROP TABLE IF EXISTS runs;

CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,
    source_file     TEXT NOT NULL,
    surface         TEXT NOT NULL,      -- dry | wet | split-mu
    mu_nominal      REAL NOT NULL,      -- peak friction the run was generated with
    v0_kph          REAL NOT NULL,
    sample_rate_hz  INTEGER NOT NULL,
    duration_s      REAL NOT NULL,
    data_origin     TEXT NOT NULL,      -- where the signals come from
    vehicle         TEXT,
    tyre            TEXT,
    ambient_c       REAL,
    driver          TEXT,
    test_date       TEXT,
    context_origin  TEXT NOT NULL       -- where this row's context comes from
);

CREATE TABLE samples (
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    time            REAL NOT NULL,      -- s
    vehicle_speed   REAL,               -- km/h
    wheel_speed_fl  REAL,               -- km/h
    wheel_speed_fr  REAL,
    wheel_speed_rl  REAL,
    wheel_speed_rr  REAL,
    brake_pressure  REAL,               -- bar
    yaw_rate        REAL,               -- deg/s
    steering_angle  REAL,               -- deg
    long_accel      REAL,               -- g, not m/s^2 (checked against the speed slope)
    lat_accel       REAL,               -- g
    PRIMARY KEY (run_id, time)
);

CREATE INDEX idx_samples_run ON samples(run_id);

DROP TABLE IF EXISTS sim_runs;

CREATE TABLE sim_runs (
    run_id       TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    surface      TEXT NOT NULL,      -- dry | wet | splitmu
    mu           REAL NOT NULL,
    ramp_gain    INTEGER NOT NULL,   -- hydraulic ramp, model units per second
    pb_max       INTEGER NOT NULL,   -- pressure ceiling
    tb_ms        REAL,               -- brake time constant
    slip_target  REAL,               -- controller setpoint
    decel_ms2    REAL,
    distance_m   REAL,
    t_abs_s      REAL,               -- when the ABS starts cycling
    slip_sd      REAL,               -- spread of slip while the ABS works
    slip_peak    REAL,
    traces       INTEGER NOT NULL,   -- 1 if the time histories were kept
    source       TEXT NOT NULL,      -- mat_traces | readme_meas | readme_recon
    note         TEXT
);
"""


def load_metadata(con: sqlite3.Connection, path: Path) -> list[dict]:
    """Insert runs_metadata.csv into the runs table and return its rows."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"{path.name} has no rows")

    columns = list(rows[0])
    placeholders = ", ".join(":" + c for c in columns)
    con.executemany(
        f"INSERT INTO runs ({', '.join(columns)}) VALUES ({placeholders})", rows
    )
    return rows


def load_signals(con: sqlite3.Connection, run_id: str, path: Path) -> int:
    """Insert one CSV of signals into the samples table. Returns row count."""
    if not path.exists():
        # The signal CSVs are not redistributed, so their absence is normal.
        # The metadata and the simulation runs still load, and the database is
        # useful without them.
        return 0

    db_columns = ["run_id"] + list(SIGNAL_COLUMNS.values())
    sql = (
        f"INSERT INTO samples ({', '.join(db_columns)}) "
        f"VALUES ({', '.join('?' * len(db_columns))})"
    )

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(SIGNAL_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path.name} is missing columns: {sorted(missing)}")

        batch = (
            [run_id] + [float(row[csv_name]) for csv_name in SIGNAL_COLUMNS]
            for row in reader
        )
        cur = con.executemany(sql, batch)

    # executemany does not report a reliable rowcount across versions, so ask.
    (count,) = con.execute(
        "SELECT COUNT(*) FROM samples WHERE run_id = ?", (run_id,)
    ).fetchone()
    return count


def load_sim_runs(con: sqlite3.Connection, path: Path) -> int:
    """Insert sim_runs.csv. Empty cells become NULL, not empty strings."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [
            {k: (v if v != "" else None) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]
    columns = list(rows[0])
    con.executemany(
        f"INSERT INTO sim_runs ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)})",
        rows,
    )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", default=".", help="folder holding the CSV files (default: .)"
    )
    parser.add_argument("--db", default="fleet.db", help="database file to write")
    parser.add_argument(
        "--metadata", default="runs_metadata.csv", help="run metadata CSV"
    )
    args = parser.parse_args()

    data = Path(args.data)
    db = Path(args.db)

    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    with con:
        con.executescript(SCHEMA)
        runs = load_metadata(con, data / args.metadata)
        for run in runs:
            n = load_signals(con, run["run_id"], data / run["source_file"])
            if n == 0:
                print(f"{run['run_id']:>6}  {run['surface']:<10}"
                      f"     - {run['source_file']} not present, signals skipped")
                continue
            expected = round(float(run["duration_s"]) * int(run["sample_rate_hz"]))
            flag = "" if n == expected else f"  <-- expected {expected}"
            print(f"{run['run_id']:>6}  {run['surface']:<10} {n:>5} samples{flag}")

        n = load_sim_runs(con, data / "sim_runs.csv")
        if n:
            print(f"{'sim_runs':>6}{'':12} {n:>5} simulation runs")

    print(f"\nwrote {db}  ({db.stat().st_size / 1e6:.1f} MB)")
    con.close()


if __name__ == "__main__":
    main()
