CREATE TABLE incrementer_in (
    cycle    integer PRIMARY KEY,
    in_valid boolean NOT NULL,
    din      bit(8)  NOT NULL
);

CREATE VIEW incrementer_out AS
WITH RECURSIVE sim(cycle, out_valid, dout) AS (
    SELECT 0, false, B'00000000'::bit(8)
    UNION ALL
    SELECT s.cycle + 1,
           i.in_valid,
           CASE WHEN i.in_valid THEN n.din_plus1 ELSE s.dout END
    FROM sim s
    JOIN incrementer_in i ON i.cycle = s.cycle,
    LATERAL (SELECT (i.din::int + 1)::bit(8) AS din_plus1) n
)
SELECT cycle, out_valid, dout FROM sim;
