"""Command line: sqrtl transpile | sim | check <example_dir>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .check import check
from .parse import Design, SqrtlError, parse_design
from .sim import default_dsn, run_sim
from .verilog import emit_verilog


def _load(example_dir: str) -> tuple[Path, str, Design]:
    d = Path(example_dir)
    design_path = d / "design.sql"
    if not design_path.is_file():
        raise SqrtlError(f"{design_path} not found")
    sql = design_path.read_text()
    return d, sql, parse_design(sql)


def _read_stim(d: Path) -> str:
    stim_path = d / "stim.sql"
    if not stim_path.is_file():
        raise SqrtlError(f"{stim_path} not found")
    return stim_path.read_text()


def _write(text: str, out: str | None) -> None:
    if out:
        Path(out).write_text(text)
    else:
        sys.stdout.write(text)


def cmd_transpile(args: argparse.Namespace) -> int:
    _, _, design = _load(args.example_dir)
    _write(emit_verilog(design, sv=args.sv), args.output)
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    d, sql, design = _load(args.example_dir)
    trace, _ = run_sim(design, sql, _read_stim(d), args.dsn)
    _write(trace, args.output)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    d, sql, design = _load(args.example_dir)
    expected_path = d / "expected.csv"
    expected = expected_path.read_text() if expected_path.is_file() else None
    keep = Path(args.keep) if args.keep else None
    return check(design, sql, _read_stim(d), expected, args.dsn, keep=keep, sv=args.sv)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sqrtl", description="SQL as a hardware description language.")
    p.add_argument("--version", action="version", version=f"sqrtl {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transpile", help="design.sql -> Verilog (stdout by default)")
    t.add_argument("example_dir")
    t.add_argument("-o", "--output", metavar="out.v")
    t.add_argument("--sv", action="store_true", help="emit SystemVerilog (logic, typedef enum, always_comb, always_ff)")
    t.set_defaults(func=cmd_transpile)

    s = sub.add_parser("sim", help="run the design in PostgreSQL and print the trace CSV")
    s.add_argument("example_dir")
    s.add_argument("-o", "--output", metavar="trace.csv")
    s.add_argument("--dsn", default=None, help="PostgreSQL DSN (default: $SQRTL_DSN, then postgresql://localhost/sqrtl)")
    s.set_defaults(func=cmd_sim)

    c = sub.add_parser("check", help="sim + transpile + iverilog + diff; exit 0 on match, 1 on mismatch")
    c.add_argument("example_dir")
    c.add_argument("--dsn", default=None, help="PostgreSQL DSN (default: $SQRTL_DSN, then postgresql://localhost/sqrtl)")
    c.add_argument("--keep", metavar="DIR", help="keep the Verilog, testbench and traces in DIR")
    c.add_argument("--sv", action="store_true", help="transpile to SystemVerilog and run Icarus on its sv2v conversion")
    c.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "dsn", None) is None and hasattr(args, "dsn"):
        args.dsn = default_dsn()
    try:
        return args.func(args)
    except SqrtlError as e:
        if e.line is not None:
            design_path = Path(args.example_dir) / "design.sql"
            print(f"{design_path}:{e.line}:{e.col}: {e.message}", file=sys.stderr)
        else:
            print(f"sqrtl: {e.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
