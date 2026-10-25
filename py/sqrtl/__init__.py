"""sqrtl: a PostgreSQL query as a hardware description language."""

__version__ = "0.1.0"

from .parse import Bin, Cond, Design, Expr, Lit, Ref, Signal, SqrtlError, Switch, Un, parse_design  # noqa: E402
from .verilog import emit_verilog  # noqa: E402

__all__ = [
    "__version__",
    "SqrtlError", "Design", "Signal", "Expr", "Ref", "Lit", "Un", "Bin", "Cond", "Switch",
    "parse_design", "emit_verilog",
]
