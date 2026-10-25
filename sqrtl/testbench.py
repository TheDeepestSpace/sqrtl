"""Render the Icarus testbench for a Design.

The testbench reads stim.csv (header + one row per tick), gives every clock
one rising edge while rst is high, then for every stimulus row drives the
inputs, raises the clocks that row flags (all of them for the implicit single
clock), and 1 time unit after the edge appends ``cycle,<outputs>`` to
trace_iv.csv. Period is 10 time units. No `timescale is used so that
iverilog -Wall stays silent alongside the generated module.
"""

from __future__ import annotations

from .parse import Design
from .verilog import vrange

__all__ = ["render_testbench", "STIM_FILE", "TRACE_FILE"]

STIM_FILE = "stim.csv"
TRACE_FILE = "trace_iv.csv"


def render_testbench(d: Design) -> str:
    regs = {r.name: r for r in d.regs}
    clocks = d.clock_names()
    declared = bool(d.clocks)  # clock ports come from the stimulus; the implicit clk rises every tick
    out_names = list(d.outputs)
    # stim.csv columns are the port table columns in table order; clocks scan into edge flags
    scan_targets = ["tb_cycle"] + [f"tb_e_{s.name}" if s.is_clock else s.name for s in d.inputs]
    in_fmt = ",".join(["%d"] * len(scan_targets)) + "\\n"
    out_fmt = ",".join(["%0d"] * (1 + len(out_names))) + "\\n"
    header = ",".join(["cycle"] + out_names)
    write_args = ", ".join(["tb_n"] + out_names)
    conns = ", ".join([f".{c}({c})" for c in clocks] + [".rst(rst)"]
                      + [f".{n}({n})" for n in [s.name for s in d.data_inputs] + out_names])

    lines = ["module tb;"]
    for c in clocks:
        lines.append(f"    reg {c} = 1'b0;")
    lines.append("    reg rst = 1'b1;")
    for s in d.data_inputs:
        rng = vrange(s.width)
        lines.append(f"    reg {rng + ' ' if rng else ''}{s.name} = {s.width}'d0;")
    for c in (clocks if declared else []):
        lines.append(f"    reg tb_e_{c} = 1'b0;")
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
    ]
    for c in clocks:  # reset: one rising edge of every clock while rst is high
        lines.append(f"        #5 {c} = 1'b1;")
        lines.append(f"        #5 {c} = 1'b0;")
    rise = " ".join(f"{c} = {('tb_e_' + c) if declared else chr(39).join(['1', 'b1'])};" for c in clocks)
    fall = " ".join(f"{c} = 1'b0;" for c in clocks)
    lines += [
        f'        #1 $fwrite(tb_trace, "{out_fmt}", {write_args});',
        f'        while ($fscanf(tb_stim, "{in_fmt}", {", ".join(scan_targets)}) == {len(scan_targets)}) begin',
        "            rst = 1'b0;",
        f"            #5 {rise}",
        "            tb_n = tb_n + 1;",
        f'            #1 $fwrite(tb_trace, "{out_fmt}", {write_args});',
        f"            #4 {fall}",
        "        end",
        "        $fclose(tb_stim);",
        "        $fclose(tb_trace);",
        "        $finish;",
        "    end",
        "endmodule",
    ]
    return "\n".join(lines) + "\n"
