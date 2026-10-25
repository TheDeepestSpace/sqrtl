-- Clock-domain crossing: a toggle pulse synchronizer from a fast clock to a
-- slow one. A pulse on pulse_in (clk_fast) toggles req; the slow domain
-- synchronizes req with two flops and turns each toggle into a one-cycle
-- pulse_out. The slow side's view of req is synchronized back as the
-- acknowledge, and a new pulse is accepted only when the previous one has
-- been seen (busy = req != ack), which is what makes fast -> slow safe.
CREATE DOMAIN clock AS boolean;

CREATE TABLE pulse_sync_in (
    cycle    integer PRIMARY KEY,
    clk_fast clock   NOT NULL,   -- true when clk_fast rises at the end of this tick
    clk_slow clock   NOT NULL,
    pulse_in boolean NOT NULL    -- clk_fast domain
);

CREATE VIEW pulse_sync_out AS
WITH RECURSIVE sim(cycle, req, ack_f1, ack_f2, req_s1, req_s2, req_s3, pulse_out) AS (
    SELECT 0, false, false, false, false, false, false, false
    UNION ALL
    SELECT s.cycle + 1,
           -- clk_fast domain
           CASE WHEN i.clk_fast THEN s.req <> (i.pulse_in AND NOT f.busy) ELSE s.req    END,
           CASE WHEN i.clk_fast THEN s.req_s2                              ELSE s.ack_f1 END,
           CASE WHEN i.clk_fast THEN s.ack_f1                              ELSE s.ack_f2 END,
           -- clk_slow domain
           CASE WHEN i.clk_slow THEN s.req                ELSE s.req_s1    END,
           CASE WHEN i.clk_slow THEN s.req_s1             ELSE s.req_s2    END,
           CASE WHEN i.clk_slow THEN s.req_s2             ELSE s.req_s3    END,
           CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END
    FROM sim s
    JOIN pulse_sync_in i ON i.cycle = s.cycle,
    LATERAL (SELECT s.req <> s.ack_f2 AS busy) f
)
SELECT cycle, req, ack_f2, pulse_out FROM sim;
