-- A three-state sequence detector: `detected` pulses for one cycle after the
-- serial input `din` has carried the pattern 1-0-1 (overlaps allowed).
-- The state register is a bit(2): 00 IDLE, 01 SAW_1, 10 SAW_10.
CREATE TABLE seqdet_in (
    cycle integer PRIMARY KEY,
    din   boolean NOT NULL
);

CREATE VIEW seqdet_out AS
WITH RECURSIVE sim(cycle, state, detected) AS (
    SELECT 0, B'00'::bit(2), false
    UNION ALL
    SELECT s.cycle + 1,
           CASE WHEN st.idle  THEN CASE WHEN i.din THEN B'01'::bit(2) ELSE B'00'::bit(2) END
                WHEN st.saw_1 THEN CASE WHEN i.din THEN B'01'::bit(2) ELSE B'10'::bit(2) END
                ELSE               CASE WHEN i.din THEN B'01'::bit(2) ELSE B'00'::bit(2) END
           END,
           st.saw_10 AND i.din
    FROM sim s
    JOIN seqdet_in i ON i.cycle = s.cycle,
    LATERAL (SELECT s.state = B'00'::bit(2) AS idle,
                    s.state = B'01'::bit(2) AS saw_1,
                    s.state = B'10'::bit(2) AS saw_10) st
)
SELECT cycle, state, detected FROM sim;
