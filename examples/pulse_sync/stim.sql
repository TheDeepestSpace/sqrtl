-- clk_fast rises every tick, clk_slow every third tick. Pulses are requested
-- at ticks 1, 2, 9 and 20; the one at tick 2 arrives while the first is still
-- crossing (busy) and is dropped.
INSERT INTO pulse_sync_in
SELECT t,
       true,
       t % 3 = 2,
       t IN (1, 2, 9, 20)
FROM generate_series(0, 35) AS t;
