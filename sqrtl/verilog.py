"""Design -> SystemVerilog text. Pure: no I/O, no sqlglot, deterministic output.

The transpiler emits SystemVerilog only: `logic` ports and registers, a
`typedef enum` per SQL enum, `always_comb` for LATERAL columns, `always_ff`
for the register block, and `case` statements for simple CASE. Plain Verilog
is one `sv2v` away, which is how `sqrtl check` feeds Icarus.
"""

from __future__ import annotations

from .parse import Bin, Cond, Design, Expr, Lit, Ref, Signal, Switch, Un

__all__ = ["emit_verilog", "emit_expr", "emit_assign", "vrange"]


def vrange(width: int) -> str:
    """The packed range for a signal: '' for 1 bit, '[n-1:0]' otherwise."""
    return "" if width <= 1 else f"[{width - 1}:0]"


def emit_expr(e: Expr) -> str:
    if isinstance(e, Ref):
        return e.name
    if isinstance(e, Lit):
        if e.label:
            return e.label
        if e.base == "b":
            return f"{e.width}'b{e.value:0{e.width}b}"
        return f"{e.width}'d{e.value}"
    if isinstance(e, Un):
        return f"({e.op}{emit_expr(e.a)})"
    if isinstance(e, Bin):
        return f"({emit_expr(e.a)} {e.op} {emit_expr(e.b)})"
    if isinstance(e, Cond):
        return f"({emit_expr(e.c)} ? {emit_expr(e.a)} : {emit_expr(e.b)})"
    if isinstance(e, Switch):
        # In expression position a case statement is not available: chain compares.
        out = emit_expr(e.default)
        subject = emit_expr(e.subject)
        for lit, val in reversed(e.arms):
            out = f"(({subject} == {emit_expr(lit)}) ? {emit_expr(val)} : {out})"
        return out
    raise TypeError(f"not an Expr: {e!r}")


def emit_assign(lhs: str, e: Expr, op: str, indent: str) -> list[str]:
    """Lines assigning ``e`` to ``lhs``; a Switch becomes a case statement."""
    if not isinstance(e, Switch):
        return [f"{indent}{lhs} {op} {emit_expr(e)};"]
    lines = [f"{indent}case ({emit_expr(e.subject)})"]
    inner = indent + "    "
    for lit, val in e.arms + ((None, e.default),):
        label = "default" if lit is None else emit_expr(lit)
        body = emit_assign(lhs, val, op, inner + "    ")
        if len(body) == 1:
            lines.append(f"{inner}{label}: {body[0].strip()}")
        else:
            lines.append(f"{inner}{label}: begin")
            lines += body
            lines.append(f"{inner}end")
    lines.append(f"{indent}endcase")
    return lines


def _kind(sig: Signal) -> str:
    return sig.enum or "logic"


def _range(sig: Signal) -> str:
    return "" if sig.enum else vrange(sig.width)


def _decl(sig: Signal) -> str:
    rng = _range(sig)
    return f"{_kind(sig)} {rng} {sig.name}" if rng else f"{_kind(sig)} {sig.name}"


def emit_verilog(d: Design) -> str:
    """Render the design as SystemVerilog."""
    regs = {r.name: r for r in d.regs}
    lines: list[str] = []

    for name, labels in d.enums.items():
        w = Design.enum_width(labels)
        members = ", ".join(f"{Design.member(l)} = {w}'d{k}" for k, l in enumerate(labels))
        lines.append(f"typedef enum logic {vrange(w) or '[0:0]'} {{{members}}} {name};")
    if d.enums:
        lines.append("")

    ports: list[tuple[str, str, str, str]] = [
        ("input", "logic", "", "clk"),
        ("input", "logic", "", "rst"),
    ]
    ports += [("input", _kind(s), _range(s), s.name) for s in d.inputs]
    ports += [("output", _kind(regs[o]), _range(regs[o]), o) for o in d.outputs]
    kind_w = max(len(p[1]) for p in ports)
    rng_w = max(len(p[2]) for p in ports)

    lines.append(f"module {d.name} (")
    for k, (direction, kind, rng, name) in enumerate(ports):
        comma = "," if k < len(ports) - 1 else ""
        if rng_w:
            lines.append(f"    {direction:<6} {kind:<{kind_w}} {rng:<{rng_w}} {name}{comma}")
        else:
            lines.append(f"    {direction:<6} {kind:<{kind_w}} {name}{comma}")
    lines.append(");")

    for r in d.regs:
        if r.name not in d.outputs:
            lines.append(f"    {_decl(r)};")
    for name, e in d.wires:
        lines.append(f"    {_decl(d.wire_signals[name])};")
        body = emit_assign(name, e, "=", "        ")
        if len(body) == 1:
            lines.append(f"    always_comb {body[0].strip()}")
        else:
            lines.append("    always_comb begin")
            lines += body
            lines.append("    end")

    lines.append("")
    lines.append("    always_ff @(posedge clk) begin")
    lines.append("        if (rst) begin")
    for r in d.regs:
        lines.append(f"            {r.name} <= {emit_expr(d.reset[r.name])};")
    lines.append("        end else begin")
    for r in d.regs:
        lines += emit_assign(r.name, d.next[r.name], "<=", "            ")
    lines.append("        end")
    lines.append("    end")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"
