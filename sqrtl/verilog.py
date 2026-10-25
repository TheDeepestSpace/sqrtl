"""Design -> Verilog-2005 or SystemVerilog text. Pure: no I/O, no sqlglot,
deterministic output."""

from __future__ import annotations

from .parse import Bin, Cond, Design, Expr, Lit, Ref, Signal, Switch, Un

__all__ = ["emit_verilog", "emit_expr", "vrange"]


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


def _kind(sig: Signal, sv: bool, default: str) -> str:
    return sig.enum if (sv and sig.enum) else default


def _decl(kind: str, sig: Signal, sv: bool) -> str:
    rng = "" if (sv and sig.enum) else vrange(sig.width)
    return f"{kind} {rng} {sig.name}" if rng else f"{kind} {sig.name}"


def emit_verilog(d: Design, sv: bool = False) -> str:
    """Render the design. ``sv=False``: Verilog-2005 (wire/reg, always @(posedge clk),
    enums as localparams). ``sv=True``: SystemVerilog (logic, typedef enum,
    always_comb, always_ff)."""
    regs = {r.name: r for r in d.regs}
    lines: list[str] = []

    if sv:
        for name, labels in d.enums.items():
            w = Design.enum_width(labels)
            members = ", ".join(f"{Design.member(l)} = {w}'d{k}" for k, l in enumerate(labels))
            lines.append(f"typedef enum logic {vrange(w) or '[0:0]'} {{{members}}} {name};")
        if d.enums:
            lines.append("")

    ports: list[tuple[str, str, str, str]] = []
    in_kind, reg_kind = ("logic", "logic") if sv else ("wire", "reg")
    ports.append(("input", in_kind, "", "clk"))
    ports.append(("input", in_kind, "", "rst"))
    for s in d.inputs:
        ports.append(("input", _kind(s, sv, in_kind), "" if (sv and s.enum) else vrange(s.width), s.name))
    for o in d.outputs:
        s = regs[o]
        ports.append(("output", _kind(s, sv, reg_kind), "" if (sv and s.enum) else vrange(s.width), o))
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

    if not sv:
        for name, labels in d.enums.items():
            w = Design.enum_width(labels)
            for k, l in enumerate(labels):
                lines.append(f"    localparam {vrange(w) + ' ' if w > 1 else ''}{Design.member(l)} = {w}'d{k};")

    for r in d.regs:
        if r.name not in d.outputs:
            lines.append(f"    {_decl(_kind(r, sv, reg_kind), r, sv)};")
    for name, e in d.wires:
        sig = d.wire_signals[name]
        if sv:
            lines.append(f"    {_decl(_kind(sig, sv, 'logic'), sig, sv)};")
            body = emit_assign(name, e, "=", "        ")
            if len(body) == 1:
                lines.append(f"    always_comb {body[0].strip()}")
            else:
                lines.append("    always_comb begin")
                lines += body
                lines.append("    end")
        else:
            lines.append(f"    {_decl('wire', sig, sv)} = {emit_expr(e)};")

    lines.append("")
    lines.append(f"    {'always_ff' if sv else 'always'} @(posedge clk) begin")
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
