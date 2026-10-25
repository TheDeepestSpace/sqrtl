"""parse.py: the incrementer lowers to the expected Design, and every row of
the spec's error table has a failing input."""

import re

import pytest

from sqrtl.parse import Bin, Cond, Lit, Ref, SqrtlError, Switch, Un, parse_design

INC = """\
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
"""


def variant(old: str, new: str, src: str = INC) -> str:
    assert old in src, old
    return src.replace(old, new, 1)


def with_dout(expr: str, lateral: str = "(i.din::int + 1)::bit(8) AS din_plus1") -> str:
    src = variant("CASE WHEN i.in_valid THEN n.din_plus1 ELSE s.dout END", expr)
    return variant("(i.din::int + 1)::bit(8) AS din_plus1", lateral, src)


def fails(src: str, message: str) -> SqrtlError:
    with pytest.raises(SqrtlError) as info:
        parse_design(src)
    assert info.value.message == message, info.value.message
    assert info.value.line is not None and info.value.col is not None
    return info.value


# -- happy path -------------------------------------------------------------

def test_incrementer_design(incrementer_sql):
    assert incrementer_sql == INC
    d = parse_design(INC)
    assert d.name == "incrementer"
    assert [(s.name, s.width, s.is_bool) for s in d.inputs] == [("in_valid", 1, True), ("din", 8, False)]
    assert [(s.name, s.width, s.is_bool) for s in d.regs] == [("out_valid", 1, True), ("dout", 8, False)]
    assert d.outputs == ["out_valid", "dout"]
    assert d.reset == {"out_valid": Lit(0, 1, "b"), "dout": Lit(0, 8, "d")}
    assert d.wires == [("din_plus1", Bin("+", Ref("din"), Lit(1, 8, "d")))]
    assert d.next == {
        "out_valid": Ref("in_valid"),
        "dout": Cond(Ref("in_valid"), Ref("din_plus1"), Ref("dout")),
    }


def test_outputs_follow_outer_select_order_and_subset():
    d = parse_design(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT dout, cycle FROM sim"))
    assert d.outputs == ["dout"]
    assert [r.name for r in d.regs] == ["out_valid", "dout"]


def test_full_operator_subset_lowers():
    src = with_dout(
        "CASE WHEN (i.in_valid AND NOT s.out_valid) OR i.din = s.dout THEN n.a "
        "WHEN i.din <> s.dout THEN n.b ELSE n.c END",
        "(i.din::int - 1)::bit(8) AS a, (s.dout & i.din) | (~s.dout # B'10101010'::bit(8)) AS b, "
        "CASE WHEN true THEN B'00000001'::bit(8) ELSE s.dout END AS c",
    )
    d = parse_design(src)
    assert [w for w, _ in d.wires] == ["a", "b", "c"]
    a, b, c = (e for _, e in d.wires)
    assert a == Bin("-", Ref("din"), Lit(1, 8, "d"))
    assert b.op == "|" and b.b.op == "^" and b.b.b == Lit(0b10101010, 8, "b")
    assert c == Cond(Lit(1, 1, "b"), Lit(1, 8, "b"), Ref("dout"))
    nxt = d.next["dout"]
    assert isinstance(nxt, Cond) and isinstance(nxt.b, Cond)
    assert nxt.c.op == "||" and nxt.c.a.op == "&&" and nxt.c.a.b.op == "!"
    assert nxt.c.b.op == "==" and nxt.b.c.op == "!="


def test_laterals_see_earlier_laterals_in_order():
    src = with_dout("m.y", "(i.din::int + 1)::bit(8) AS x) n, LATERAL (SELECT (n.x::int + 1)::bit(8) AS y) m, LATERAL (SELECT m.y # n.x AS z) p")
    src = src.replace("AS z) p) n", "AS z) p")
    d = parse_design(src)
    assert [w for w, _ in d.wires] == ["x", "y", "z"]
    assert d.next["dout"] == Ref("y")


def test_cross_join_lateral_and_swapped_on_are_accepted():
    src = variant("JOIN incrementer_in i ON i.cycle = s.cycle,\n    LATERAL",
                  "JOIN incrementer_in i ON s.cycle = i.cycle\n    CROSS JOIN LATERAL")
    assert parse_design(src).next["dout"] == Cond(Ref("in_valid"), Ref("din_plus1"), Ref("dout"))


def test_table_level_primary_key_and_bare_bit():
    src = variant("cycle    integer PRIMARY KEY,\n    in_valid boolean NOT NULL,\n    din      bit(8)  NOT NULL",
                  "cycle integer, in_valid boolean NOT NULL, din bit(8) NOT NULL, flag bit NOT NULL, PRIMARY KEY (cycle)")
    d = parse_design(src)
    assert [(s.name, s.width) for s in d.inputs] == [("in_valid", 1), ("din", 8), ("flag", 1)]


# -- the error table --------------------------------------------------------

def test_err_statement_count():
    fails(INC + "\nCREATE TABLE extra (x integer);\n", "expected CREATE TABLE then CREATE VIEW")
    fails(INC.split("CREATE VIEW")[0], "expected CREATE TABLE then CREATE VIEW")
    fails("CREATE VIEW v AS SELECT 1;\nCREATE TABLE t (cycle integer PRIMARY KEY);\n",
          "expected CREATE TABLE then CREATE VIEW")


def test_err_cycle_primary_key():
    fails(variant("cycle    integer PRIMARY KEY", "cycle integer"), "port table needs cycle integer PRIMARY KEY")
    fails(variant("cycle    integer PRIMARY KEY", "cycle bigint PRIMARY KEY"), "port table needs cycle integer PRIMARY KEY")
    fails(variant("cycle    integer PRIMARY KEY,\n", ""), "port table needs cycle integer PRIMARY KEY")


def test_err_unsupported_type():
    fails(variant("din      bit(8)  NOT NULL", "din integer NOT NULL"), "unsupported type integer for column din")
    fails(variant("din      bit(8)  NOT NULL", "din text NOT NULL"), "unsupported type text for column din")
    fails(variant("din      bit(8)  NOT NULL", "din bit(65) NOT NULL"), "unsupported type bit(65) for column din")
    fails(variant("din      bit(8)  NOT NULL", "din bit(0) NOT NULL"), "unsupported type bit(0) for column din")


def test_err_recursive_cte():
    fails(variant("WITH RECURSIVE sim(cycle, out_valid, dout)", "WITH RECURSIVE state(cycle, out_valid, dout)"),
          "design view must be WITH RECURSIVE sim(...)")
    fails(variant("WITH RECURSIVE sim(cycle, out_valid, dout)", "WITH sim(cycle, out_valid, dout)"),
          "design view must be WITH RECURSIVE sim(...)")
    fails(variant("WITH RECURSIVE sim(cycle, out_valid, dout)", "WITH RECURSIVE sim"),
          "design view must be WITH RECURSIVE sim(...)")


@pytest.mark.parametrize("old, new", [
    ("FROM sim s\n    JOIN incrementer_in i ON i.cycle = s.cycle", "FROM sim s\n    JOIN incrementer_in i ON i.cycle = s.cycle + 1"),
    ("FROM sim s\n    JOIN incrementer_in i", "FROM sim s\n    LEFT JOIN incrementer_in i"),
    ("FROM sim s\n    JOIN incrementer_in i", "FROM sim t\n    JOIN incrementer_in i"),
    ("FROM sim s\n    JOIN incrementer_in i", "FROM sim s\n    JOIN other_in i"),
    ("FROM sim s\n    JOIN incrementer_in i", "FROM sim s\n    JOIN incrementer_in j"),
    ("FROM sim s\n    JOIN incrementer_in i ON i.cycle = s.cycle,\n    LATERAL", "FROM sim s,\n    LATERAL"),
    ("LATERAL (SELECT (i.din::int + 1)::bit(8) AS din_plus1) n", "(SELECT B'00000001'::bit(8) AS din_plus1) n"),
])
def test_err_step_shape(old, new):
    fails(variant(old, new), "step must be FROM sim s JOIN incrementer_in i ON i.cycle = s.cycle")


def test_err_unsupported_expression():
    e = fails(with_dout("n.din_plus1", "coalesce(i.din, s.dout) AS din_plus1"), "unsupported expression: COALESCE")
    assert (e.line, e.col) == (16, 21)
    fails(with_dout("CASE WHEN i.din < s.dout THEN n.din_plus1 ELSE s.dout END"), "unsupported expression: i.din < s.dout")
    fails(with_dout("n.din_plus1", "(i.din::int * 2)::bit(8) AS din_plus1"), "unsupported expression: i.din::int * 2")
    fails(with_dout("n.din_plus1", "i.din::text::bit(8) AS din_plus1"), "unsupported expression: ::text")
    fails(with_dout("n.din_plus1", "NULL::bit(8) AS din_plus1"), "unsupported expression: NULL")


def test_err_type_mismatch():
    fails(with_dout("CASE WHEN i.din AND i.in_valid THEN n.din_plus1 ELSE s.dout END"),
          "type mismatch: AND needs boolean operands, got bit(8)")
    fails(with_dout("n.din_plus1", "i.in_valid & i.in_valid AS din_plus1"),
          "type mismatch: & needs bit(n) operands, got boolean")
    fails(with_dout("n.din_plus1", "~i.in_valid AS din_plus1"),
          "type mismatch: ~ needs a bit(n) operand, got boolean")
    fails(with_dout("CASE WHEN NOT i.din THEN n.din_plus1 ELSE s.dout END"),
          "type mismatch: NOT needs a boolean operand, got bit(8)")
    fails(with_dout("CASE WHEN i.din = i.in_valid THEN n.din_plus1 ELSE s.dout END"),
          "type mismatch: cannot compare bit(8) with boolean")
    fails(with_dout("CASE WHEN i.in_valid THEN i.in_valid ELSE s.dout END"),
          "type mismatch: CASE branches differ: bit(8) vs boolean")
    fails(with_dout("CASE WHEN i.din THEN n.din_plus1 ELSE s.dout END"),
          "type mismatch: WHEN needs a boolean condition, got bit(8)")
    fails(with_dout("i.in_valid"), "type mismatch: dout is bit(8) but expression is boolean")
    fails(with_dout("n.din_plus1", "(i.in_valid::int + 1)::bit(8) AS din_plus1"),
          "type mismatch: ::int needs a bit(n) operand, got boolean")


def test_err_width_mismatch():
    fails(with_dout("n.din_plus1", "i.din & B'0101'::bit(4) AS din_plus1"), "width mismatch: 8 vs 4")
    fails(with_dout("CASE WHEN i.din = B'0101'::bit(4) THEN n.din_plus1 ELSE s.dout END"), "width mismatch: 8 vs 4")
    fails(with_dout("n.din_plus1", "B'0101'::bit(8) AS din_plus1"), "width mismatch: 4 vs 8")
    fails(with_dout("n.din_plus1", "(i.din::int + 1)::bit(4) AS din_plus1"), "width mismatch: 8 vs 4")
    fails(with_dout("n.din_plus1", "(i.din::int + 1)::bit(8) AS din_plus1, B'0101'::bit(8) AS q"), "width mismatch: 4 vs 8")
    fails(with_dout("n.din_plus1", "(i.din::int + 1)::bit(4) AS din_plus1"), "width mismatch: 8 vs 4")
    fails(variant("B'00000000'::bit(8)", "B'0000'::bit(8)"), "width mismatch: 4 vs 8")


def test_err_unbounded_integer():
    fails(with_dout("n.din_plus1", "i.din::int + 1 AS din_plus1"), "unbounded integer in next-state logic")
    fails(with_dout("n.din_plus1", "i.din::int AS din_plus1"), "unbounded integer in next-state logic")
    fails(with_dout("CASE WHEN i.din::int = 5 THEN n.din_plus1 ELSE s.dout END"), "unbounded integer in next-state logic")
    fails(with_dout("CASE WHEN i.in_valid THEN 1 ELSE s.dout END"), "unbounded integer in next-state logic")
    fails(with_dout("n.din_plus1", "s.cycle AS din_plus1"), "unbounded integer in next-state logic")


def test_err_case_needs_else():
    fails(with_dout("CASE WHEN i.in_valid THEN n.din_plus1 END"), "CASE needs ELSE")


@pytest.mark.parametrize("old, new, bad", [
    ("in_valid boolean", "Valid boolean", "Valid"),
    ("in_valid boolean", "clk boolean", "clk"),
    ("in_valid boolean", "wire boolean", "wire"),
    ("in_valid boolean", "_x boolean", "_x"),
    ("sim(cycle, out_valid, dout)", "sim(cycle, out_valid, rst)", "rst"),
    ("AS din_plus1) n", "AS always) n", "always"),
    ("AS din_plus1) n", "AS din_plus1) i", "i"),
    ("incrementer_in", "Incrementer_in", "Incrementer"),
])
def test_err_invalid_name(old, new, bad):
    src = INC.replace(old, new) if old == "incrementer_in" else variant(old, new)
    fails(src, f"invalid name: {bad}")


def test_err_output_not_register():
    fails(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT cycle, out_valid, din FROM sim"),
          "output din is not a register")
    fails(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT cycle, din_plus1 FROM sim"),
          "output din_plus1 is not a register")


# -- other diagnostics ------------------------------------------------------

def test_err_misc():
    fails(variant("UNION ALL", "UNION"), "sim must be <base> UNION ALL <step>")
    fails(variant("SELECT 0, false, B'00000000'::bit(8)", "SELECT 0, false"), "base case has 2 columns, sim has 3")
    fails(variant("SELECT 0, false, B'00000000'::bit(8)", "SELECT 1, false, B'00000000'::bit(8)"), "base case must start with cycle 0")
    fails(variant("SELECT 0, false, B'00000000'::bit(8)", "SELECT 0, false, s.dout"), "reset value of dout must be true, false, B'...'::bit(n) or 'label'::enum")
    fails(variant("SELECT s.cycle + 1,", "SELECT s.cycle + 2,"), "step must start with s.cycle + 1")
    fails(with_dout("s.nope"), "unknown column: s.nope")
    fails(with_dout("i.dout"), "unknown column: i.dout")
    fails(with_dout("q.din_plus1"), "unknown column: q.din_plus1")
    fails(with_dout("dout"), "column dout must be qualified with s., i. or a lateral alias")
    fails(variant("in_valid boolean NOT NULL", "in_valid boolean"), "column in_valid must be NOT NULL")
    fails(variant("in_valid boolean NOT NULL", "dout boolean NOT NULL"), "duplicate name: dout")
    fails(with_dout("n.din_plus1", "(i.din::int + 1)::bit(8) AS din_plus1, s.dout AS din"), "duplicate name: din")
    fails(variant("CREATE VIEW incrementer_out", "CREATE VIEW other_out"),
          "port table and design view names differ: incrementer_in vs other_out")
    fails(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT out_valid, dout FROM sim"), "outer SELECT must include cycle")
    fails(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT * FROM sim"), "outer SELECT must list cycle and register columns")
    fails(with_dout("n.din_plus1", "(i.din::int + 256)::bit(8) AS din_plus1"), "literal 256 does not fit in bit(8)")
    fails(with_dout("n.din_plus1", "(i.din::int + 1)::bit(8) AS din_plus1 FROM sim"), "lateral may not contain FROM")
    e = fails(variant("UNION ALL", "UNION ALL SELECT ("), "syntax error: Expecting )")
    assert e.line is not None


def test_error_positions_point_at_the_offending_token():
    e = fails(with_dout("CASE WHEN i.in_valid THEN n.din_plus1 END"), "CASE needs ELSE")
    assert (e.line, e.col) == (13, 22)  # first positioned token inside the CASE
    e = fails(variant("din      bit(8)  NOT NULL", "din text NOT NULL"), "unsupported type text for column din")
    assert (e.line, e.col) == (4, 5)
    e = fails(variant("SELECT cycle, out_valid, dout FROM sim", "SELECT cycle, out_valid, din FROM sim"), "output din is not a register")
    assert (e.line, e.col) == (18, 26)
    assert re.match(r"\d+:\d+: output din is not a register", str(e))


# -- web.py: the browser entry point --------------------------------------

def test_web_analyze_roundtrip():
    import json
    from sqrtl.web import analyze, analyze_json

    r = json.loads(analyze_json(INC))
    assert r == analyze(INC)
    assert r["ok"] and r["name"] == "incrementer"
    assert [s["name"] for s in r["inputs"]] == ["in_valid", "din"]
    assert r["outputs"] == ["out_valid", "dout"]
    assert r["wires"] == [{"name": "din_plus1", "width": 8, "is_bool": False, "enum": None, "is_clock": False}]
    assert r["clocks"] == ["clk"] and r["warnings"] == [] and r["domain"] == {"out_valid": "clk", "dout": "clk"}
    assert r["enums"] == {}
    assert "verilog" not in r
    assert r["systemverilog"].startswith("module incrementer (\n    input  logic")
    assert r["trace_select"] == "SELECT cycle, out_valid::int AS out_valid, dout::bigint AS dout FROM incrementer_out ORDER BY cycle"
    assert r["stim_select"] == "SELECT cycle, in_valid::int AS in_valid, din::bigint AS din FROM incrementer_in ORDER BY cycle"

    bad = analyze(variant("CASE WHEN i.in_valid THEN n.din_plus1 ELSE s.dout END", "CASE WHEN i.in_valid THEN n.din_plus1 END"))
    assert bad == {"ok": False, "error": {"message": "CASE needs ELSE", "line": 13, "col": 22}}


# -- enums and simple CASE ----------------------------------------------------

SEQDET = (pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent / "examples" / "seqdet" / "design.sql").read_text()

IDLE, SAW_1, SAW_10 = Lit(0, 2, "d", "IDLE"), Lit(1, 2, "d", "SAW_1"), Lit(2, 2, "d", "SAW_10")


def test_enum_state_machine_lowers_to_switch():
    d = parse_design(SEQDET)
    assert d.enums == {"state_t": ["idle", "saw_1", "saw_10"]}
    state = d.regs[0]
    assert (state.name, state.width, state.is_bool, state.enum) == ("state", 2, False, "state_t")
    assert d.reset["state"] == IDLE
    nxt = d.next["state"]
    assert isinstance(nxt, Switch) and nxt.subject == Ref("state")
    assert [lit for lit, _ in nxt.arms] == [IDLE, SAW_1]
    assert nxt.arms[0][1] == Cond(Ref("din"), SAW_1, IDLE)
    assert nxt.arms[1][1] == Cond(Ref("din"), SAW_1, SAW_10)
    assert nxt.default == Cond(Ref("din"), SAW_1, IDLE)
    assert d.next["detected"] == Bin("&&", Bin("==", Ref("state"), SAW_10), Ref("din"))


def test_enum_width_and_members():
    from sqrtl.parse import Design
    assert Design.enum_width(["a"]) == 1
    assert Design.enum_width(["a", "b"]) == 1
    assert Design.enum_width(["a", "b", "c"]) == 2
    assert Design.enum_width([str(i) for i in range(17)]) == 5
    assert Design.member("saw_1") == "SAW_1"


def sv(old: str, new: str) -> str:
    return variant(old, new, SEQDET)


def test_enum_inputs_and_label_on_either_side():
    src = sv("din   boolean NOT NULL", "din boolean NOT NULL, mode state_t NOT NULL")
    src = variant("s.state = 'saw_10' AND i.din", "'saw_10' = s.state AND i.din AND i.mode <> 'idle'", src)
    d = parse_design(src)
    assert d.inputs[1].enum == "state_t" and d.inputs[1].width == 2
    det = d.next["detected"]
    assert det.b == Bin("!=", Ref("mode"), IDLE)
    assert det.a.a == Bin("==", SAW_10, Ref("state"))


def test_simple_case_on_bits_and_bool():
    src = with_dout("CASE i.din WHEN B'00000000'::bit(8) THEN n.din_plus1 WHEN B'11111111'::bit(8) THEN s.dout ELSE i.din END")
    src = variant("i.in_valid,\n", "CASE i.in_valid WHEN true THEN false ELSE s.out_valid END,\n", src)
    d = parse_design(src)
    sw = d.next["dout"]
    assert isinstance(sw, Switch) and [l for l, _ in sw.arms] == [Lit(0, 8, "b"), Lit(255, 8, "b")]
    assert d.next["out_valid"] == Switch(Ref("in_valid"), ((Lit(1, 1, "b"), Lit(0, 1, "b")),), Ref("out_valid"))


def test_err_enums():
    fails(sv("'saw_10' AND", "'saw_11' AND"), "unknown enum label 'saw_11' for state_t")
    fails(sv("SELECT 0, 'idle'::state_t, false", "SELECT 0, 'nope'::state_t, false"), "unknown enum label 'nope' for state_t")
    fails(sv("SELECT 0, 'idle'::state_t, false", "SELECT 0, 'idle', false"),
          "reset value of state must be true, false, B'...'::bit(n) or 'label'::enum")
    fails(sv("ENUM ('idle', 'saw_1', 'saw_10')", "ENUM ('idle', 'idle', 'saw_10')"), "duplicate name: idle")
    fails(sv("ENUM ('idle', 'saw_1', 'saw_10')", "ENUM ('Idle', 'saw_1', 'saw_10')"), "invalid name: Idle")
    fails(sv("CREATE TYPE state_t AS ENUM ('idle', 'saw_1', 'saw_10');", "CREATE TYPE state_t AS (a integer);"),
          "CREATE TYPE state_t must be AS ENUM ('label', ...)")
    fails(sv("s.state = 'saw_10' AND i.din", "s.state = 'saw_10' AND i.din AND s.state = B'10'::bit(2)"),
          "type mismatch: cannot compare state_t with bit(2)")
    fails(sv("s.state = 'saw_10' AND i.din", "s.state = 'saw_10' AND i.din AND s.state::state_t = s.state"),
          "only a label can be cast to state_t")
    fails(sv("WHEN 'idle'  THEN CASE WHEN i.din THEN 'saw_1'::state_t ELSE 'idle'   END",
             "WHEN 'idle'  THEN CASE WHEN i.din THEN 'saw_1' ELSE 'idle' END"),
          "CASE needs a typed branch (write one label as 'label'::enum)")
    fails(sv("WHEN 'saw_1' THEN", "WHEN 'idle' THEN"), "duplicate CASE label IDLE")
    fails(sv("WHEN 'saw_1' THEN", "WHEN s.state THEN"), "CASE label must be a state_t literal")
    fails(sv("WHEN 'saw_1' THEN", "WHEN 'later' THEN"), "unknown enum label 'later' for state_t")
    fails(sv("           ELSE              CASE WHEN i.din THEN 'saw_1'::state_t ELSE 'idle'   END\n", ""), "CASE needs ELSE")
    fails(with_dout("CASE WHEN i.in_valid THEN 'idle' ELSE s.dout END"),
          "string literal 'idle' has no enum context (write 'idle'::bit(8))")
    fails(with_dout("n.din_plus1", "'idle' AS din_plus1"),
          "string literal 'idle' has no enum context (write 'idle'::<enum type>)")
    fails(sv("CREATE TYPE state_t", "CREATE TYPE module"), "invalid name: module")
    fails(sv("din   boolean NOT NULL", "din other_t NOT NULL"), "unsupported type other_t for column din")
    # CREATE TYPE after CREATE TABLE is not a preamble
    fails(sv("CREATE TYPE state_t AS ENUM ('idle', 'saw_1', 'saw_10');\n\n", "") + "CREATE TYPE state_t AS ENUM ('idle');",
          "expected CREATE TABLE then CREATE VIEW")


# -- clock domains ----------------------------------------------------------------

PULSE_SYNC = (pytest.importorskip("pathlib").Path(__file__).resolve().parent.parent / "examples" / "pulse_sync" / "design.sql").read_text()


def ps(old: str, new: str) -> str:
    return variant(old, new, PULSE_SYNC)


def test_multi_clock_design():
    d = parse_design(PULSE_SYNC)
    assert d.clocks == ["clk_fast", "clk_slow"] and d.clock_names() == ["clk_fast", "clk_slow"]
    assert [s.name for s in d.inputs] == ["clk_fast", "clk_slow", "pulse_in"]
    assert [s.name for s in d.data_inputs] == ["pulse_in"]
    assert d.inputs[0].is_clock and d.inputs[0].is_bool and not d.inputs[2].is_clock
    assert d.domain == {"req": "clk_fast", "ack_f1": "clk_fast", "ack_f2": "clk_fast",
                        "req_s1": "clk_slow", "req_s2": "clk_slow", "req_s3": "clk_slow", "pulse_out": "clk_slow"}
    # the clock gate is stripped; what remains is the next-state logic
    assert d.next["req_s1"] == Ref("req")
    assert d.next["req"] == Bin("!=", Ref("req"), Bin("&&", Ref("pulse_in"), Un("!", Ref("busy"))))
    assert d.reg_pos["req"] == (17, 27)


def test_single_clock_designs_have_the_implicit_clk():
    d = parse_design(INC)
    assert d.clocks == [] and d.clock_names() == ["clk"]
    assert d.domain == {"out_valid": "clk", "dout": "clk"}


def test_err_clocks():
    fails(ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END", "s.req_s2 <> s.req_s3"),
          "register pulse_out needs a clock: CASE WHEN i.<clock> THEN <next> ELSE s.pulse_out END")
    fails(ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
             "CASE WHEN i.pulse_in THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END"),
          "register pulse_out needs a clock: CASE WHEN i.<clock> THEN <next> ELSE s.pulse_out END")
    fails(ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
             "CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE false END"),
          "register pulse_out must hold when clk_slow does not rise: CASE WHEN i.<clock> THEN <next> ELSE s.pulse_out END")
    fails(ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
             "CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 WHEN i.clk_fast THEN false ELSE s.pulse_out END"),
          "register pulse_out needs a clock: CASE WHEN i.<clock> THEN <next> ELSE s.pulse_out END")
    fails(ps("LATERAL (SELECT s.req <> s.ack_f2 AS busy) f", "LATERAL (SELECT s.req <> s.ack_f2 AND i.clk_slow AS busy) f"),
          "clock clk_slow may only gate a register (CASE WHEN i.clk_slow THEN ... ELSE s.<reg> END)")
    fails(ps("CREATE DOMAIN clock AS boolean;", "CREATE DOMAIN clock AS integer;"),
          "unsupported statement: CREATE DOMAIN clock AS integer")
    fails(ps("CREATE DOMAIN clock AS boolean;", "CREATE DOMAIN clk AS boolean;"), "invalid name: clk")
    fails(ps("clk_fast clock   NOT NULL", "clk_fast clock"), "column clk_fast must be NOT NULL")
    fails(ps("CREATE DOMAIN clock AS boolean;", ""), "unsupported type clock for column clk_fast")


# -- the CDC lint ----------------------------------------------------------------

def warnings_of(src: str) -> list[str]:
    from sqrtl.cdc import cdc_warnings
    return [w.message for w in cdc_warnings(parse_design(src))]


def test_cdc_lint_is_quiet_for_the_synchronizer_and_single_clock_designs():
    assert warnings_of(PULSE_SYNC) == []
    assert warnings_of(INC) == []
    assert warnings_of(SEQDET) == []


def test_cdc_lint_flags_unsynchronized_crossings():
    # slow-domain logic reading the fast-domain req directly
    src = ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
             "CASE WHEN i.clk_slow THEN s.req <> s.req_s3 ELSE s.pulse_out END")
    assert warnings_of(src) == ["pulse_out (clk_slow) reads req (clk_fast) without a synchronizer"]
    # ...also when the read goes through a wire
    src = ps("LATERAL (SELECT s.req <> s.ack_f2 AS busy) f", "LATERAL (SELECT s.req <> s.ack_f2 AS busy, s.req <> s.req_s3 AS req_edge) f")
    src = variant("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
                  "CASE WHEN i.clk_slow THEN f.req_edge ELSE s.pulse_out END", src)
    assert warnings_of(src) == ["pulse_out (clk_slow) reads req (clk_fast) without a synchronizer"]
    # a first stage read through logic
    src = ps("CASE WHEN i.clk_slow THEN s.req_s2 <> s.req_s3 ELSE s.pulse_out END",
             "CASE WHEN i.clk_slow THEN s.req_s1 <> s.req_s3 ELSE s.pulse_out END")
    assert warnings_of(src) == ["pulse_out reads synchronizer stage req_s1 through logic; read it via a second register"]
    # a first stage as output
    src = ps("SELECT cycle, req, ack_f2, pulse_out FROM sim", "SELECT cycle, req_s1, pulse_out FROM sim")
    assert warnings_of(src) == ["synchronizer stage req_s1 is a module output; expose the second stage instead"]


def test_cdc_lint_flags_multi_bit_copies():
    src = """
CREATE DOMAIN clock AS boolean;
CREATE TABLE bus_in (cycle integer PRIMARY KEY, clk_a clock NOT NULL, clk_b clock NOT NULL, d bit(8) NOT NULL);
CREATE VIEW bus_out AS
WITH RECURSIVE sim(cycle, da, db) AS (
    SELECT 0, B'00000000'::bit(8), B'00000000'::bit(8)
    UNION ALL
    SELECT s.cycle + 1,
           CASE WHEN i.clk_a THEN i.d ELSE s.da END,
           CASE WHEN i.clk_b THEN s.da ELSE s.db END
    FROM sim s
    JOIN bus_in i ON i.cycle = s.cycle
)
SELECT cycle, db FROM sim;
"""
    assert warnings_of(src) == ["db (clk_b) copies 8-bit da (clk_a) across clock domains; synchronize a 1-bit flag instead"]
