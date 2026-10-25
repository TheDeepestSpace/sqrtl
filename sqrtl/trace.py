"""Pure helpers for rendering traces: the SQL that turns a design's view and
port table into the CSV trace format, shared by sim.py (psycopg) and web.py
(PGlite in the browser). No I/O here."""

from __future__ import annotations

from .parse import Design, Signal, SqrtlError

__all__ = ["normalize_csv", "render_column", "stim_select", "trace_select", "check_contiguous"]

_TWO64 = 1 << 64


def normalize_csv(text: str) -> str:
    """Normalize line endings and guarantee a single trailing newline."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n"


def render_column(sig: Signal) -> str:
    """SQL rendering a signal as the trace's 0/1 (boolean) or unsigned decimal (bit(n))."""
    if sig.is_bool:
        return f"{sig.name}::int AS {sig.name}"
    if sig.width < 64:
        return f"{sig.name}::bigint AS {sig.name}"
    # bit(64)::bigint is two's complement; fold back to unsigned.
    return f"mod({sig.name}::bigint::numeric + {_TWO64}, {_TWO64}) AS {sig.name}"


def stim_select(design: Design) -> str:
    """SELECT producing `cycle,<inputs in table order>` from the port table."""
    cols = ", ".join(["cycle"] + [render_column(s) for s in design.inputs])
    return f"SELECT {cols} FROM {design.name}_in ORDER BY cycle"


def trace_select(design: Design) -> str:
    """SELECT producing `cycle,<outputs in outer-SELECT order>` from the design view."""
    regs = {r.name: r for r in design.regs}
    cols = ", ".join(["cycle"] + [render_column(regs[o]) for o in design.outputs])
    return f"SELECT {cols} FROM {design.name}_out ORDER BY cycle"


def check_contiguous(cycles: list[int]) -> None:
    """Stimulus rows must be 0, 1, 2, ... with no gaps."""
    if not cycles:
        raise SqrtlError("stimulus is empty")
    for k, c in enumerate(cycles):
        if c != k:
            raise SqrtlError(f"stimulus has gap at cycle {k}")
