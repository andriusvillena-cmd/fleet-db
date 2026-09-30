# fleet-db: braking runs in SQLite, and the questions a CSV cannot answer

Three braking runs, 1200 samples each at 100 Hz, eleven channels. They already
lived as CSV files and were already analysed in MATLAB. This repository puts
them in a database, because a CSV answers questions about one file and stops
there.

The split is the whole point:

- `samples` — one row per 10 ms sample per run. The signals as recorded.
- `runs` — one row per run. Surface, friction, tyre, ambient temperature,
  where the data came from. None of that is in the CSV.
- `sim_runs` — one row per Simulink calibration run, with the parameters it was
  run with and where the numbers were recovered from.

Every question worth asking crosses the two. "Every run where the pressure went
past 80 bar and the surface was not dry" needs a JOIN for the surface, a
GROUP BY for the per-run statistics, and a HAVING because the filter is on an
aggregate. That is one query here and a scripting problem otherwise.

## Running it

```
python make_demo_runs.py    # no real data? generate stand-ins first
python build_db.py          # CSV -> fleet.db, reading the current folder
python build_db.py --data ..\simulink-abs-model-scope    # or from where they live
python run_queries.py       # every query in queries.sql
python run_queries.py braking_summary
python -m pytest -v
```

Nothing to install beyond pandas, and that is only for the tests. SQLite ships
with Python.

## What the queries found

**The units were not what the column names imply.** `Long_Accel` is in g, not
m/s². Nothing in the file says so. The slope of the speed channel over the
full-braking stretch is 5.885 m/s² in run01 and the acceleration channel
averages -0.601 over the same window. 5.885 / 9.81 = 0.600. That settles it,
and `test_sql_decel_matches_the_speed_slope` keeps it settled.

**MAX() found a planted fault on the first try.** Asking for the peak brake
pressure per run returns 411 bar for run01. The other two return 131. The
plateau in all three runs is 130 bar. It is one sample, at 7.42 s, reading
411.000000 exactly in a file where nothing else is a round number, with 129.8
before it and 130.0 after, and the car already stopped. Six weeks of working
with these files in Python and MATLAB had not turned it up. The first
aggregate ran straight into it.

Two independent ways to keep it out, and both are in `queries.sql`:

- Require the value to be held for two consecutive samples. All three runs then
  come back at the same 130 bar.
- Apply a validity window. `brake_pressure > 10 AND vehicle_speed > 5` keeps
  the two seconds of cruising before the pedal and the stopped tail out of every
  average. The 411 bar sample lands in the tail, so it goes too.

**The rear left is the wheel that suffers in split-mu.** The `wheel_slip` query
computes slip in SQL, four wheels, one row per run. In run01 the rear left runs
at 0.151 against 0.078 on the front left, while both right-hand wheels sit at
0.027 like every wheel in the other two runs. The low-grip side is not one
wheel, and its worst wheel is the one carrying least load. The surface label
comes from the metadata table, so this query checks the label against the
signal rather than trusting it.

**The database agrees with the MATLAB work, by a different route.** MATLAB got
the deceleration by fitting the slope of the speed channel. The database takes
the mean of the acceleration channel over the braking event. Both give 5.89,
8.93 and 5.49 m/s². Two channels, two methods, two languages, same numbers.

**And it reproduces the finding that mattered, in six lines of SQL.**
`pressure_rise` shows the brake taking 230 to 240 ms to go from pedal to
130 bar. `braking_summary` shows the mean deceleration reaching its full value
straight away. The channel that is supposed to cause the deceleration is still
climbing while the deceleration is already there. The generator wrote the two
independently. The MATLAB comparison reached the same conclusion from a plot.

## The calibration runs, and where each row came from

A third table, `sim_runs`, holds the 45 Simulink runs from two ABS calibration
sessions. They were scattered across three places and none of them was a data
file:

- **11 runs** in `abs_sesion_24sep.mat`, with the time histories kept.
- **16 runs** as measured numbers written into tables in the sibling repository's
  README.
- **18 runs** reconstructed. That README did not record the ramp sweep's
  distances; it recorded how many metres each ramp lost against the best one on
  its surface. Adding that cost back to each surface's optimum recovers them.

The reconstruction was checked against six numbers that were not used to compute
it. The strongest: the README states the sweep's distances span 42.4 m to 88.5 m,
and the reconstruction gives 42.40 and 88.50. `test_sim_runs.py` keeps that check
running.

Every row carries a `source` column saying which of the three it is, because that
decides what the row can be used for. A row with `traces = 1` can be plotted
again; the other 34 are one number each.

**What the .mat file did not save.** Its fields are surface, friction, ramp,
deceleration, distance, engagement time and mean slip. `PBmax` and `TB` are not
among them. Two of its runs are both on dry tarmac with ramp 1400 and differ by
8.5 m of stopping distance, and the only thing separating them is a pressure
ceiling recorded in a notes file. That is the argument for a metadata table,
found in my own data rather than in a textbook: a parameter you can change
between runs has to be stored with the result, or the result stops meaning
anything a month later.

`sim_runs.csv` is published — it is simulation output, not a third-party capture
— so the tests over it run in CI with no private data.

## The slip window, again

Slip is (v − wheel) / v. As the car stops, the denominator goes to zero and slip
goes to 1 on division noise alone, whatever the wheel is doing. That artefact
cost hours in the MATLAB work. Averaging slip over a whole file gives 0.43 for
run01; over the braking event it gives 0.078. The window is not a detail, it is
most of the answer.

## Testing something that has no data

The signal CSVs are not redistributed, so on any machine but mine the checks in
`test_db.py` have nothing to read and skip. That would leave the loader, the
validity window and the outlier check untested in CI, which is worse than having
no CI at all: a green tick that means nothing.

`make_demo_runs.py` fills the gap. It writes three runs with the same column
names, the same 100 Hz sampling and the same shape — cruising, braking, stopped
tail — and plants one pressure outlier at a known instant. It is a stand-in for
the data, not a model of a car: the deceleration is imposed from the surface
friction rather than produced by a tyre.

It stands in well enough to be worth running against. On the generated data the
published queries return 5.89 and 5.49 m/s² for wet and split-mu, the same
figures as the real runs, the same 0.078 against 0.151 slip asymmetry, and the
planted outlier in the same place.

`test_demo.py` runs the queries **from `queries.sql`**, not copies of them, so a
query that breaks fails the build. Nine checks, standard library plus pytest.

**One of them earned its place while being written.** The check on the validity
window first asserted that the average slip over a whole file comes back as
nonsense. It does not: errors of both signs cancel and the mean lands on a
plausible 0.16, hiding the lot. What the window actually prevents is impossible
values — 505 of the 1200 samples fall outside the 0-to-1 range slip can occupy,
and none do inside the window. Counting impossible samples catches it; averaging
them does not.

Three test files, three scopes:

| file | needs | runs in CI |
|---|---|---|
| `test_demo.py` | nothing, it generates its own data | yes |
| `test_sim_runs.py` | `sim_runs.csv`, which is published | yes |
| `test_db.py` | the real signal CSVs | no, skips |

## Scope and honesty

The signals come from a generator, not from a vehicle. Nothing here validates
anything about a real car. The runs are internally consistent — the split-mu
deceleration is exactly the average of the ice and dry cases — which is what
you would expect from a generator and not from a test track.

The CSV files are not redistributed, so `build_db.py` and the tests need data
that is not in this repository. The tests skip instead of failing when it is
absent. What is published is the schema, the queries and the findings.

The `runs` table mixes two kinds of row. Surface, friction, initial speed,
sample rate and data origin are facts about how the runs were generated.
Vehicle, tyre, ambient temperature, driver and date are written by hand for the
exercise and are marked as such in the `context_origin` column. They are there
because the JOIN is the exercise, not because anyone measured them.

## Files

| file | what it is |
|---|---|
| `build_db.py` | CSV to SQLite. Schema, loader, row-count check |
| `runs_metadata.csv` | the `runs` table, three rows, hand-written |
| `queries.sql` | the queries, each with why it is written that way |
| `run_queries.py` | runs them and prints the results |
| `sim_runs.csv` | the `sim_runs` table, 45 calibration runs, with their provenance |
| `make_demo_runs.py` | writes stand-in signal runs so CI has something to load |
| `test_demo.py` | end-to-end checks on generated data, against `queries.sql` itself |
| `test_db.py` | cross-checks the database against pandas reading the same files |
| `test_sim_runs.py` | checks on `sim_runs`; these run in CI, the others cannot |
