"""Design -> Verilog-2005 text. Pure: no I/O, no sqlglot, deterministic output."""

from __future__ import annotations

from .parse import Bin, Cond, Design, Expr, Lit, Ref, Signal, Un

__all__ = ["emit_verilog", "emit_expr", "vrange"]


def vrange(width: int) -> str:
    """The packed range for a signal: '' for 1 bit, '[n-1:0]' otherwise."""
    return "" if width <= 1 else f"[{width - 1}:0]"


def emit_expr(e: Expr) -> str:
    if isinstance(e, Ref):
        return e.name
    if isinstance(e, Lit):
        if e.base == "b":
            return f"{e.width}'b{e.value:0{e.width}b}"
        return f"{e.width}'d{e.value}"
    if isinstance(e, Un):
        return f"({e.op}{emit_expr(e.a)})"
    if isinstance(e, Bin):
        return f"({emit_expr(e.a)} {e.op} {emit_expr(e.b)})"
    if isinstance(e, Cond):
        return f"({emit_expr(e.c)} ? {emit_expr(e.a)} : {emit_expr(e.b)})"
    raise TypeError(f"not an Expr: {e!r}")


def _decl(kind: str, sig: Signal) -> str:
    rng = vrange(sig.width)
    return f"{kind} {rng} {sig.name}" if rng else f"{kind} {sig.name}"


def emit_verilog(d: Design) -> str:
    regs = {r.name: r for r in d.regs}
    ports: list[tuple[str, str, str, str]] = [
        ("input", "wire", "", "clk"),
        ("input", "wire", "", "rst"),
    ]
    ports += [("input", "wire", vrange(s.width), s.name) for s in d.inputs]
    ports += [("output", "reg", vrange(regs[o].width), o) for o in d.outputs]
    rng_w = max(len(p[2]) for p in ports)

    lines = [f"module {d.name} ("]
    for k, (direction, kind, rng, name) in enumerate(ports):
        comma = "," if k < len(ports) - 1 else ""
        if rng_w:
            lines.append(f"    {direction:<6} {kind:<4} {rng:<{rng_w}} {name}{comma}")
        else:
            lines.append(f"    {direction:<6} {kind:<4} {name}{comma}")
    lines.append(");")

    for r in d.regs:
        if r.name not in d.outputs:
            lines.append(f"    {_decl('reg', r)};")
    for name, e in d.wires:
        lines.append(f"    {_decl('wire', d.wire_signals[name])} = {emit_expr(e)};")

    lines.append("")
    lines.append("    always @(posedge clk) begin")
    lines.append("        if (rst) begin")
    for r in d.regs:
        lines.append(f"            {r.name} <= {emit_expr(d.reset[r.name])};")
    lines.append("        end else begin")
    for r in d.regs:
        lines.append(f"            {r.name} <= {emit_expr(d.next[r.name])};")
    lines.append("        end")
    lines.append("    end")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"
