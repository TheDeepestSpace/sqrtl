"""parse.py: the incrementer lowers to the expected Design, and every row of
the spec's error table has a failing input."""

import re

import pytest

from sqrtl.parse import Bin, Cond, Lit, Ref, SqrtlError, parse_design

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
          "type mismatch: CASE branches differ: boolean vs bit(8)")
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
    fails(variant("SELECT 0, false, B'00000000'::bit(8)", "SELECT 0, false, s.dout"), "reset value of dout must be true, false or B'...'::bit(n)")
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
