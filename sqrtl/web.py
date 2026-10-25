"""The browser-facing entry point, run under Pyodide by the web UI.

It depends only on parse.py, verilog.py and trace.py (no psycopg, no I/O):
the page runs the SQL itself in PGlite and only needs the Design metadata,
the generated SystemVerilog and the trace queries.
"""

from __future__ import annotations

import json

from .parse import Signal, SqrtlError, parse_design
from .trace import stim_select, trace_select
from .verilog import emit_verilog

__all__ = ["analyze", "analyze_json"]


def _sig(s: Signal) -> dict:
    return {"name": s.name, "width": s.width, "is_bool": s.is_bool, "enum": s.enum}


def analyze(design_sql: str) -> dict:
    """Parse + transpile. Returns a JSON-able dict; never raises for SqrtlError."""
    try:
        d = parse_design(design_sql)
    except SqrtlError as e:
        return {"ok": False, "error": {"message": e.message, "line": e.line, "col": e.col}}
    return {
        "ok": True,
        "name": d.name,
        "inputs": [_sig(s) for s in d.inputs],
        "regs": [_sig(s) for s in d.regs],
        "wires": [_sig(d.wire_signals[w]) for w, _ in d.wires],
        "outputs": d.outputs,
        "enums": d.enums,
        "systemverilog": emit_verilog(d),
        "stim_select": stim_select(d),
        "trace_select": trace_select(d),
    }


def analyze_json(design_sql: str) -> str:
    return json.dumps(analyze(design_sql))
