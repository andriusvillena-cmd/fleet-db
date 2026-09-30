-- Queries against fleet.db. Each block starts with "-- name:" so
-- run_queries.py can pick them apart and print them one by one.
--
-- Units, checked and not assumed: speed km/h, pressure bar, yaw rate deg/s,
-- steering deg. Long_Accel and Lat_Accel are in g. The CSV does not say so;
-- the slope of the speed channel over the full-braking stretch is 5.885 m/s^2
-- in run01 and the acceleration channel averages -0.601 over the same window.
-- 5.885 / 9.81 = 0.600. That settles it.
--
-- The braking event window, used wherever a number should describe the
-- braking and not the file: brake_pressure > 10 AND vehicle_speed > 5. Each
-- run holds about two seconds of cruising before the pedal and a stopped tail
-- afterwards, and both of them poison an average. The tail is worse than
-- useless: slip is (v - wheel) / v, so with the car stopped it goes to 1 on
-- division noise alone.


-- name: row_count
-- The first thing to ask of any table you did not build yourself.
SELECT COUNT(*) AS samples,
       COUNT(DISTINCT run_id) AS runs
FROM samples;


-- name: peak_pressure
-- One number per run. Compare against pandas: same answer, or the loader is
-- wrong. run01 comes back with 411 bar, which is not a brake pressure.
SELECT run_id,
       ROUND(MAX(brake_pressure), 1) AS peak_bar
FROM samples
GROUP BY run_id
ORDER BY run_id;


-- name: pressure_outliers
-- Why MAX() is a bad way to ask for the highest pressure.
--
-- The plateau sits at 130 bar in all three runs. This lists every sample more
-- than 5 bar above it. One shows up: run01 at 7.42 s, 411.000000 bar exactly,
-- with 129.8 before it and 130.0 after, and the car already stopped. A single
-- sample, a round number in a file where nothing else is round, outside the
-- braking. It is a planted fault, and MAX() walked straight into it.
SELECT s.run_id,
       s.time,
       ROUND(s.brake_pressure, 3)  AS bar,
       ROUND(s.vehicle_speed, 2)   AS kph,
       ROUND(s.long_accel, 3)      AS g
FROM samples AS s
WHERE s.brake_pressure > 135
ORDER BY s.brake_pressure DESC;


-- name: pressure_robust
-- The same question asked so that one sample cannot answer it: a pressure
-- counts as reached only if the next sample holds it too. All three runs come
-- back at the same 130 bar plateau, which is what the trace actually shows.
SELECT a.run_id,
       ROUND(MAX(MIN(a.brake_pressure, b.brake_pressure)), 1) AS held_peak_bar
FROM samples AS a
JOIN samples AS b
  ON b.run_id = a.run_id
 AND b.time = ROUND(a.time + 0.01, 3)
GROUP BY a.run_id
ORDER BY a.run_id;


-- name: braking_summary
-- The question the CSV cannot answer on its own: "every run where the
-- pressure went past 80 bar and the surface was not dry".
--
-- The surface lives in runs, the pressure in samples, so it takes a JOIN.
-- The statistics are per run, so it takes a GROUP BY. And the filter is on an
-- aggregate, so it takes HAVING, not WHERE.
--
-- mean_decel_ms2 is the number to read, not peak_decel: the peak is one
-- sample and moves with the noise. The mean over the event window comes out
-- at 5.89, 8.93 and 5.49 m/s^2, the same figures the MATLAB work got by
-- fitting the slope of the speed channel. Two methods, two languages, same
-- answer.
SELECT r.run_id,
       r.surface,
       r.mu_nominal,
       r.ambient_c,
       COUNT(*)                                  AS samples,
       ROUND(MAX(s.brake_pressure), 1)           AS peak_bar,
       ROUND(AVG(s.long_accel) * -9.81, 2)       AS mean_decel_ms2,
       ROUND(AVG(s.long_accel) * -1.0, 3)        AS mean_decel_g,
       ROUND(MAX(ABS(s.yaw_rate)), 1)            AS peak_yaw_deg_s,
       ROUND(MAX(ABS(s.steering_angle)), 1)      AS peak_steer_deg
FROM runs AS r
JOIN samples AS s ON s.run_id = r.run_id
WHERE r.surface <> 'dry'
  AND s.brake_pressure > 10
  AND s.vehicle_speed > 5
GROUP BY r.run_id, r.surface, r.mu_nominal, r.ambient_c
HAVING MAX(s.brake_pressure) > 80
ORDER BY mean_decel_ms2 DESC;


-- name: wheel_slip
-- Slip computed in SQL, one row per run, all four wheels.
--
-- What it shows: run01 is the only run where the two sides disagree, and that
-- is what split-mu means. run02 and run03 are symmetric to the third decimal.
-- The surface column comes from the metadata table, so this query checks the
-- label against the signal instead of trusting it.
--
-- Read the four columns, not just the front axle. In run01 the rear left slips
-- twice as much as the front left, 0.151 against 0.078, while both right-hand
-- wheels sit at 0.027 like every wheel in the other two runs. The low-grip
-- side is not one wheel, and its worst wheel is the one carrying least load.
SELECT r.run_id,
       r.surface,
       ROUND(AVG((s.vehicle_speed - s.wheel_speed_fl) / s.vehicle_speed), 3) AS slip_fl,
       ROUND(AVG((s.vehicle_speed - s.wheel_speed_fr) / s.vehicle_speed), 3) AS slip_fr,
       ROUND(AVG((s.vehicle_speed - s.wheel_speed_rl) / s.vehicle_speed), 3) AS slip_rl,
       ROUND(AVG((s.vehicle_speed - s.wheel_speed_rr) / s.vehicle_speed), 3) AS slip_rr,
       ROUND(AVG((s.vehicle_speed - s.wheel_speed_fl) / s.vehicle_speed)
           - AVG((s.vehicle_speed - s.wheel_speed_fr) / s.vehicle_speed), 3) AS front_asymmetry
FROM runs AS r
JOIN samples AS s ON s.run_id = r.run_id
WHERE s.brake_pressure > 10
  AND s.vehicle_speed > 5
GROUP BY r.run_id, r.surface
ORDER BY r.run_id;


-- name: by_surface
-- Same tables, one level up: statistics per surface instead of per run. With
-- one run per surface the numbers repeat, which is the point — this is the
-- query that keeps working when the fleet grows and the per-run table stops
-- being readable.
SELECT r.surface,
       COUNT(DISTINCT r.run_id)                  AS runs,
       ROUND(AVG(r.mu_nominal), 2)               AS mu,
       ROUND(AVG(s.long_accel) * -9.81, 2)       AS mean_decel_ms2,
       ROUND(MAX(ABS(s.yaw_rate)), 1)            AS worst_yaw_deg_s
FROM runs AS r
JOIN samples AS s ON s.run_id = r.run_id
WHERE s.brake_pressure > 10
  AND s.vehicle_speed > 5
GROUP BY r.surface
ORDER BY mu DESC;


-- name: pressure_rise
-- How long the brake took to go from the pedal to 130 bar, per run.
--
-- 230 to 240 ms in all three. Worth putting next to mean_decel_ms2: the
-- deceleration reaches its full value straight away while the pressure that
-- is supposed to cause it is still climbing. The generator wrote the two
-- channels independently. This is the same finding the MATLAB comparison
-- reached, arrived at here in six lines of SQL.
SELECT r.run_id,
       r.surface,
       ROUND(MIN(CASE WHEN s.brake_pressure > 10  THEN s.time END), 3) AS t_pedal_s,
       ROUND(MIN(CASE WHEN s.brake_pressure >= 130 THEN s.time END), 3) AS t_130bar_s,
       ROUND((MIN(CASE WHEN s.brake_pressure >= 130 THEN s.time END)
            - MIN(CASE WHEN s.brake_pressure > 10  THEN s.time END)) * 1000) AS rise_ms
FROM runs AS r
JOIN samples AS s ON s.run_id = r.run_id
GROUP BY r.run_id, r.surface
ORDER BY r.run_id;
