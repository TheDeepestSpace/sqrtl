"""`sqrtl check`: Postgres trace vs. Icarus trace of the generated SystemVerilog (via sv2v)."""

from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from .parse import Design, SqrtlError
from .sim import normalize_csv, run_sim
from .testbench import STIM_FILE, TRACE_FILE, render_testbench
from .verilog import emit_verilog

__all__ = ["check", "run_icarus", "udiff"]


def udiff(a: str, b: str, a_name: str, b_name: str) -> str:
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True), a_name, b_name))


def run_icarus(design: Design, systemverilog: str, stim_csv: str, workdir: Path) -> str:
    """Compile and run the design under Icarus; return the trace CSV.

    The SystemVerilog is first converted to Verilog with sv2v (a tool, not a
    package dependency) and the converted file is what Icarus compiles. Any
    sv2v or iverilog diagnostic (warnings included) is a failure.
    """
    for tool in ("sv2v", "iverilog", "vvp"):
        if shutil.which(tool) is None:
            hint = "see scripts/install-sv2v.sh or `make tools`" if tool == "sv2v" else "install Icarus Verilog"
            raise SqrtlError(f"{tool} not found on PATH ({hint})")
    src = workdir / f"{design.name}.sv"
    dut = workdir / f"{design.name}.v"
    tb = workdir / "tb.v"
    src.write_text(systemverilog)
    r = subprocess.run(["sv2v", src.name], cwd=workdir, capture_output=True, text=True)
    if r.returncode != 0 or r.stderr.strip():
        raise SqrtlError(f"sv2v {'failed' if r.returncode else 'warned'}:\n{r.stderr.strip()}")
    dut.write_text(r.stdout)
    tb.write_text(render_testbench(design))
    (workdir / STIM_FILE).write_text(stim_csv)

    cmd = ["iverilog", "-Wall", "-g2005", "-o", "sim.vvp", dut.name, tb.name]
    r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or out:
        raise SqrtlError(f"iverilog {'failed' if r.returncode else 'warned'}:\n{out}")

    r = subprocess.run(["vvp", "-n", "sim.vvp"], cwd=workdir, capture_output=True, text=True)
    if r.returncode != 0:
        raise SqrtlError(f"vvp failed:\n{(r.stdout + r.stderr).strip()}")
    trace = workdir / TRACE_FILE
    if not trace.exists():
        raise SqrtlError("vvp produced no trace")
    return normalize_csv(trace.read_text())


def check(design: Design, design_sql: str, stim_sql: str, expected_csv: str | None,
          dsn: str | None = None, out: TextIO | None = None, keep: Path | None = None) -> int:
    """Run sim + transpile + sv2v + iverilog and diff the traces. Returns 0 or 1."""
    out = out if out is not None else sys.stdout
    trace_pg, stim_csv = run_sim(design, design_sql, stim_sql, dsn)
    verilog = emit_verilog(design)

    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        trace_iv = run_icarus(design, verilog, stim_csv, keep)
        (keep / "trace_pg.csv").write_text(trace_pg)
    else:
        with tempfile.TemporaryDirectory(prefix="sqrtl_") as tmp:
            trace_iv = run_icarus(design, verilog, stim_csv, Path(tmp))

    ok = True
    diff = udiff(trace_pg, trace_iv, "trace_pg.csv", "trace_iv.csv")
    if diff:
        ok = False
        out.write(diff)
    if expected_csv is not None:
        diff = udiff(normalize_csv(expected_csv), trace_pg, "expected.csv", "trace_pg.csv")
        if diff:
            ok = False
            out.write(diff)
    cycles = trace_pg.count("\n") - 1
    if ok:
        out.write(f"ok: {design.name}: postgres and icarus traces match over {cycles} cycles"
                  f"{' and equal expected.csv' if expected_csv is not None else ''}\n")
    return 0 if ok else 1
