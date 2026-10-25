# sqrtl

**SQL + RTL, why the hell not.**

sqrtl is a toy hardware description language where the design file is a
PostgreSQL query. A design is one `CREATE TABLE` (the input ports) and one
`CREATE VIEW` whose body is a `WITH RECURSIVE` CTE: every row of the CTE is the
register state after one clock edge. PostgreSQL *is* the simulator, and a small
Python transpiler turns the same file into synthesizable SystemVerilog. The
proof of correctness is `sqrtl check`: the trace PostgreSQL produces and the
trace Icarus Verilog produces from the generated module (converted with sv2v)
must be byte-identical.

```sh
sqrtl sim       examples/incrementer      # run the design in Postgres, print the trace CSV
sqrtl transpile examples/incrementer      # design.sql -> SystemVerilog on stdout
sqrtl check     examples/incrementer      # sim + transpile + sv2v + iverilog + diff; exit 0 on match
```

What it does not do (v1): one clock, one synchronous reset, no memories, no
hierarchy, no window functions, no Verilog optimization, PostgreSQL 15+ only.
See [Future work](#future-work).

## Quick start

Requirements: Python 3.11+, a PostgreSQL 15+ server, Icarus Verilog
(`iverilog`, `vvp`) and [sv2v](https://github.com/zachjs/sv2v) on `PATH`.
`make tools` installs a pinned sv2v release; it is a tool for the check, not a
dependency of the package. `transpile` and `sim` need neither.

```sh
pip install -e '.[test]'
make tools                                      # sv2v
export SQRTL_DSN=postgresql://localhost/sqrtl   # default; any psycopg DSN works
make check                                      # sqrtl check on every example
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

```systemverilog
module incrementer (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    input  logic [7:0] din,
    output logic       out_valid,
    output logic [7:0] dout
);
    logic [7:0] din_plus1;
    always_comb din_plus1 = (din + 8'd1);

    always_ff @(posedge clk) begin
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

## The sequence detector

`examples/seqdet` is a three-state machine that pulses `detected` for one
cycle after the serial input `din` has carried `1-0-1`, overlaps allowed. The
state register is a PostgreSQL enum and the next-state logic is a simple
`CASE` on it:

```sql
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
```

The enum becomes a `typedef enum`, the register block an `always_ff`, and the
simple `CASE` a `case` statement:

```systemverilog
typedef enum logic [1:0] {IDLE = 2'd0, SAW_1 = 2'd1, SAW_10 = 2'd2} state_t;

module seqdet (
    input  logic   clk,
    input  logic   rst,
    input  logic   din,
    output state_t state,
    output logic   detected
);

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            detected <= 1'b0;
        end else begin
            case (state)
                IDLE: state <= (din ? SAW_1 : IDLE);
                SAW_1: state <= (din ? SAW_1 : SAW_10);
                default: state <= (din ? SAW_1 : IDLE);
            endcase
            detected <= ((state == SAW_10) && din);
        end
    end
endmodule
```

In the trace an enum is its ordinal (`idle` = 0), so `expected.csv` is
encoding-independent.

## Web UI

The repository also ships a browser playground, deployed to GitHub Pages from
`web/` by `.github/workflows/pages.yml`: a CodeMirror editor for `design.sql`
and `stim.sql` on the left, the transpiled SystemVerilog (read-only, one-click
copy or download) on the right, and a WaveDrom waveform of the simulation below, with inputs and outputs grouped and
enum registers shown by label. It runs entirely client-side: the real
transpiler executes under [Pyodide](https://pyodide.org) and the real
simulator is PostgreSQL compiled to WebAssembly via
[PGlite](https://pglite.dev), so what you see is the same `sqrtl sim` trace.
Edits re-run on the fly and diagnostics are underlined at `LINE:COL`.

```sh
npm ci
npm run build:web     # bundles CodeMirror + WaveDrom, vendors Pyodide + PGlite into web/dist
npm run serve:web     # http://localhost:8000
```

The Icarus half of `sqrtl check` is not part of the page; run it locally or
in CI.

## The language

A `design.sql` holds, in this order, any number of enum definitions and then
exactly two statements:

0. **Enums (optional).** `CREATE TYPE <name> AS ENUM ('label', ...)`. An enum
   column is `ceil(log2(n))` bits wide (at least 1) and becomes a
   `typedef enum logic [w-1:0] {...} <name>;` whose members are the labels in
   upper case (`'saw_1'` becomes `SAW_1`).
1. **The port table.** `CREATE TABLE <name>_in (...)` declares the inputs. It
   must have `cycle integer PRIMARY KEY` plus one `NOT NULL` column per input
   port; the column types are the port widths.
2. **The design view.** `CREATE VIEW <name>_out AS WITH RECURSIVE sim(...) AS
   (<base> UNION ALL <step>) SELECT ... FROM sim;`. The CTE must be named
   `sim`, its first column must be `cycle`, and every other column is a
   register (`output logic`). The base case is the reset state and must be
   literals. The outer `SELECT` lists `cycle` and the registers that become
   module outputs, in port order.

The step's `FROM` clause must be exactly

```sql
FROM sim s
JOIN <name>_in i ON i.cycle = s.cycle
[, LATERAL (SELECT <expr> AS <wire>, ...) <alias>]*
```

`s` is the current state, `i` the inputs for that cycle, and every `LATERAL`
column becomes an `always_comb` signal; laterals may reference `s`, `i` and earlier
lateral aliases. A lateral may not contain `FROM`.

### Types

| SQL type  | SystemVerilog          | Notes                                   |
| --------- | ---------------------- | --------------------------------------- |
| `boolean` | `logic` (1 bit)        | control signals                         |
| `bit(n)`  | `logic [n-1:0]`        | data; `n` from 1 to 64                  |
| enum type | `typedef enum logic [w-1:0]` | `w = ceil(log2(labels))`; compare and `CASE` only |
| `integer` | error                  | only allowed for `cycle`                |

### Expressions

| SQL                                   | SystemVerilog               | Operand rule                               |
| ------------------------------------- | --------------------------- | ------------------------------------------ |
| `a AND b`, `a OR b`, `NOT a`          | `a && b`, `a \|\| b`, `!a`  | boolean only                               |
| `a <> b`, `a = b`                     | `a != b`, `a == b`          | same type both sides                       |
| `a & b`, `a \| b`, `a # b`, `~a`      | `a & b`, `a \| b`, `a ^ b`, `~a` | `bit(n)` same width                   |
| `x::int`, `x::bit(n)`                 | dropped                     | casts are width annotations, see below     |
| `x::int + y::int`, `x::int - y::int`  | `x + y`, `x - y`            | result must be wrapped in `::bit(n)`       |
| `CASE WHEN c THEN a ELSE b END`       | `c ? a : b`                 | nested `CASE` allowed; `ELSE` required     |
| `CASE x WHEN lit THEN a ... ELSE b END` | `case (x) lit: ... default: ... endcase` | labels are literals of `x`'s type; `ELSE` required; a chain of `==` inside expressions |
| `'label'::enum_t`, `'label'`          | `LABEL`                     | a bare label takes its type from the other side of `=`, the typed branches of a `CASE`, or the register it feeds |
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
| label not in the enum                            | `unknown enum label 'x' for T`                               |
| bare label with nothing to infer its enum from   | `string literal 'x' has no enum context (write 'x'::T)`      |
| simple CASE label is not a literal of the subject's type | `CASE label must be a T literal`                     |
| simple CASE label repeated                       | `duplicate CASE label X`                                     |
| every branch of a CASE is a bare label           | `CASE needs a typed branch (write one label as 'label'::enum)` |

`sqrtl check` exits 1 and prints a unified diff when the PostgreSQL trace, the
Icarus trace and `expected.csv` (if present) disagree. Any sv2v or
`iverilog -Wall` diagnostic is also a failure.

## Output

The transpiler emits SystemVerilog, and only SystemVerilog:

| SQL | SystemVerilog |
| --- | --- |
| ports and registers | `logic`; enum-typed ones use their `typedef` |
| `LATERAL` columns | `logic x; always_comb x = expr;` (a `case` gets a `begin … end` block) |
| the recursive step | one `always_ff @(posedge clk)` with a synchronous `rst` |
| enums | `typedef enum logic [w-1:0] {…} name;` before the module |
| simple `CASE` | `case … endcase` |

What you do with it is up to you. For the equivalence check, `sqrtl check`
converts the output with [sv2v](https://github.com/zachjs/sv2v) and runs the
result through `iverilog -Wall -g2005`, so Icarus only ever sees plain Verilog
and any sv2v or iverilog diagnostic fails the check. sv2v is installed by
`make tools` (or `scripts/install-sv2v.sh`), pinned by version and checksum,
and is not a dependency of the Python package. `sqrtl check --keep DIR` leaves
the `.sv`, its conversion, the testbench and both traces behind.

## How it works

```
design.sql --sqlglot--> parse.py --> Design --> verilog.py --> <name>.sv --sv2v--> <name>.v
                                      |                                              |
                                      +--> sim.py (psycopg, COPY ... CSV) --> trace_pg.csv
                                      +--> testbench.py --> tb.v --------iverilog/vvp--> trace_iv.csv
```

- `sqrtl/parse.py` lowers the sqlglot AST into a tiny expression tree
  (`Ref`, `Lit`, `Un`, `Bin`, `Cond`, `Switch`) and rejects anything outside the subset.
- `sqrtl/verilog.py` prints a `Design` as SystemVerilog. Output is
  deterministic; every binary expression is parenthesized and every literal is
  sized. A simple `CASE` (`Switch` in the expression tree) becomes a `case`
  statement wherever a statement is allowed.
- `sqrtl/sim.py` runs the design in a throwaway schema and exports the trace
  and the stimulus as CSV with `COPY`.
- `sqrtl/check.py` renders a per-design testbench, converts the SystemVerilog
  with `sv2v`, runs `iverilog -Wall -g2005` and `vvp`, and diffs the traces.
- `sqrtl/trace.py` holds the trace-rendering SQL shared by `sim.py` and
  `sqrtl/web.py`, the entry point the web UI calls under Pyodide.

## Development

The repository ships a devcontainer (`.devcontainer/`, built from the `dev`
target of the root `Dockerfile`) with Python, Icarus Verilog and a local
PostgreSQL 16 server. On start it launches PostgreSQL, ensures the `sqrtl`
database exists and installs the package in editable mode, so `sqrtl check
examples/incrementer` and `pytest` work immediately. The `ci` target of the
same Dockerfile is the minimal environment; both images are published to GHCR
by `.github/workflows/docker-build.yml`.

CI (`.github/workflows/ci.yml`) runs `pytest` against a PostgreSQL 16 service
container with `iverilog` and `sv2v` installed, then `sqrtl check` on every
directory under `examples/`. The equivalence tests read the DSN from `SQRTL_DSN` and are skipped
locally when no server is reachable.

## Future work

Explicitly out of scope for v1:

- Multiple clock domains, CDC lint.
- Memories, register files, tables-as-RAM.
- `LAG()` / window-function pipelines.
- Functions as modules, hierarchy.
- `CHECK` constraints as assertions.
- Any optimization of the generated Verilog.
- Support for any database other than PostgreSQL 15+.
- Running the sv2v + Icarus equivalence check in the browser.
- A Verilog-2005 emitter (use sv2v on the output).
- Three-valued logic as X-propagation.
