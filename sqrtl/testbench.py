"""Render the Icarus testbench for a Design.

The testbench reads stim.csv (header + one row per stimulus cycle), holds rst
high for one clock, then for every stimulus row drives the inputs on the
falling edge, raises clk, and 1 time unit after the rising edge appends
``cycle,<outputs>`` to trace_iv.csv. Period is 10 time units. No `timescale
is used so that iverilog -Wall stays silent alongside the generated module.
"""

from __future__ import annotations

from .parse import Design
from .verilog import vrange

__all__ = ["render_testbench", "STIM_FILE", "TRACE_FILE"]

STIM_FILE = "stim.csv"
TRACE_FILE = "trace_iv.csv"


def render_testbench(d: Design) -> str:
    regs = {r.name: r for r in d.regs}
    in_names = [s.name for s in d.inputs]
    out_names = list(d.outputs)
    in_fmt = ",".join(["%d"] * (1 + len(in_names))) + "\\n"
    out_fmt = ",".join(["%0d"] * (1 + len(out_names))) + "\\n"
    header = ",".join(["cycle"] + out_names)
    scan_args = ", ".join(["tb_cycle"] + in_names)
    write_args = ", ".join(["tb_n"] + out_names)
    conns = ", ".join([".clk(clk)", ".rst(rst)"] + [f".{n}({n})" for n in in_names + out_names])

    lines = [
        "module tb;",
        "    reg clk = 1'b0;",
        "    reg rst = 1'b1;",
    ]
    for s in d.inputs:
        rng = vrange(s.width)
        lines.append(f"    reg {rng + ' ' if rng else ''}{s.name} = {s.width}'d0;")
    for o in out_names:
        rng = vrange(regs[o].width)
        lines.append(f"    wire {rng + ' ' if rng else ''}{o};")
    lines += [
        "    integer tb_stim, tb_trace, tb_cycle, tb_n, tb_r;",
        "    reg [8*256-1:0] tb_line;",
        f"    {d.name} dut ({conns});",
        "    initial begin",
        f'        tb_stim = $fopen("{STIM_FILE}", "r");',
        f'        tb_trace = $fopen("{TRACE_FILE}", "w");',
        "        tb_r = $fgets(tb_line, tb_stim);",
        f'        $fwrite(tb_trace, "{header}\\n");',
        "        tb_n = 0;",
        "        #5 clk = 1'b1;",
        f'        #1 $fwrite(tb_trace, "{out_fmt}", {write_args});',
        f'        while ($fscanf(tb_stim, "{in_fmt}", {scan_args}) == {1 + len(in_names)}) begin',
        "            #4 clk = 1'b0;",
        "            rst = 1'b0;",
        "            tb_n = tb_n + 1;",
        "            #5 clk = 1'b1;",
        f'            #1 $fwrite(tb_trace, "{out_fmt}", {write_args});',
        "        end",
        "        $fclose(tb_stim);",
        "        $fclose(tb_trace);",
        "        $finish;",
        "    end",
        "endmodule",
    ]
    return "\n".join(lines) + "\n"
