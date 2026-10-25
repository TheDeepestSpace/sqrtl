-- A free-running 4-bit counter. cnt does not depend on any input: it
-- increments every cycle and wraps at 16. The en port is declared so the
-- design has a stimulus to run against; the logic deliberately ignores it.
CREATE TABLE counter_in (
    cycle integer PRIMARY KEY,
    en    boolean NOT NULL
);

CREATE VIEW counter_out AS
WITH RECURSIVE sim(cycle, cnt) AS (
    SELECT 0, B'0000'::bit(4)
    UNION ALL
    SELECT s.cycle + 1,
           (s.cnt::int + 1)::bit(4)
    FROM sim s
    JOIN counter_in i ON i.cycle = s.cycle
)
SELECT cycle, cnt FROM sim;
