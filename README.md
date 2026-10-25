# sqrtl

**SQL + RTL, why the hell not.**

sqrtl is a toy hardware description language where the design file is a
PostgreSQL query. A design is one `CREATE TABLE` (the input ports) and one
`CREATE VIEW` whose body is a `WITH RECURSIVE` CTE: every row of the CTE is the
register state after one clock edge. PostgreSQL *is* the simulator, and a small
Python transpiler turns the same file into synthesizable Verilog-2005. The
proof of correctness is `sqrtl check`: the trace PostgreSQL produces and the
trace Icarus Verilog produces from the generated module must be byte-identical.

```sh
sqrtl sim       examples/incrementer      # run the design in Postgres, print the trace CSV
sqrtl transpile examples/incrementer      # design.sql -> Verilog on stdout
sqrtl check     examples/incrementer      # sim + transpile + iverilog + diff; exit 0 on match
```

What it does not do (v1): one clock, one synchronous reset, no memories, no
hierarchy, no window functions, no Verilog optimization, PostgreSQL 15+ only.
See [Future work](#future-work).

## Quick start

Requirements: Python 3.11+, a PostgreSQL 15+ server, and Icarus Verilog
(`iverilog`, `vvp`) on `PATH`.

```sh
pip install -e '.[test]'
export SQRTL_DSN=postgresql://localhost/sqrtl   # default; any psycopg DSN works
sqrtl check examples/incrementer
sqrtl check examples/counter
pytest
```

`sim` and `check` load the design and stimulus into a temporary schema and drop
it afterwards; the database itself must exist.

Or open the repository in the [devcontainer](#development): it ships Python,
Icarus and a local PostgreSQL that is started for you.

## The incrementer

`examples/incrementer/design.sql` adds 1 to `din` when `in_valid` is high and
presents the result one cycle later with `out_valid`.

<table>
<tr><th>design.sql</th><th>sqrtl transpile</th></tr>
<tr><td>

```sql
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
           CASE WHEN i.in_valid THEN n.din_plus1
                ELSE s.dout END
    FROM sim s
    JOIN incrementer_in i ON i.cycle = s.cycle,
    LATERAL (SELECT (i.din::int + 1)::bit(8)
             AS din_plus1) n
)
SELECT cycle, out_valid, dout FROM sim;
```

</td><td>

```verilog
module incrementer (
    input  wire       clk,
    input  wire       rst,
    input  wire       in_valid,
    input  wire [7:0] din,
    output reg        out_valid,
    output reg  [7:0] dout
);
    wire [7:0] din_plus1 = (din + 8'd1);

    always @(posedge clk) begin
        if (rst) begin
            out_valid <= 1'b0;
            dout <= 8'd0;
        end else begin
            out_valid <= in_valid;
            dout <= (in_valid ? din_plus1 : dout);
        end
    end
endmodule
```

</td></tr>
</table>

The stimulus is plain `INSERT`s into the port table, one row per cycle:

```sql
INSERT INTO incrementer_in VALUES
  (0, false, B'00000000'),
  (1, true,  B'00000101'),
  (2, true,  B'11111111'),
  (3, false, B'00000000'),
  (4, true,  B'00001010');
```

and both simulators produce this trace (`expected.csv`):

```csv
cycle,out_valid,dout
0,0,0
1,0,0
2,1,6
3,1,0
4,0,0
5,1,11
```

Row 0 is the reset state. Inputs present in stimulus row `k` are sampled at
edge `k+1` and appear in state row `k+1`; `B'11111111' + 1` wraps to 0 because
the cast back to `bit(8)` is the register width.

## The counter

`examples/counter` is a free-running 4-bit counter: `cnt` increments every
cycle and wraps at 16. It exists to show that a register that depends on no
input works, and that `bit(4)` wraps. The `en` port is declared so there is a
stimulus to run against; the logic deliberately ignores it.

```sql
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
```

## The language

A `design.sql` holds exactly two statements, in this order:

1. **The port table.** `CREATE TABLE <name>_in (...)` declares the inputs. It
   must have `cycle integer PRIMARY KEY` plus one `NOT NULL` column per input
   port; the column types are the port widths.
2. **The design view.** `CREATE VIEW <name>_out AS WITH RECURSIVE sim(...) AS
   (<base> UNION ALL <step>) SELECT ... FROM sim;`. The CTE must be named
   `sim`, its first column must be `cycle`, and every other column is a
   register (`output reg`). The base case is the reset state and must be
   literals. The outer `SELECT` lists `cycle` and the registers that become
   module outputs, in port order.

The step's `FROM` clause must be exactly

```sql
FROM sim s
JOIN <name>_in i ON i.cycle = s.cycle
[, LATERAL (SELECT <expr> AS <wire>, ...) <alias>]*
```

`s` is the current state, `i` the inputs for that cycle, and every `LATERAL`
column becomes a Verilog `wire`; laterals may reference `s`, `i` and earlier
lateral aliases. A lateral may not contain `FROM`.

### Types

| SQL type  | Verilog                | Notes                                   |
| --------- | ---------------------- | --------------------------------------- |
| `boolean` | `wire` / `reg` (1 bit) | control signals                         |
| `bit(n)`  | `[n-1:0]`              | data; `n` from 1 to 64                  |
| `integer` | error                  | only allowed for `cycle`                |

### Expressions

| SQL                                   | Verilog                     | Operand rule                               |
| ------------------------------------- | --------------------------- | ------------------------------------------ |
| `a AND b`, `a OR b`, `NOT a`          | `a && b`, `a \|\| b`, `!a`  | boolean only                               |
| `a <> b`, `a = b`                     | `a != b`, `a == b`          | same type both sides                       |
| `a & b`, `a \| b`, `a # b`, `~a`      | `a & b`, `a \| b`, `a ^ b`, `~a` | `bit(n)` same width                   |
| `x::int`, `x::bit(n)`                 | dropped                     | casts are width annotations, see below     |
| `x::int + y::int`, `x::int - y::int`  | `x + y`, `x - y`            | result must be wrapped in `::bit(n)`       |
| `CASE WHEN c THEN a ELSE b END`       | `c ? a : b`                 | nested `CASE` allowed; `ELSE` required     |
| `true`, `false`                       | `1'b1`, `1'b0`              |                                            |
| `B'0101'::bit(n)`                     | `n'b0101`                   | literal width must equal `n`               |
| `s.col`, `i.col`, `<alias>.col`       | identifier                  | state, input, wire                         |

Casts are width annotations, not operations. `(x::int + 1)::bit(8)` means
"8-bit add, wrap": the transpiler emits `x + 8'd1` and relies on the declared
width for the wrap. Arithmetic that is not immediately wrapped in `::bit(n)` is
an error, and every `::int` operand must have the width of the enclosing
`::bit(n)`.

Reserved names: `clk`, `rst`, `cycle`, `sim`, `s`, `i` and all Verilog
keywords. Names must match `[a-z][a-z0-9_]*`.

### Semantics

- Row `cycle = 0` of `sim` is the reset state; row `k+1` is computed from row
  `k` and the stimulus row `cycle = k`. In Verilog, `rst` is synchronous and
  active-high, asserted for exactly one clock before cycle 0.
- A register whose step expression is `s.<self>` holds; one that does not
  mention `s.<self>` is overwritten every cycle. No enables are inferred.
- The step joins on the stimulus with an inner join, so simulation ends when
  the stimulus runs out. Stimulus rows must be contiguous from 0.
- All columns are `NOT NULL`. A `NULL` reaching the trace is an error.
- Trace format: `cycle,<outputs in outer-SELECT order>`, booleans as `0`/`1`,
  `bit(n)` as unsigned decimal, one row per cycle from 0 to
  `max(stimulus.cycle) + 1`.

## Diagnostics

Errors are printed as `design.sql:LINE:COL: message` and exit with status 2.
Every row below has a test in `tests/test_parse.py`.

| Condition                                        | Message                                                      |
| ------------------------------------------------ | ------------------------------------------------------------ |
| file has other than two statements               | `expected CREATE TABLE then CREATE VIEW`                     |
| table lacks `cycle integer PRIMARY KEY`          | `port table needs cycle integer PRIMARY KEY`                 |
| column type not boolean / bit(n)                 | `unsupported type T for column C`                            |
| CTE not named `sim` or not recursive             | `design view must be WITH RECURSIVE sim(...)`                |
| step FROM clause has the wrong shape             | `step must be FROM sim s JOIN <name>_in i ON i.cycle = s.cycle` |
| unsupported operator or function                 | `unsupported expression: F`                                  |
| boolean operator on bit(n) or vice versa         | `type mismatch: ...`                                         |
| widths differ in a bitwise op or compare         | `width mismatch: N vs M`                                     |
| arithmetic not wrapped in `::bit(n)`             | `unbounded integer in next-state logic`                      |
| CASE without ELSE                                | `CASE needs ELSE`                                            |
| name reserved or not `[a-z][a-z0-9_]*`           | `invalid name: X`                                            |
| outer SELECT references a non-register           | `output X is not a register`                                 |

`sqrtl check` exits 1 and prints a unified diff when the PostgreSQL trace, the
Icarus trace and `expected.csv` (if present) disagree. Any `iverilog -Wall`
warning is also a failure.

## How it works

```
design.sql --sqlglot--> parse.py --> Design --> verilog.py --> <name>.v
                                      |
                                      +--> sim.py (psycopg, COPY ... CSV) --> trace_pg.csv
                                      +--> testbench.py --> tb.v --iverilog/vvp--> trace_iv.csv
```

- `sqrtl/parse.py` lowers the sqlglot AST into a tiny expression tree
  (`Ref`, `Lit`, `Un`, `Bin`, `Cond`) and rejects anything outside the subset.
- `sqrtl/verilog.py` prints a `Design` as Verilog. Output is deterministic;
  every binary expression is parenthesized and every literal is sized.
- `sqrtl/sim.py` runs the design in a throwaway schema and exports the trace
  and the stimulus as CSV with `COPY`.
- `sqrtl/check.py` renders a per-design testbench, runs `iverilog -Wall -g2005`
  and `vvp`, and diffs the traces.

## Development

The repository ships a devcontainer (`.devcontainer/`, built from the `dev`
target of the root `Dockerfile`) with Python, Icarus Verilog and a local
PostgreSQL 16 server. On start it launches PostgreSQL, ensures the `sqrtl`
database exists and installs the package in editable mode, so `sqrtl check
examples/incrementer` and `pytest` work immediately. The `ci` target of the
same Dockerfile is the minimal environment; both images are published to GHCR
by `.github/workflows/docker-build.yml`.

CI (`.github/workflows/ci.yml`) runs `pytest` against a PostgreSQL 16 service
container with `iverilog` installed, then `sqrtl check` on every directory under
`examples/`. The equivalence tests read the DSN from `SQRTL_DSN` and are skipped
locally when no server is reachable.

## Future work

Explicitly out of scope for v1:

- Multiple clock domains, CDC lint.
- Memories, register files, tables-as-RAM.
- `LAG()` / window-function pipelines.
- Functions as modules, hierarchy.
- `CHECK` constraints as assertions.
- Any optimization of the generated Verilog.
- A web demo, or support for any database other than PostgreSQL 15+.
- Three-valued logic as X-propagation.
