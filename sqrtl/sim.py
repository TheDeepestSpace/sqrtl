"""Run a design plus its stimulus in PostgreSQL and return CSV traces.

Everything here owns database access; parse.py and verilog.py never do.
"""

from __future__ import annotations

import os
import uuid

import psycopg

from .parse import Design, Signal, SqrtlError

__all__ = ["default_dsn", "run_sim", "normalize_csv"]

DEFAULT_DSN = "postgresql://localhost/sqrtl"
_TWO64 = 1 << 64


def default_dsn() -> str:
    return os.environ.get("SQRTL_DSN") or DEFAULT_DSN


def normalize_csv(text: str) -> str:
    """Normalize line endings and guarantee a single trailing newline."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip("\n") + "\n"


def _render(sig: Signal) -> str:
    """SQL rendering a signal as the trace's unsigned decimal / 0-1 text."""
    if sig.is_bool:
        return f"{sig.name}::int AS {sig.name}"
    if sig.width < 64:
        return f"{sig.name}::bigint AS {sig.name}"
    # bit(64)::bigint is two's complement; fold back to unsigned.
    return f"mod({sig.name}::bigint::numeric + {_TWO64}, {_TWO64}) AS {sig.name}"


def _copy_csv(cur: psycopg.Cursor, select: str) -> str:
    with cur.copy(f"COPY ({select}) TO STDOUT (FORMAT csv, HEADER true)") as copy:
        data = b"".join(bytes(chunk) for chunk in copy)
    return normalize_csv(data.decode("utf-8"))


def run_sim(design: Design, design_sql: str, stim_sql: str, dsn: str | None = None) -> tuple[str, str]:
    """Return ``(trace_csv, stim_csv)`` for the design.

    The design and stimulus are loaded into a fresh schema that is dropped
    afterwards. Raises SqrtlError for stimulus gaps, NULLs in the trace, or
    any PostgreSQL error.
    """
    dsn = dsn or default_dsn()
    schema = f"sqrtl_{uuid.uuid4().hex[:12]}"
    name = design.name
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as e:
        raise SqrtlError(f"cannot connect to {dsn}: {e}".strip()) from None
    with conn:
        cur = conn.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        try:
            cur.execute(f'SET search_path TO "{schema}"')
            try:
                cur.execute(design_sql)
                cur.execute(stim_sql)
            except psycopg.Error as e:
                raise SqrtlError(f"postgres: {str(e).strip()}") from None

            cycles = [row[0] for row in cur.execute(f"SELECT cycle FROM {name}_in ORDER BY cycle")]
            if not cycles:
                raise SqrtlError("stimulus is empty")
            for k, c in enumerate(cycles):
                if c != k:
                    raise SqrtlError(f"stimulus has gap at cycle {k}")

            stim_cols = ", ".join(["cycle"] + [_render(s) for s in design.inputs])
            stim_csv = _copy_csv(cur, f"SELECT {stim_cols} FROM {name}_in ORDER BY cycle")

            regs = {r.name: r for r in design.regs}
            out_cols = ", ".join(["cycle"] + [_render(regs[o]) for o in design.outputs])
            try:
                trace_csv = _copy_csv(cur, f"SELECT {out_cols} FROM {name}_out ORDER BY cycle")
            except psycopg.Error as e:
                raise SqrtlError(f"postgres: {str(e).strip()}") from None
        finally:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')

    rows = trace_csv.split("\n")[1:-1]
    if len(rows) != len(cycles) + 1:
        raise SqrtlError(f"trace has {len(rows)} rows, expected {len(cycles) + 1}")
    for row in rows:
        fields = row.split(",")
        if any(f == "" for f in fields):
            raise SqrtlError(f"NULL reached the trace at cycle {fields[0]}")
    return trace_csv, stim_csv
