"""verilog.py: byte-exact reference output, determinism, iverilog -Wall silence."""

import shutil
import subprocess

import pytest

from sqrtl.parse import Bin, Cond, Lit, Ref, Un, parse_design
from sqrtl.verilog import emit_expr, emit_verilog

from conftest import EXAMPLE_DIRS, EXAMPLE_IDS

EXPECTED_INCREMENTER = """\
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
"""

needs_iverilog = pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")


def test_incrementer_byte_exact(incrementer_sql):
    assert emit_verilog(parse_design(incrementer_sql)) == EXPECTED_INCREMENTER


@pytest.mark.parametrize("example", EXAMPLE_DIRS, ids=EXAMPLE_IDS)
def test_deterministic(example):
    sql = (example / "design.sql").read_text()
    assert emit_verilog(parse_design(sql)) == emit_verilog(parse_design(sql))


def test_expr_emission():
    assert emit_expr(Lit(5, 4, "b")) == "4'b0101"
    assert emit_expr(Lit(0, 8, "d")) == "8'd0"
    assert emit_expr(Lit(1, 1, "b")) == "1'b1"
    assert emit_expr(Un("!", Ref("a"))) == "(!a)"
    assert emit_expr(Bin("&&", Ref("a"), Un("~", Ref("b")))) == "(a && (~b))"
    assert emit_expr(Cond(Ref("c"), Bin("^", Ref("a"), Ref("b")), Lit(3, 2, "d"))) == "(c ? (a ^ b) : 2'd3)"


def test_internal_registers_are_declared_not_ported():
    sql = (EXAMPLE_DIRS[EXAMPLE_IDS.index("incrementer")] / "design.sql").read_text()
    sql = sql.replace("SELECT cycle, out_valid, dout FROM sim", "SELECT cycle, out_valid FROM sim")
    v = emit_verilog(parse_design(sql))
    assert "    output reg  out_valid\n);" in v or "    output reg        out_valid\n);" in v
    assert "    reg [7:0] dout;\n" in v
    assert "dout <= 8'd0;" in v


def _iverilog(tmp_path, verilog: str) -> str:
    (tmp_path / "dut.v").write_text(verilog)
    r = subprocess.run(["iverilog", "-Wall", "-g2005", "-o", "dut.vvp", "dut.v"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (r.stdout + r.stderr).strip()


@needs_iverilog
@pytest.mark.parametrize("example", EXAMPLE_DIRS, ids=EXAMPLE_IDS)
def test_examples_compile_without_warnings(tmp_path, example):
    assert _iverilog(tmp_path, emit_verilog(parse_design((example / "design.sql").read_text()))) == ""


@needs_iverilog
def test_operator_subset_compiles_without_warnings(tmp_path):
    sql = """
CREATE TABLE ops_in (cycle integer PRIMARY KEY, a boolean NOT NULL, x bit(8) NOT NULL, y bit(8) NOT NULL);
CREATE VIEW ops_out AS
WITH RECURSIVE sim(cycle, q, r, w) AS (
    SELECT 0, false, B'00000000'::bit(8), B'1111111111111111111111111111111111111111111111111111111111111111'::bit(64)
    UNION ALL
    SELECT s.cycle + 1,
           (i.a AND NOT s.q) OR i.x = i.y OR i.x <> s.r,
           CASE WHEN i.a THEN n.sum WHEN s.q THEN n.diff ELSE (s.r & i.x) | (~i.y # B'10101010'::bit(8)) END,
           (s.w::int - 1)::bit(64)
    FROM sim s
    JOIN ops_in i ON i.cycle = s.cycle,
    LATERAL (SELECT (i.x::int + i.y::int)::bit(8) AS sum, (i.x::int - 1)::bit(8) AS diff) n
)
SELECT cycle, r, q, w FROM sim;
"""
    assert _iverilog(tmp_path, emit_verilog(parse_design(sql))) == ""
