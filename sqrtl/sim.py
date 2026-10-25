"""Run a design plus its stimulus in PostgreSQL and return CSV traces.

Everything here owns database access; parse.py and verilog.py never do.
"""

from __future__ import annotations

import os
import uuid

import psycopg

from .parse import Design, SqrtlError
from .trace import check_contiguous, normalize_csv, stim_select, trace_select

__all__ = ["default_dsn", "run_sim", "normalize_csv"]

DEFAULT_DSN = "postgresql://localhost/sqrtl"


def default_dsn() -> str:
    return os.environ.get("SQRTL_DSN") or DEFAULT_DSN


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
            check_contiguous(cycles)

            stim_csv = _copy_csv(cur, stim_select(design))
            try:
                trace_csv = _copy_csv(cur, trace_select(design))
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
