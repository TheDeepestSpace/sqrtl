"""A small structural clock-domain-crossing lint over a Design.

Rules (warnings, never errors):

1. A register may read a register of another clock domain only as a direct
   copy of a 1-bit signal: that register is a synchronizer stage. Anything
   else (logic on a foreign register, or a direct copy of a multi-bit one)
   is an unsynchronized crossing.
2. The first stage of a synchronizer may only be read as a direct copy by a
   second register. Logic that reads it sees a metastable flop.
3. A synchronizer's first stage should not be a module output.
"""

from __future__ import annotations

from dataclasses import dataclass

from .parse import Bin, Cond, Design, Expr, Lit, Ref, Switch, Un

__all__ = ["CdcWarning", "cdc_warnings", "refs"]


@dataclass(frozen=True)
class CdcWarning:
    register: str
    message: str
    line: int
    col: int


def refs(e: Expr) -> set[str]:
    """Names referenced by an expression."""
    if isinstance(e, Ref):
        return {e.name}
    if isinstance(e, Lit):
        return set()
    if isinstance(e, Un):
        return refs(e.a)
    if isinstance(e, Bin):
        return refs(e.a) | refs(e.b)
    if isinstance(e, Cond):
        return refs(e.c) | refs(e.a) | refs(e.b)
    if isinstance(e, Switch):
        out = refs(e.subject) | refs(e.default)
        for lit, val in e.arms:
            out |= refs(val)
        return out
    raise TypeError(f"not an Expr: {e!r}")


def cdc_warnings(d: Design) -> list[CdcWarning]:
    if len(d.clock_names()) < 2:
        return []
    regs = {r.name: r for r in d.regs}
    wires = dict(d.wires)

    def regs_read(e: Expr) -> set[str]:
        out: set[str] = set()
        todo = list(refs(e))
        seen: set[str] = set()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in regs:
                out.add(n)
            elif n in wires:
                todo += list(refs(wires[n]))
        return out

    warnings: list[CdcWarning] = []

    def warn(reg: str, message: str) -> None:
        line, col = d.reg_pos.get(reg, (1, 1))
        warnings.append(CdcWarning(reg, message, line, col))

    stage1: dict[str, str] = {}  # synchronizer first stage -> the foreign register it copies
    for r in d.regs:
        e = d.next[r.name]
        dom = d.domain[r.name]
        foreign = sorted(q for q in regs_read(e) if d.domain[q] != dom)
        if not foreign:
            continue
        if isinstance(e, Ref) and e.name in regs:
            src = regs[e.name]
            if src.width == 1:
                stage1[r.name] = e.name
            else:
                warn(r.name, f"{r.name} ({dom}) copies {src.width}-bit {src.name} ({d.domain[src.name]}) across "
                             f"clock domains; synchronize a 1-bit flag instead")
            continue
        for q in foreign:
            warn(r.name, f"{r.name} ({dom}) reads {q} ({d.domain[q]}) without a synchronizer")

    for r in d.regs:
        e = d.next[r.name]
        if isinstance(e, Ref):
            continue  # a direct copy is the second stage
        for q in sorted(regs_read(e)):
            if q in stage1:
                warn(r.name, f"{r.name} reads synchronizer stage {q} through logic; read it via a second register")

    for name in d.outputs:
        if name in stage1:
            warn(name, f"synchronizer stage {name} is a module output; expose the second stage instead")

    return warnings
