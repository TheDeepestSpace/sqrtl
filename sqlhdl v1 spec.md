# sqlhdl v1 spec

Oct 24, 2026 · @Guy

## Goal and non-goals

sqlhdl is a toy hardware description language where the design file is a PostgreSQL query: Postgres is the simulator, and a small transpiler turns the same file into synthesizable Verilog. v1 is complete when the reference incrementer example simulates in Postgres, transpiles to Verilog, and both traces match in CI.

v1 is:

- Single clock, single synchronous reset.
- One recursive CTE per design.
- A Python transpiler (`sqlhdl`) to Verilog-2005.
- An equivalence check: Postgres trace vs. Icarus Verilog trace, row for row.

v1 is not (list these in the README as future work, do not build them):

- Multiple clock domains, CDC lint.
- Memories, register files, tables-as-RAM.
- `LAG()` / window-function pipelines.
- Functions as modules, hierarchy.
- CHECK constraints as assertions.
- Any optimization of the generated Verilog.
- A web demo, or support for any database other than PostgreSQL 15+.

Dependencies: Python 3.11+, `sqlglot` (parsing), `psycopg` (running Postgres), `iverilog` and a Postgres server available in CI (GitHub Actions service container is fine). No custom SQL parser.

## Repo layout and deliverables

```
sqlhdl/
  sqlhdl/
    __init__.py
    parse.py        # sqlglot -> Design dataclass
    verilog.py      # Design -> Verilog text
    sim.py          # run a design + stimulus in Postgres, dump trace CSV
    cli.py          # `sqlhdl transpile`, `sqlhdl sim`, `sqlhdl check`
  examples/
    incrementer/
      design.sql    # the design (CREATE TABLE + CREATE VIEW)
      stim.sql      # INSERT INTO <design>_in ...
      expected.csv  # hand-written expected trace
    counter/        # same three files, a free-running 4-bit counter
  tb/
    harness.v       # generic Icarus testbench: reads stim CSV, drives DUT, writes trace CSV
  tests/
    test_parse.py
    test_verilog.py
    test_equiv.py   # runs `sqlhdl check` on every example
  .github/workflows/ci.yml
  README.md
```

Deliverables, in order of priority:

1. `sqlhdl sim examples/incrementer` prints the trace table and matches `expected.csv`.
2. `sqlhdl transpile examples/incrementer` writes `incrementer.v` that compiles under `iverilog -Wall` with no warnings.
3. `sqlhdl check examples/incrementer` runs both and diffs them; exit 0 on match, exit 1 with a unified diff on mismatch.
4. CI runs `check` on every directory under `examples/`.
5. README with the one-paragraph pitch, the two examples, the subset table, and the future-work list.

## The language

A design is one `design.sql` file containing exactly two statements, in this order. Anything else in the file is a transpiler error.

**1. The port table.** `CREATE TABLE <name>_in (...)` declares the inputs. It must have `cycle integer PRIMARY KEY` plus one column per input port. Column types are the port widths.

**2. The design view.** `CREATE VIEW <name>_out AS WITH RECURSIVE sim(...) AS (<base> UNION ALL <step>) SELECT ... FROM sim;` declares the registers and the logic. The CTE must be named `sim`. Its first column must be `cycle`; every other column is a register (a Verilog `output reg`). The outer `SELECT` must select `cycle` and a subset of the register columns, in any order; those are the module's outputs.

**Types and widths**

| SQL type | Verilog | Notes |
| --- | --- | --- |
| `boolean` | `wire` / `reg` (1 bit) | control signals |
| `bit(n)` | `[n-1:0]` | data; `n` from 1 to 64 |
| `integer` | error | only allowed for `cycle` |

**Expressions.** The step's `SELECT` list and `LATERAL` subqueries may use only:

| SQL | Verilog | Operand rule |
| --- | --- | --- |
| `a AND b`, `a OR b`, `NOT a` | `a && b`, `a \|\| b`, `!a` | boolean only |
| `a <> b`, `a = b` | `a != b`, `a == b` | same type both sides |
| `a & b`, `a \| b`, `a # b`, `~a` | `a & b`, `a \| b`, `a ^ b`, `~a` | `bit(n)` same width |
| `x::int`, `x::bit(n)` | dropped | see below |
| `x::int + y::int`, ` -  ` | `x + y`, `x - y` | result must be cast back with `::bit(n)` |
| `CASE WHEN c THEN a ELSE b END` | `c ? a : b` | nested CASE allowed; `ELSE` required |
| `true`, `false` | `1'b1`, `1'b0` |  |
| `B'0101'::bit(n)` | `n'b0101` | literal width must equal `n` |
| `s.col`, `i.col`, `<alias>.col` | identifier | `s` = current state, `i` = inputs |

Casts are width annotations, not operations. `(x::int + 1)::bit(8)` means "8-bit add, wrap"; the transpiler emits `x + 8'd1` and relies on Verilog's declared width for the wrap. An arithmetic expression that is not immediately wrapped in `::bit(n)` is an error ("unbounded integer in next-state logic").

**FROM clause of the step.** Must be exactly:

```sql
FROM sim s
JOIN <name>_in i ON i.cycle = s.cycle
[, LATERAL (SELECT <expr> AS <wire>, ...) <alias>]*
```

Each `LATERAL` column becomes a Verilog `wire`. Laterals may reference `s`, `i`, and any earlier lateral alias; left-to-right order is the emission order. A lateral may not contain `FROM`.

**Reserved names.** `clk`, `rst`, `cycle`, `sim`, `s`, `i`, and all Verilog keywords are reserved and may not be used as column or alias names. Column names must match `[a-z][a-z0-9_]*`.

**Comments.** `--` comments are preserved as `//` comments on the following line of Verilog where it is cheap to do so; not required for v1.

## Semantics

One row of `sim` is the register state after one rising clock edge. Row `cycle = 0` is the reset state. Row `k+1` is computed from row `k` and the stimulus row with `cycle = k`.

**Timing.** Inputs present in stimulus row `k` are sampled at edge `k+1` and appear in state row `k+1`. So a registered output of a value driven at cycle `k` is visible at cycle `k+1`. This is the only latency model; there are no combinational outputs in v1.

**Reset.** The base case of the CTE is the reset state. In Verilog this is a synchronous active-high `rst` port, asserted for exactly one clock before cycle 0 by the testbench. The stimulus table never contains a reset column.

**Simulation length.** The step joins on `<name>_in` with an inner join, so the recursion ends when there is no stimulus row for `s.cycle`. Stimulus rows must be contiguous from 0; a gap is a `sim` error ("stimulus has gap at cycle N"), checked before running.

**Hold.** A register whose step expression is `s.<self>` holds its value. A register whose expression does not mention `s.<self>` is overwritten every cycle. Both are legal; the transpiler does not infer enables.

**No X.** All columns are `NOT NULL`. A `NULL` reaching the trace is a `sim` error. (Three-valued logic as X-propagation is future work.)

**Trace format.** `sqlhdl sim` and the Icarus harness both emit the same CSV: header row is `cycle,<outputs in outer-SELECT order>`; booleans as `0`/`1`; `bit(n)` as an unsigned decimal integer; one row per cycle starting at 0, last row is `max(stimulus.cycle) + 1`. Two traces are equal if the files are byte-identical after normalizing line endings.

## Transpiler

**CLI**

```
sqlhdl transpile <example_dir> [-o out.v]   # design.sql -> Verilog (stdout by default)
sqlhdl sim       <example_dir> [-o trace.csv] [--dsn postgresql://...]
sqlhdl check     <example_dir>              # sim + transpile + iverilog + diff; exit 0/1
```

`--dsn` defaults to `$SQLHDL_DSN`, then `postgresql://localhost/sqlhdl`. `sim` runs the design in a temporary schema and drops it afterwards.

**Pipeline.** `parse.py` turns `design.sql` into a `Design` dataclass; `verilog.py` turns a `Design` into text. Nothing else touches sqlglot.

```python
@dataclass
class Signal:
    name: str
    width: int            # 1 for boolean
    is_bool: bool

@dataclass
class Design:
    name: str             # from <name>_in / <name>_out
    inputs: list[Signal]
    regs: list[Signal]    # CTE columns minus cycle, in CTE order
    outputs: list[str]    # names, outer-SELECT order
    reset: dict[str, Expr]      # reg name -> literal
    wires: list[tuple[str, Expr]]  # lateral columns, emission order
    next: dict[str, Expr]       # reg name -> step expression
```

`Expr` is a small tree the transpiler owns (not sqlglot nodes): `Ref(name)`, `Lit(value, width)`, `Un(op, a)`, `Bin(op, a, b)`, `Cond(c, a, b)`. `parse.py` lowers sqlglot's AST into it and rejects anything outside the subset at that point. Width and type checking happen on `Expr`, not on sqlglot nodes.

**Emission rules**

1. Module name is `<name>`. Port order: `clk`, `rst`, inputs in table order, outputs in outer-SELECT order.
2. Inputs are `input wire`, outputs are `output reg`. Registers that are not outputs are plain `reg`s declared after the ports.
3. Each lateral column is a `wire <name> = <expr>;`, in order.
4. One `always @(posedge clk)` block. `if (rst) begin <reset assignments> end else begin <next assignments> end`. All assignments non-blocking. One register per line, in CTE order.
5. Literal widths are always explicit: `1'b1`, `8'd0`, `4'b0101`. Never emit an unsized literal.
6. Every binary expression is fully parenthesized. Readability is not a goal; `iverilog -Wall` silence is.
7. Output is deterministic: same input file, byte-identical output.

**Errors.** All diagnostics are `SqlhdlError(message, line, col)` and the CLI prints them as `design.sql:LINE:COL: message` and exits 2. Required diagnostics, each with a test:

| Condition | Message |
| --- | --- |
| file has other than two statements | `expected CREATE TABLE then CREATE VIEW` |
| table lacks `cycle integer PRIMARY KEY` | `port table needs cycle integer PRIMARY KEY` |
| column type not boolean / bit(n) | `unsupported type T for column C` |
| CTE not named `sim` or not recursive | `design view must be WITH RECURSIVE sim(...)` |
| step FROM clause does not match the required shape | `step must be FROM sim s JOIN <name>_in i ON i.cycle = s.cycle` |
| unsupported operator or function | `unsupported expression: F` |
| boolean operator on bit(n) or vice versa | `type mismatch: ...` |
| widths differ in a bitwise op or compare | `width mismatch: N vs M` |
| arithmetic not wrapped in ::bit(n) | `unbounded integer in next-state logic` |
| CASE without ELSE | `CASE needs ELSE` |
| name reserved or not `[a-z][a-z0-9_]*` | `invalid name: X` |
| outer SELECT references a non-register | `output X is not a register` |

**Out of scope for the transpiler:** any transformation of the Verilog, any attempt to infer enables, resets or clock enables, and any support for SQL outside the subset even when sqlglot would parse it.

## Equivalence check

`sqlhdl check <dir>` is the project's proof of correctness: the Postgres trace and the Icarus trace of the generated Verilog must be byte-identical.

**Steps**

1. `sim`: load `design.sql` and `stim.sql` into a fresh schema, `COPY (SELECT ... FROM <name>_out ORDER BY cycle) TO STDOUT CSV HEADER` with booleans rendered as `0`/`1` and `bit(n)` as `::int`, write `trace_pg.csv`.
2. `transpile`: write `<name>.v` to a temp dir.
3. Export the stimulus as `stim.csv` with the same header convention (`cycle,<inputs in table order>`), from the same Postgres schema, so both simulators read the same bytes.
4. Generate `tb.v` from a template: it reads `stim.csv` with `$fscanf`, holds `rst` high for one clock, then for each row drives the inputs, pulses `clk`, and after the edge writes `cycle,<outputs>` to `trace_iv.csv` with `$fwrite`. Output values are written as `%0d`.
5. `iverilog -Wall -g2005 -o sim.vvp <name>.v tb.v && vvp sim.vvp`. Any `iverilog` warning is a failure.
6. Diff `trace_pg.csv` against `trace_iv.csv` and against `expected.csv` if present. Print a unified diff on mismatch.

**Testbench template.** One generic `tb/harness.v` is not enough because port lists differ per design, so `check` renders `tb.v` from a Python string template with the input and output lists filled in. Keep it under 60 lines. Use `$fscanf(fd, "%d,%d,%d\n", ...)` with one `%d` per column; read the header line with `$fgets` and discard it.

**Timing in the testbench.** Use a 10 ns period. Drive inputs on the falling edge, sample outputs 1 ns after the rising edge. Reset: `rst = 1` for one full period before the first stimulus row, then `rst = 0`. The row written after the reset clock is `cycle 0`.

**CI.** `.github/workflows/ci.yml`: Ubuntu, Postgres 16 service container, `apt install iverilog`, `pip install -e .[test]`, `pytest`. `tests/test_equiv.py` parametrizes over every directory in `examples/` that contains `design.sql` and calls `check` on it. Target: whole workflow under three minutes.

## Reference example: incrementer

The incrementer adds 1 to `din` when `in_valid` is high and presents the result one cycle later with `out_valid`. It is the first example to make pass, and the fixture for most unit tests.

**examples/incrementer/design.sql**

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
           CASE WHEN i.in_valid THEN n.din_plus1 ELSE s.dout END
    FROM sim s
    JOIN incrementer_in i ON i.cycle = s.cycle,
    LATERAL (SELECT (i.din::int + 1)::bit(8) AS din_plus1) n
)
SELECT cycle, out_valid, dout FROM sim;
```

**examples/incrementer/stim.sql**

```sql
INSERT INTO incrementer_in VALUES
  (0, false, B'00000000'),
  (1, true,  B'00000101'),
  (2, true,  B'11111111'),
  (3, false, B'00000000'),
  (4, true,  B'00001010');
```

**examples/incrementer/expected.csv**

```csv
cycle,out_valid,dout
0,0,0
1,0,0
2,1,6
3,1,0
4,0,0
5,1,11
```

**Expected transpiler output** (byte-exact target for `tests/test_verilog.py`)

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

**Second example: counter.** A free-running 4-bit counter with an `en` input and no other logic; `design.sql`, `stim.sql` with 20 cycles of `en` toggling, and `expected.csv`. It exists to prove that a register that does not depend on any input still works and that `bit(4)` wraps at 16.

## Milestones and acceptance criteria

Work in this order; each milestone is a PR that leaves CI green.

1. **Scaffold.** Package layout, `pyproject.toml` with `sqlglot` and `psycopg`, empty CLI with the three subcommands, CI that runs `pytest` against a Postgres service and installs `iverilog`. Done when CI is green with zero tests.
2. **Sim.** `sqlhdl sim examples/incrementer` prints the trace and it equals `expected.csv`. Done when `test_equiv.py::test_sim_matches_expected[incrementer]` passes. No transpiler yet.
3. **Parse.** `parse.py` produces the `Design` for the incrementer, and every row of the error table has a failing-input test. Done when `test_parse.py` passes.
4. **Verilog.** `verilog.py` emits the byte-exact reference output. Done when `test_verilog.py` passes and `iverilog -Wall` is silent.
5. **Check.** Testbench template, `sqlhdl check`, full equivalence on the incrementer. Done when `test_equiv.py::test_check[incrementer]` passes.
6. **Counter.** Add the second example and make it pass without changing the transpiler. If it needs a transpiler change, that is a bug in milestone 3 or 4, fix it there.
7. **README.** Pitch, the incrementer in SQL and in Verilog side by side, the subset table, the error table, the future-work list from the Goal section.

**Acceptance for v1 overall**

- [ ] `sqlhdl check` passes on both examples locally and in CI.
- [ ] Generated Verilog compiles with `iverilog -Wall -g2005` with no warnings.
- [ ] Every error-table row has a test.
- [ ] Transpiler output is deterministic (run twice, `diff` is empty).
- [ ] README answers "what is this, how do I run it, what does it not do" in the first screen.

**Working agreements for the agent**

- Ask before adding a dependency beyond `sqlglot`, `psycopg`, `pytest`.
- Do not extend the SQL subset to make a test pass; tighten the test or the example instead.
- If sqlglot cannot represent something in this spec (e.g. `LATERAL` shape, `bit(n)` literals), stop and report rather than working around it with string manipulation.
- Keep `parse.py` and `verilog.py` free of I/O; `cli.py` and `sim.py` own all file and database access.
