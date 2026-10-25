"""verilog.py: byte-exact reference output, determinism, iverilog -Wall silence."""

import shutil
import subprocess

import pytest

from sqrtl.parse import Bin, Cond, Lit, Ref, Switch, Un, parse_design
from sqrtl.verilog import emit_assign, emit_expr, emit_verilog

from conftest import EXAMPLE_DIRS, EXAMPLE_IDS

EXPECTED_INCREMENTER = """\
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
"""

EXPECTED_SEQDET = """\
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
"""

EXPECTED_PULSE_SYNC = """\
module pulse_sync (
    input  logic clk_fast,
    input  logic clk_slow,
    input  logic rst,
    input  logic pulse_in,
    output logic req,
    output logic ack_f2,
    output logic pulse_out
);
    logic ack_f1;
    logic req_s1;
    logic req_s2;
    logic req_s3;
    logic busy;
    always_comb busy = (req != ack_f2);

    always_ff @(posedge clk_fast) begin
        if (rst) begin
            req <= 1'b0;
            ack_f1 <= 1'b0;
            ack_f2 <= 1'b0;
        end else begin
            req <= (req != (pulse_in && (!busy)));
            ack_f1 <= req_s2;
            ack_f2 <= ack_f1;
        end
    end

    always_ff @(posedge clk_slow) begin
        if (rst) begin
            req_s1 <= 1'b0;
            req_s2 <= 1'b0;
            req_s3 <= 1'b0;
            pulse_out <= 1'b0;
        end else begin
            req_s1 <= req;
            req_s2 <= req_s1;
            req_s3 <= req_s2;
            pulse_out <= (req_s2 != req_s3);
        end
    end
endmodule
"""

needs_iverilog = pytest.mark.skipif(shutil.which("iverilog") is None, reason="iverilog not installed")
needs_sv2v = pytest.mark.skipif(shutil.which("sv2v") is None, reason="sv2v not installed")


def test_incrementer_byte_exact(incrementer_sql):
    assert emit_verilog(parse_design(incrementer_sql)) == EXPECTED_INCREMENTER


def test_seqdet_byte_exact():
    sql = (EXAMPLE_DIRS[EXAMPLE_IDS.index("seqdet")] / "design.sql").read_text()
    assert emit_verilog(parse_design(sql)) == EXPECTED_SEQDET


def test_pulse_sync_byte_exact():
    sql = (EXAMPLE_DIRS[EXAMPLE_IDS.index("pulse_sync")] / "design.sql").read_text()
    assert emit_verilog(parse_design(sql)) == EXPECTED_PULSE_SYNC


def test_multi_clock_testbench_drives_each_clock_from_its_flag():
    from sqrtl.testbench import render_testbench
    sql = (EXAMPLE_DIRS[EXAMPLE_IDS.index("pulse_sync")] / "design.sql").read_text()
    tb = render_testbench(parse_design(sql))
    assert "    reg tb_e_clk_fast = 1'b0;\n    reg tb_e_clk_slow = 1'b0;\n" in tb
    assert 'while ($fscanf(tb_stim, "%d,%d,%d,%d\\n", tb_cycle, tb_e_clk_fast, tb_e_clk_slow, pulse_in) == 4)' in tb
    assert "            #5 clk_fast = tb_e_clk_fast; clk_slow = tb_e_clk_slow;\n" in tb
    assert "        #5 clk_fast = 1'b1;\n        #5 clk_fast = 1'b0;\n        #5 clk_slow = 1'b1;\n        #5 clk_slow = 1'b0;\n" in tb
    assert tb.count("\n") < 60
    single = render_testbench(parse_design((EXAMPLE_DIRS[EXAMPLE_IDS.index("incrementer")] / "design.sql").read_text()))
    assert "            #5 clk = 1'b1;\n" in single and "tb_e_" not in single


def test_switch_emission():
    sw = Switch(Ref("s"), ((Lit(0, 2, "d", "A"), Ref("x")), (Lit(1, 2, "d", "B"), Switch(Ref("t"), ((Lit(1, 1, "b"), Ref("y")),), Ref("z")))), Lit(3, 2, "d"))
    assert emit_expr(sw) == "((s == A) ? x : ((s == B) ? ((t == 1'b1) ? y : z) : 2'd3))"
    assert emit_assign("q", sw, "<=", "  ") == [
        "  case (s)",
        "      A: q <= x;",
        "      B: begin",
        "          case (t)",
        "              1'b1: q <= y;",
        "              default: q <= z;",
        "          endcase",
        "      end",
        "      default: q <= 2'd3;",
        "  endcase",
    ]
    assert emit_assign("q", Ref("x"), "=", "") == ["q = x;"]


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
    assert "    output logic       out_valid\n);" in v
    assert "    logic [7:0] dout;\n" in v
    assert "dout <= 8'd0;" in v


def _iverilog(tmp_path, verilog: str) -> str:
    (tmp_path / "dut.v").write_text(verilog)
    r = subprocess.run(["iverilog", "-Wall", "-g2005", "-o", "dut.vvp", "dut.v"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (r.stdout + r.stderr).strip()


def _sv2v(tmp_path, systemverilog: str) -> str:
    (tmp_path / "dut.sv").write_text(systemverilog)
    r = subprocess.run(["sv2v", "dut.sv"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    return r.stdout


@needs_iverilog
@needs_sv2v
@pytest.mark.parametrize("example", EXAMPLE_DIRS, ids=EXAMPLE_IDS)
def test_examples_convert_and_compile_without_warnings(tmp_path, example):
    verilog = _sv2v(tmp_path, emit_verilog(parse_design((example / "design.sql").read_text())))
    assert _iverilog(tmp_path, verilog) == ""


@needs_iverilog
@needs_sv2v
def test_operator_subset_converts_and_compiles_without_warnings(tmp_path):
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
    assert _iverilog(tmp_path, _sv2v(tmp_path, emit_verilog(parse_design(sql)))) == ""


def test_wire_with_case_uses_always_comb_block():
    sql = """
CREATE TYPE op_t AS ENUM ('add', 'sub', 'hold');
CREATE TABLE alu_in (cycle integer PRIMARY KEY, op op_t NOT NULL, x bit(4) NOT NULL);
CREATE VIEW alu_out AS
WITH RECURSIVE sim(cycle, acc, last) AS (
    SELECT 0, B'0000'::bit(4), 'hold'::op_t
    UNION ALL
    SELECT s.cycle + 1, n.nxt, i.op
    FROM sim s
    JOIN alu_in i ON i.cycle = s.cycle,
    LATERAL (SELECT CASE i.op WHEN 'add' THEN (s.acc::int + i.x::int)::bit(4)
                              WHEN 'sub' THEN (s.acc::int - i.x::int)::bit(4)
                              ELSE s.acc END AS nxt) n
)
SELECT cycle, acc, last FROM sim;
"""
    v = emit_verilog(parse_design(sql))
    assert "typedef enum logic [1:0] {ADD = 2'd0, SUB = 2'd1, HOLD = 2'd2} op_t;\n" in v
    assert "    input  op_t        op,\n" in v
    assert "    logic [3:0] nxt;\n    always_comb begin\n        case (op)\n            ADD: nxt = (acc + x);\n" in v
    assert "            default: nxt = acc;\n        endcase\n    end\n" in v
    assert "    output op_t        last\n" in v
    assert "localparam" not in v
    # the same Switch in expression position is a chain of compares
    nested = sql.replace("SELECT s.cycle + 1, n.nxt, i.op", "SELECT s.cycle + 1, CASE WHEN i.op = 'hold' THEN s.acc ELSE n.nxt END, i.op")
    v2 = emit_verilog(parse_design(nested.replace("n.nxt, i.op\n", "n.nxt, i.op\n")))
    assert "acc <= (((op == HOLD)) ? acc : nxt);" in v2 or "acc <= ((op == HOLD) ? acc : nxt);" in v2
