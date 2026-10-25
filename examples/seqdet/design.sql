-- A three-state sequence detector: `detected` pulses for one cycle after the
-- serial input `din` has carried the pattern 1-0-1 (overlaps allowed).
-- The state register is a PostgreSQL enum; it becomes a SystemVerilog
-- `typedef enum` (or localparams in Verilog-2005) and the simple CASE on it
-- becomes a `case` statement.
CREATE TYPE state_t AS ENUM ('idle', 'saw_1', 'saw_10');

CREATE TABLE seqdet_in (
    cycle integer PRIMARY KEY,
    din   boolean NOT NULL
);

CREATE VIEW seqdet_out AS
WITH RECURSIVE sim(cycle, state, detected) AS (
    SELECT 0, 'idle'::state_t, false
    UNION ALL
    SELECT s.cycle + 1,
           CASE s.state
               WHEN 'idle'  THEN CASE WHEN i.din THEN 'saw_1'::state_t ELSE 'idle'   END
               WHEN 'saw_1' THEN CASE WHEN i.din THEN 'saw_1'::state_t ELSE 'saw_10' END
               ELSE              CASE WHEN i.din THEN 'saw_1'::state_t ELSE 'idle'   END
           END,
           s.state = 'saw_10' AND i.din
    FROM sim s
    JOIN seqdet_in i ON i.cycle = s.cycle
)
SELECT cycle, state, detected FROM sim;
