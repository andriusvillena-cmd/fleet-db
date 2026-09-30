"""Generate demo braking runs so the pipeline can be tested without real data.

The signal CSVs this project was built on are not redistributed, which leaves CI
with nothing to load. This writes three synthetic runs with the same column
names, the same 100 Hz sampling and the same shape: a couple of seconds of
cruising, a braking event, and a stopped tail.

It is a stand-in for the data, not a model of a car. The deceleration is imposed
from the surface friction rather than produced by a tyre, exactly the shortcut
the real generator took — and the point of the demo is to exercise the loader,
the validity window and the outlier check, not to predict anything.

One pressure outlier is planted on purpose, at a known instant, so the check that
found the 411 bar sample in the real data has something to find in CI too.

    python make_demo_runs.py            # writes demo0*.csv and demo_metadata.csv

Deterministic: same seed, same files, byte for byte.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from pathlib import Path

RATE_HZ = 100
DURATION_S = 12.0
BRAKE_ONSET_S = 2.0
PRESSURE_RISE_S = 0.24
PRESSURE_PLATEAU_BAR = 130.0
V0_KPH = 100.0
G = 9.81

# surface -> (peak friction, yaw during braking deg/s, steering deg)
SURFACES = {
    "dry": (1.00, 0.4, 0.5),
    "wet": (0.56, 0.4, 0.6),
    "split-mu": (0.60, 8.1, 12.9),
}

# The planted fault: run, instant, value. Outside the braking window and three
# times the plateau, so both defences in queries.sql catch it.
OUTLIER = ("demo01", 7.42, 411.0)


def pressure(t: float) -> float:
    """Brake pressure in bar: nothing, then a ramp, then the plateau."""
    if t < BRAKE_ONSET_S:
        return 0.0
    rise = (t - BRAKE_ONSET_S) / PRESSURE_RISE_S
    return PRESSURE_PLATEAU_BAR * min(rise, 1.0)


def run_rows(run_id: str, surface: str, rng: random.Random) -> list[dict]:
    """One run: speed falls at mu*g while the brake is on, then the car sits."""
    mu, yaw_peak, steer_peak = SURFACES[surface]
    decel = mu * G                      # m/s^2
    v = V0_KPH / 3.6                    # m/s

    rows = []
    for i in range(int(DURATION_S * RATE_HZ)):
        t = round(i / RATE_HZ, 2)
        braking = t >= BRAKE_ONSET_S and v > 0.05

        if braking:
            v = max(0.0, v - decel / RATE_HZ)
            long_accel = -decel / G     # g, as in the real files
        else:
            long_accel = 0.0

        v_kph = v * 3.6
        # Wheel slip: both sides equal, except on split-mu where the left side
        # and especially the unloaded rear left run away.
        if braking and v_kph > 5:
            slip = {"fl": 0.027, "fr": 0.027, "rl": 0.027, "rr": 0.027}
            if surface == "split-mu":
                slip["fl"], slip["rl"] = 0.078, 0.151
        else:
            slip = dict.fromkeys(("fl", "fr", "rl", "rr"), 0.0)

        p = pressure(t)
        if (run_id, t) == (OUTLIER[0], OUTLIER[1]):
            p = OUTLIER[2]

        noise = lambda scale: rng.gauss(0, scale)
        yaw = (yaw_peak if braking and v_kph > 5 else 0.0)
        steer = (steer_peak if braking and v_kph > 5 else 0.0)

        rows.append({
            "time": f"{t:.6f}",
            "Vehicle_Speed": f"{v_kph + noise(0.05):.6f}",
            "Wheel_Speed_FL": f"{v_kph * (1 - slip['fl']) + noise(0.05):.6f}",
            "Wheel_Speed_FR": f"{v_kph * (1 - slip['fr']) + noise(0.05):.6f}",
            "Wheel_Speed_RL": f"{v_kph * (1 - slip['rl']) + noise(0.05):.6f}",
            "Wheel_Speed_RR": f"{v_kph * (1 - slip['rr']) + noise(0.05):.6f}",
            "Brake_Pressure": f"{p + (noise(0.3) if p else noise(0.3)):.6f}"
                              if p != OUTLIER[2] else f"{OUTLIER[2]:.6f}",
            "Yaw_Rate": f"{yaw * (0.5 + 0.5 * math.sin(9 * t)) + noise(0.05):.6f}",
            "Steering_Angle": f"{steer * (0.5 + 0.5 * math.sin(3 * t)) + noise(0.05):.6f}",
            "Long_Accel": f"{long_accel + noise(0.004):.6f}",
            "Lat_Accel": f"{noise(0.006):.6f}",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".", help="where to write (default: .)")
    parser.add_argument("--seed", type=int, default=30092026)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    meta = []
    for n, surface in enumerate(SURFACES, 1):
        run_id = f"demo{n:02d}"
        name = f"{run_id}_{surface.replace('-', '')}.csv"
        rows = run_rows(run_id, surface, random.Random(args.seed + n))

        with (out / name).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        meta.append({
            "run_id": run_id,
            "source_file": name,
            "surface": surface,
            "mu_nominal": SURFACES[surface][0],
            "v0_kph": V0_KPH,
            "sample_rate_hz": RATE_HZ,
            "duration_s": DURATION_S,
            "data_origin": "make_demo_runs.py",
            "vehicle": "none, this is a stand-in",
            "tyre": "",
            "ambient_c": "",
            "driver": "",
            "test_date": "",
            "context_origin": "generated, no vehicle involved",
        })
        print(f"{run_id}  {surface:<9} {len(rows):>5} samples  ->  {name}")

    with (out / "demo_metadata.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(meta[0]))
        writer.writeheader()
        writer.writerows(meta)
    print("demo_metadata.csv")


if __name__ == "__main__":
    main()
