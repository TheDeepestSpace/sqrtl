"""design.sql -> Design.

This module is the only place that touches sqlglot. It lowers the SQL AST
into the small Expr tree the rest of the transpiler owns and rejects anything
outside the v1 subset while doing so. It performs no I/O: callers hand it the
text of design.sql.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

__all__ = [
    "SqrtlError",
    "Ref", "Lit", "Un", "Bin", "Cond", "Switch", "Expr",
    "Signal", "Design",
    "parse_design",
    "RESERVED",
]


class SqrtlError(Exception):
    """A diagnostic. ``line``/``col`` are 1-based positions in design.sql."""

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self) -> str:
        if self.line is None:
            return self.message
        return f"{self.line}:{self.col}: {self.message}"


# --------------------------------------------------------------------------
# Expr: the tree verilog.py consumes. Operators are stored as Verilog tokens.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Lit:
    value: int
    width: int
    base: str = "d"  # 'd' -> 8'd1, 'b' -> 4'b0101
    label: str | None = None  # enum member name (emitted instead of the number)


@dataclass(frozen=True)
class Un:
    op: str  # '!' or '~'
    a: "Expr"


@dataclass(frozen=True)
class Bin:
    op: str  # '&&' '||' '==' '!=' '&' '|' '^' '+' '-'
    a: "Expr"
    b: "Expr"


@dataclass(frozen=True)
class Cond:
    c: "Expr"
    a: "Expr"
    b: "Expr"


@dataclass(frozen=True)
class Switch:
    """A simple CASE: `CASE subject WHEN lit THEN a ... ELSE d END`.

    Emitted as a Verilog `case` statement where a statement is allowed and as
    a chain of `==` conditionals inside expressions.
    """
    subject: "Expr"
    arms: tuple[tuple[Lit, "Expr"], ...]
    default: "Expr"


Expr = Union[Ref, Lit, Un, Bin, Cond, Switch]


@dataclass
class Signal:
    name: str
    width: int  # 1 for boolean
    is_bool: bool
    enum: str | None = None  # SQL enum type name, if the signal is an enum

    @property
    def sql_type(self) -> str:
        if self.enum:
            return self.enum
        return "boolean" if self.is_bool else f"bit({self.width})"


@dataclass
class Design:
    name: str
    inputs: list[Signal]
    regs: list[Signal]
    outputs: list[str]
    reset: dict[str, Expr]
    wires: list[tuple[str, Expr]]
    next: dict[str, Expr]
    wire_signals: dict[str, Signal] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)  # type name -> labels, in order

    @staticmethod
    def enum_width(labels: list[str]) -> int:
        return max(1, (len(labels) - 1).bit_length())

    @staticmethod
    def member(label: str) -> str:
        """The Verilog identifier of an enum label."""
        return label.upper()

    def signal(self, name: str) -> Signal:
        for s in self.inputs + self.regs:
            if s.name == name:
                return s
        return self.wire_signals[name]


# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------

NAME_RE = re.compile(r"[a-z][a-z0-9_]*\Z")

VERILOG_KEYWORDS = frozenset("""
always and assign automatic begin buf bufif0 bufif1 case casex casez cell cmos
config deassign default defparam design disable edge else end endcase endconfig
endfunction endgenerate endmodule endprimitive endspecify endtable endtask event
for force forever fork function generate genvar highz0 highz1 if ifnone incdir
include initial inout input instance integer join large liblist library
localparam macromodule medium module nand negedge nmos nor noshowcancelled not
notif0 notif1 or output parameter pmos posedge primitive pull0 pull1 pulldown
pullup pulsestyle_onevent pulsestyle_ondetect rcmos real realtime reg release
repeat rnmos rpmos rtran rtranif0 rtranif1 scalared showcancelled signed small
specify specparam strong0 strong1 supply0 supply1 table task time tran tranif0
tranif1 tri tri0 tri1 triand trior trireg unsigned use uwire vectored wait wand
weak0 weak1 while wire wor xnor xor
""".split())

RESERVED = frozenset({"clk", "rst", "cycle", "sim", "s", "i"}) | VERILOG_KEYWORDS


# --------------------------------------------------------------------------
# Types used while lowering. 'int' is the unbounded integer produced by ::int
# and arithmetic; it must be wrapped in ::bit(n) before it reaches a signal.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Ty:
    kind: str  # 'bool' | 'bits' | 'enum'
    width: int
    enum: str | None = None

    def __str__(self) -> str:
        if self.kind == "enum":
            return self.enum or "enum"
        return "boolean" if self.kind == "bool" else f"bit({self.width})"


_BOOL = _Ty("bool", 1)


def _bits(n: int) -> _Ty:
    return _Ty("bits", n)


def _ty_of(sig: Signal) -> _Ty:
    if sig.enum:
        return _Ty("enum", sig.width, sig.enum)
    return _BOOL if sig.is_bool else _bits(sig.width)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class _Parser:
    def __init__(self, src: str):
        self.src = src
        self.name = ""
        self.inputs: dict[str, Signal] = {}
        self.regs: dict[str, Signal] = {}
        # lateral alias -> {column -> type}
        self.laterals: dict[str, dict[str, _Ty]] = {}
        self.wires: list[tuple[str, Expr]] = []
        self.wire_signals: dict[str, Signal] = {}
        self.enums: dict[str, list[str]] = {}

    # -- diagnostics -------------------------------------------------------

    def pos(self, node: exp.Expression | None) -> tuple[int, int]:
        if node is None:
            return 1, 1
        starts = [n.meta["start"] for n in node.walk() if n.meta and "start" in n.meta]
        if not starts:
            return 1, 1
        off = min(starts)
        line = self.src.count("\n", 0, off) + 1
        col = off - (self.src.rfind("\n", 0, off) + 1) + 1
        return line, col

    def err(self, message: str, node: exp.Expression | None) -> SqrtlError:
        line, col = self.pos(node)
        return SqrtlError(message, line, col)

    def check_name(self, name: str, node: exp.Expression | None) -> str:
        if not NAME_RE.match(name) or name in RESERVED:
            raise self.err(f"invalid name: {name}", node)
        return name

    def check_unique(self, name: str, node: exp.Expression | None) -> None:
        if name in self.inputs or name in self.regs or name in self.wire_signals:
            raise self.err(f"duplicate name: {name}", node)

    # -- entry -------------------------------------------------------------

    def parse(self) -> Design:
        try:
            stmts = [s for s in sqlglot.parse(self.src, dialect="postgres") if s is not None]
        except ParseError as e:
            info = e.errors[0] if e.errors else {}
            raise SqrtlError(f"syntax error: {info.get('description', e)}",
                             info.get("line", 1), info.get("col", 1)) from None
        while stmts and isinstance(stmts[0], exp.Create) and stmts[0].args.get("kind") == "TYPE":
            self.parse_enum(stmts.pop(0))
        if (len(stmts) != 2
                or not isinstance(stmts[0], exp.Create) or stmts[0].args.get("kind") != "TABLE"
                or not isinstance(stmts[1], exp.Create) or stmts[1].args.get("kind") != "VIEW"):
            node = stmts[0] if stmts else None
            raise self.err("expected CREATE TABLE then CREATE VIEW", node)
        self.parse_table(stmts[0])
        outputs, reset, nxt = self.parse_view(stmts[1])
        return Design(
            name=self.name,
            inputs=list(self.inputs.values()),
            regs=list(self.regs.values()),
            outputs=outputs,
            reset=reset,
            wires=self.wires,
            next=nxt,
            wire_signals=self.wire_signals,
            enums=self.enums,
        )

    # -- CREATE TYPE <name> AS ENUM (...) -----------------------------------

    def parse_enum(self, create: exp.Create) -> None:
        name = self.check_name(create.this.name, create.this)
        body = create.expression
        if not (isinstance(body, exp.DataType) and body.this == exp.DataType.Type.ENUM and body.expressions
                and all(isinstance(e, exp.Literal) and e.is_string for e in body.expressions)):
            raise self.err(f"CREATE TYPE {name} must be AS ENUM ('label', ...)", create)
        if name in self.enums:
            raise self.err(f"duplicate name: {name}", create.this)
        members = {Design.member(l) for labels in self.enums.values() for l in labels}
        labels: list[str] = []
        for lit in body.expressions:
            label = self.check_name(lit.this, lit)
            if label in labels or Design.member(label) in members:
                raise self.err(f"duplicate name: {label}", lit)
            labels.append(label)
        self.enums[name] = labels

    def enum_lit(self, enum: str, label: str, node: exp.Expression) -> Lit:
        labels = self.enums[enum]
        if label not in labels:
            raise self.err(f"unknown enum label '{label}' for {enum}", node)
        return Lit(labels.index(label), Design.enum_width(labels), "d", Design.member(label))

    @staticmethod
    def _strlit(node: exp.Expression) -> exp.Literal | None:
        while isinstance(node, exp.Paren):
            node = node.this
        return node if isinstance(node, exp.Literal) and node.is_string else None

    def _enum_of(self, dt: exp.DataType | None) -> str | None:
        if dt is not None and dt.this == exp.DataType.Type.USERDEFINED:
            kind = dt.args.get("kind")
            name = kind.name if isinstance(kind, exp.Expression) else str(kind or "")
            if name in self.enums:
                return name
        return None

    # -- CREATE TABLE <name>_in --------------------------------------------

    def parse_table(self, create: exp.Create) -> None:
        schema = create.this
        if not isinstance(schema, exp.Schema):
            raise self.err("port table needs cycle integer PRIMARY KEY", create)
        table_name = schema.this.name
        if not table_name.endswith("_in") or len(table_name) <= 3:
            raise self.err(f"port table must be named <name>_in, got {table_name}", schema.this)
        self.name = self.check_name(table_name[:-3], schema.this)

        cycle_ok = False
        table_pk: list[str] | None = None
        coldefs: list[exp.ColumnDef] = []
        for item in schema.expressions:
            if isinstance(item, exp.ColumnDef):
                coldefs.append(item)
            elif isinstance(item, exp.PrimaryKey):
                table_pk = [e.name for e in item.expressions]
            else:
                raise self.err(f"unsupported expression: {item.sql(dialect='postgres')}", item)

        for cd in coldefs:
            col = cd.this.name
            kinds = [c.kind for c in cd.args.get("constraints") or []]
            if col == "cycle":
                is_int = cd.kind is not None and cd.kind.this in (exp.DataType.Type.INT,)
                has_pk = any(isinstance(k, exp.PrimaryKeyColumnConstraint) for k in kinds) or table_pk == ["cycle"]
                if not (is_int and has_pk):
                    raise self.err("port table needs cycle integer PRIMARY KEY", cd)
                cycle_ok = True
                continue
            self.check_name(col, cd.this)
            self.check_unique(col, cd.this)
            sig = self.signal_type(col, cd.kind, cd)
            if not any(isinstance(k, exp.NotNullColumnConstraint) for k in kinds):
                raise self.err(f"column {col} must be NOT NULL", cd)
            for k in kinds:
                if not isinstance(k, exp.NotNullColumnConstraint):
                    raise self.err(f"unsupported constraint on column {col}", cd)
            self.inputs[col] = sig
        if not cycle_ok:
            raise self.err("port table needs cycle integer PRIMARY KEY", schema)

    def signal_type(self, col: str, dt: exp.DataType | None, node: exp.Expression) -> Signal:
        if dt is not None:
            enum = self._enum_of(dt)
            if enum:
                return Signal(col, Design.enum_width(self.enums[enum]), False, enum)
            if dt.this == exp.DataType.Type.BOOLEAN:
                return Signal(col, 1, True)
            if dt.this == exp.DataType.Type.BIT:
                params = dt.expressions
                if not params:
                    return Signal(col, 1, False)
                if len(params) == 1 and isinstance(params[0].this, exp.Literal) and not params[0].this.is_string:
                    n = int(params[0].this.this)
                    if 1 <= n <= 64:
                        return Signal(col, n, False)
        tname = dt.sql(dialect="postgres").lower() if dt is not None else "unknown"
        tname = {"int": "integer"}.get(tname, tname)
        raise self.err(f"unsupported type {tname} for column {col}", node)

    # -- CREATE VIEW <name>_out ---------------------------------------------

    def parse_view(self, create: exp.Create):
        view_name = create.this.name
        if view_name != f"{self.name}_out":
            raise self.err(f"port table and design view names differ: {self.name}_in vs {view_name}", create.this)
        outer = create.expression
        with_ = outer.args.get("with_") if isinstance(outer, exp.Select) else None
        if (with_ is None or not with_.args.get("recursive") or len(with_.expressions) != 1
                or with_.expressions[0].alias != "sim" or not with_.expressions[0].alias_column_names):
            raise self.err("design view must be WITH RECURSIVE sim(...)", outer)
        cte = with_.expressions[0]
        columns = cte.alias_column_names
        if columns[0] != "cycle":
            raise self.err("first sim column must be cycle", cte.args["alias"])
        reg_nodes = cte.args["alias"].args["columns"][1:]
        for ident in reg_nodes:
            self.check_name(ident.name, ident)

        body = cte.this
        if not (isinstance(body, exp.Union) and body.args.get("distinct") is False
                and isinstance(body.this, exp.Select) and isinstance(body.expression, exp.Select)):
            raise self.err("sim must be <base> UNION ALL <step>", body)

        reset = self.parse_base(body.this, reg_nodes)
        nxt = self.parse_step(body.expression, reg_nodes)
        outputs = self.parse_outer(outer)
        return outputs, reset, nxt

    def _plain_select(self, sel: exp.Select, what: str) -> None:
        for key in ("where", "group", "having", "order", "limit", "offset", "distinct", "qualify", "windows"):
            if sel.args.get(key):
                raise self.err(f"{what} may not have {key.upper()}", sel.args[key])

    # base case: the reset state
    def parse_base(self, sel: exp.Select, reg_nodes: list[exp.Identifier]) -> dict[str, Expr]:
        self._plain_select(sel, "base case")
        if sel.args.get("from_") or sel.args.get("joins"):
            raise self.err("base case may not have FROM", sel)
        exprs = sel.expressions
        if len(exprs) != len(reg_nodes) + 1:
            raise self.err(f"base case has {len(exprs)} columns, sim has {len(reg_nodes) + 1}", sel)
        first = exprs[0]
        if not (isinstance(first, exp.Literal) and not first.is_string and first.this == "0"):
            raise self.err("base case must start with cycle 0", first)
        reset: dict[str, Expr] = {}
        for ident, node in zip(reg_nodes, exprs[1:]):
            name = ident.name
            self.check_unique(name, ident)
            lit, sig = self.reset_literal(name, node)
            self.regs[name] = sig
            reset[name] = lit
        return reset

    def reset_literal(self, name: str, node: exp.Expression) -> tuple[Lit, Signal]:
        inner = node.this if isinstance(node, exp.Alias) else node
        if isinstance(inner, exp.Boolean):
            return Lit(1 if inner.this else 0, 1, "b"), Signal(name, 1, True)
        if isinstance(inner, exp.Cast):
            sig = self.signal_type(name, inner.to, inner)
            operand = inner.this
            while isinstance(operand, exp.Paren):
                operand = operand.this
            if sig.enum:
                lit = self._strlit(operand)
                if lit is not None:
                    return self.enum_lit(sig.enum, lit.this, lit), sig
            elif sig.is_bool:
                if isinstance(operand, exp.Boolean):
                    return Lit(1 if operand.this else 0, 1, "b"), sig
            elif isinstance(operand, exp.BitString):
                bits = str(operand.this)
                if len(bits) != sig.width:
                    raise self.err(f"width mismatch: {len(bits)} vs {sig.width}", operand)
                return Lit(int(bits, 2), sig.width, "d"), sig
            elif isinstance(operand, exp.Literal) and not operand.is_string and operand.this.isdigit():
                value = int(operand.this)
                if value >= 1 << sig.width:
                    raise self.err(f"literal {value} does not fit in bit({sig.width})", operand)
                return Lit(value, sig.width, "d"), sig
        raise self.err(f"reset value of {name} must be true, false, B'...'::bit(n) or 'label'::enum", node)

    # step: FROM shape, laterals, next-state expressions
    def parse_step(self, sel: exp.Select, reg_nodes: list[exp.Identifier]) -> dict[str, Expr]:
        shape_msg = f"step must be FROM sim s JOIN {self.name}_in i ON i.cycle = s.cycle"
        self._plain_select(sel, "step")
        from_ = sel.args.get("from_")
        joins = list(sel.args.get("joins") or [])
        if not (from_ and isinstance(from_.this, exp.Table) and from_.this.name == "sim"
                and from_.this.alias == "s" and joins):
            raise self.err(shape_msg, from_ or sel)
        j0 = joins[0]
        on = j0.args.get("on")
        if not (isinstance(j0.this, exp.Table) and j0.this.name == f"{self.name}_in" and j0.this.alias == "i"
                and not j0.args.get("side") and j0.args.get("kind") in (None, "INNER")
                and isinstance(on, exp.EQ) and self._is_col(on.this) and self._is_col(on.expression)
                and {self._colkey(on.this), self._colkey(on.expression)} == {("i", "cycle"), ("s", "cycle")}):
            raise self.err(shape_msg, j0)
        for j in joins[1:]:
            lat = j.this
            if not (isinstance(lat, exp.Lateral) and not j.args.get("on") and not j.args.get("side")
                    and j.args.get("kind") in (None, "CROSS") and isinstance(lat.this, exp.Subquery)
                    and isinstance(lat.this.this, exp.Select) and lat.alias):
                raise self.err(shape_msg, j)
            self.parse_lateral(lat)

        exprs = sel.expressions
        if len(exprs) != len(reg_nodes) + 1:
            raise self.err(f"step has {len(exprs)} columns, sim has {len(reg_nodes) + 1}", sel)
        first = exprs[0]
        if not (isinstance(first, exp.Add) and self._is_col(first.this) and self._colkey(first.this) == ("s", "cycle")
                and isinstance(first.expression, exp.Literal) and first.expression.this == "1"):
            raise self.err("step must start with s.cycle + 1", first)
        nxt: dict[str, Expr] = {}
        for ident, node in zip(reg_nodes, exprs[1:]):
            name = ident.name
            if isinstance(node, exp.Alias):
                if node.alias != name:
                    raise self.err(f"column alias {node.alias} does not match sim column {name}", node)
                node = node.this
            want = _ty_of(self.regs[name])
            e, ty = self.lower(node, want)
            self.require_same(ty, want, node, f"{name} is {want} but expression is {ty}")
            nxt[name] = e
        return nxt

    def parse_lateral(self, lat: exp.Lateral) -> None:
        alias = lat.alias
        self.check_name(alias, lat.args["alias"])
        if alias in self.laterals:
            raise self.err(f"duplicate name: {alias}", lat.args["alias"])
        sub = lat.this.this
        self._plain_select(sub, "lateral")
        if sub.args.get("from_") or sub.args.get("joins") or sub.args.get("with_"):
            raise self.err("lateral may not contain FROM", sub)
        cols: dict[str, _Ty] = {}
        for item in sub.expressions:
            if not isinstance(item, exp.Alias):
                raise self.err("lateral column needs AS <wire>", item)
            wname = self.check_name(item.alias, item.args["alias"])
            self.check_unique(wname, item.args["alias"])
            e, ty = self.lower(item.this)
            cols[wname] = ty
            sig = Signal(wname, ty.width, ty.kind == "bool")
            self.wire_signals[wname] = sig
            self.wires.append((wname, e))
            # a later column of the same lateral may not see an earlier one (SQL scoping)
        self.laterals[alias] = cols

    # outer SELECT: the outputs
    def parse_outer(self, outer: exp.Select) -> list[str]:
        self._plain_select(outer, "outer SELECT")
        from_ = outer.args.get("from_")
        if not (from_ and isinstance(from_.this, exp.Table) and from_.this.name == "sim") or outer.args.get("joins"):
            raise self.err("outer SELECT must be FROM sim", from_ or outer)
        alias = from_.this.alias or "sim"
        outputs: list[str] = []
        has_cycle = False
        for item in outer.expressions:
            if not isinstance(item, exp.Column) or (item.table and item.table not in ("sim", alias)):
                raise self.err("outer SELECT must list cycle and register columns", item)
            name = item.name
            if name == "cycle":
                if has_cycle:
                    raise self.err("duplicate output cycle", item)
                has_cycle = True
                continue
            if name not in self.regs:
                raise self.err(f"output {name} is not a register", item)
            if name in outputs:
                raise self.err(f"duplicate output {name}", item)
            outputs.append(name)
        if not has_cycle:
            raise self.err("outer SELECT must include cycle", outer)
        return outputs

    # -- expression lowering ----------------------------------------------

    @staticmethod
    def _is_col(node: exp.Expression) -> bool:
        return isinstance(node, exp.Column)

    @staticmethod
    def _colkey(node: exp.Column) -> tuple[str, str]:
        return node.table, node.name

    def require_same(self, a: _Ty, b: _Ty, node: exp.Expression, detail: str) -> None:
        if a.kind != b.kind or a.enum != b.enum:
            raise self.err(f"type mismatch: {detail}", node)
        if a.width != b.width:
            raise self.err(f"width mismatch: {a.width} vs {b.width}", node)

    def source(self, node: exp.Expression) -> str:
        """The design.sql text a node was parsed from (falls back to re-rendering)."""
        spans = [(n.meta["start"], n.meta["end"]) for n in node.walk() if n.meta and "start" in n.meta]
        if not spans:
            return node.sql(dialect="postgres")
        start = min(s for s, _ in spans)
        end = max(e for _, e in spans)
        return " ".join(self.src[start:end + 1].split())

    def unsupported(self, node: exp.Expression) -> SqrtlError:
        if isinstance(node, exp.Cast):
            what = f"::{self.source(node.to).lower()}"
        elif isinstance(node, exp.Func):
            what = node.sql_name()
        else:
            what = self.source(node)
        return self.err(f"unsupported expression: {what}", node)

    def lower(self, node: exp.Expression, want: _Ty | None = None) -> tuple[Expr, _Ty]:
        """Lower a sqlglot expression to (Expr, type).

        ``want`` is the type the context expects; it only serves to give a
        bare enum label ('idle') its type, mirroring how PostgreSQL resolves an
        unknown-typed literal against the other operand of a comparison, the
        typed branches of a CASE, or the other arm of the UNION.
        """
        while isinstance(node, exp.Paren):
            node = node.this

        if isinstance(node, exp.Column):
            return self.lower_column(node)

        if isinstance(node, exp.Boolean):
            return Lit(1 if node.this else 0, 1, "b"), _BOOL

        if isinstance(node, exp.Literal) and node.is_string:
            if want is None or want.kind != "enum":
                hint = f"'{node.this}'::{want}" if want is not None else f"'{node.this}'::<enum type>"
                raise self.err(f"string literal '{node.this}' has no enum context (write {hint})", node)
            return self.enum_lit(want.enum, node.this, node), want

        if isinstance(node, (exp.And, exp.Or)):
            op = "&&" if isinstance(node, exp.And) else "||"
            sql_op = "AND" if isinstance(node, exp.And) else "OR"
            a, ta = self.lower(node.this)
            b, tb = self.lower(node.expression)
            for t, n in ((ta, node.this), (tb, node.expression)):
                if t.kind != "bool":
                    raise self.err(f"type mismatch: {sql_op} needs boolean operands, got {t}", n)
            return Bin(op, a, b), _BOOL

        if isinstance(node, exp.Not):
            a, ta = self.lower(node.this)
            if ta.kind != "bool":
                raise self.err(f"type mismatch: NOT needs a boolean operand, got {ta}", node.this)
            return Un("!", a), _BOOL

        if isinstance(node, (exp.EQ, exp.NEQ)):
            if self._strlit(node.this) is not None and self._strlit(node.expression) is None:
                b, tb = self.lower(node.expression)
                a, ta = self.lower(node.this, tb)
            else:
                a, ta = self.lower(node.this)
                b, tb = self.lower(node.expression, ta)
            self.require_same(ta, tb, node, f"cannot compare {ta} with {tb}")
            return Bin("==" if isinstance(node, exp.EQ) else "!=", a, b), _BOOL

        if isinstance(node, (exp.BitwiseAnd, exp.BitwiseOr, exp.BitwiseXor)):
            op = {exp.BitwiseAnd: "&", exp.BitwiseOr: "|", exp.BitwiseXor: "^"}[type(node)]
            sql_op = {exp.BitwiseAnd: "&", exp.BitwiseOr: "|", exp.BitwiseXor: "#"}[type(node)]
            a, ta = self.lower(node.this)
            b, tb = self.lower(node.expression)
            for t, n in ((ta, node.this), (tb, node.expression)):
                if t.kind != "bits":
                    raise self.err(f"type mismatch: {sql_op} needs bit(n) operands, got {t}", n)
            if ta.width != tb.width:
                raise self.err(f"width mismatch: {ta.width} vs {tb.width}", node)
            return Bin(op, a, b), ta

        if isinstance(node, exp.BitwiseNot):
            a, ta = self.lower(node.this)
            if ta.kind != "bits":
                raise self.err(f"type mismatch: ~ needs a bit(n) operand, got {ta}", node.this)
            return Un("~", a), ta

        if isinstance(node, exp.Case):
            return self.lower_switch(node) if node.this is not None else self.lower_case(node)

        if isinstance(node, exp.Cast):
            return self.lower_cast(node)

        if isinstance(node, (exp.Add, exp.Sub)) or (isinstance(node, exp.Literal) and not node.is_string):
            raise self.err("unbounded integer in next-state logic", node)

        raise self.unsupported(node)

    def lower_column(self, node: exp.Column) -> tuple[Expr, _Ty]:
        table, name = node.table, node.name
        if not table:
            raise self.err(f"column {name} must be qualified with s., i. or a lateral alias", node)
        if table == "s":
            if name == "cycle":
                raise self.err("unbounded integer in next-state logic", node)
            if name not in self.regs:
                raise self.err(f"unknown column: s.{name}", node)
            return Ref(name), _ty_of(self.regs[name])
        if table == "i":
            if name == "cycle":
                raise self.err("unbounded integer in next-state logic", node)
            if name not in self.inputs:
                raise self.err(f"unknown column: i.{name}", node)
            return Ref(name), _ty_of(self.inputs[name])
        if table in self.laterals:
            cols = self.laterals[table]
            if name not in cols:
                raise self.err(f"unknown column: {table}.{name}", node)
            return Ref(name), cols[name]
        raise self.err(f"unknown column: {table}.{name}", node)

    def _case_values(self, nodes: list[exp.Expression], ctx: exp.Expression) -> tuple[list[Expr], _Ty]:
        """Lower the THEN/ELSE values of a CASE to one common type.

        Like PostgreSQL, bare labels take the type of the first typed value;
        a CASE whose values are all bare labels has no type.
        """
        anchor = next((n for n in nodes if self._strlit(n) is None), None)
        if anchor is None:
            raise self.err("CASE needs a typed branch (write one label as 'label'::enum)", ctx)
        _, ty = self.lower(anchor)
        values: list[Expr] = []
        for n in nodes:
            e, t = self.lower(n, ty)
            self.require_same(t, ty, n, f"CASE branches differ: {t} vs {ty}")
            values.append(e)
        return values, ty

    def lower_case(self, node: exp.Case) -> tuple[Expr, _Ty]:
        default = node.args.get("default")
        if default is None:
            raise self.err("CASE needs ELSE", node)
        ifs = node.args["ifs"]
        conds: list[Expr] = []
        for branch in ifs:
            c, tc = self.lower(branch.this)
            if tc.kind != "bool":
                raise self.err(f"type mismatch: WHEN needs a boolean condition, got {tc}", branch.this)
            conds.append(c)
        values, ty = self._case_values([b.args["true"] for b in ifs] + [default], node)
        result = values[-1]
        for c, a in zip(reversed(conds), reversed(values[:-1])):
            result = Cond(c, a, result)
        return result, ty

    def case_label(self, node: exp.Expression, tsub: _Ty) -> Lit:
        """A WHEN label of a simple CASE: a literal of the subject's type."""
        while isinstance(node, exp.Paren):
            node = node.this
        if tsub.kind == "enum":
            lit = self._strlit(node)
            if lit is None and isinstance(node, exp.Cast) and self._enum_of(node.to) == tsub.enum:
                lit = self._strlit(node.this)
            if lit is not None:
                return self.enum_lit(tsub.enum, lit.this, lit)
        elif tsub.kind == "bool":
            if isinstance(node, exp.Boolean):
                return Lit(1 if node.this else 0, 1, "b")
        else:
            e, t = self.lower(node)
            if isinstance(e, Lit):
                self.require_same(t, tsub, node, f"CASE label is {t} but subject is {tsub}")
                return e
        raise self.err(f"CASE label must be a {tsub} literal", node)

    def lower_switch(self, node: exp.Case) -> tuple[Expr, _Ty]:
        default = node.args.get("default")
        if default is None:
            raise self.err("CASE needs ELSE", node)
        subject, tsub = self.lower(node.this)
        ifs = node.args["ifs"]
        labels: list[Lit] = []
        for branch in ifs:
            lit = self.case_label(branch.this, tsub)
            if any(l.value == lit.value for l in labels):
                raise self.err(f"duplicate CASE label {lit.label or lit.value}", branch.this)
            labels.append(lit)
        values, ty = self._case_values([b.args["true"] for b in ifs] + [default], node)
        return Switch(subject, tuple(zip(labels, values[:-1])), values[-1]), ty

    def lower_cast(self, node: exp.Cast) -> tuple[Expr, _Ty]:
        to = node.to
        enum = self._enum_of(to)
        if enum:
            lit = self._strlit(node.this)
            if lit is None:
                raise self.err(f"only a label can be cast to {enum}", node)
            return self.enum_lit(enum, lit.this, lit), _Ty("enum", Design.enum_width(self.enums[enum]), enum)
        if to.this == exp.DataType.Type.INT:
            raise self.err("unbounded integer in next-state logic", node)
        if to.this != exp.DataType.Type.BIT:
            raise self.unsupported(node)
        sig = self.signal_type("<cast>", to, node)
        width = sig.width
        operand = node.this
        while isinstance(operand, exp.Paren):
            operand = operand.this
        if isinstance(operand, exp.BitString):
            bits = str(operand.this)
            if len(bits) != width:
                raise self.err(f"width mismatch: {len(bits)} vs {width}", operand)
            return Lit(int(bits, 2), width, "b"), _bits(width)
        return self.lower_int(operand, width), _bits(width)

    def lower_int(self, node: exp.Expression, width: int) -> Expr:
        """Lower an integer-typed expression whose result is wrapped in ::bit(width)."""
        while isinstance(node, exp.Paren):
            node = node.this
        if isinstance(node, (exp.Add, exp.Sub)):
            op = "+" if isinstance(node, exp.Add) else "-"
            return Bin(op, self.lower_int(node.this, width), self.lower_int(node.expression, width))
        if isinstance(node, exp.Literal) and not node.is_string and node.this.isdigit():
            value = int(node.this)
            if value >= 1 << width:
                raise self.err(f"literal {value} does not fit in bit({width})", node)
            return Lit(value, width, "d")
        if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.INT:
            e, ty = self.lower(node.this)
            if ty.kind != "bits":
                raise self.err(f"type mismatch: ::int needs a bit(n) operand, got {ty}", node.this)
            if ty.width != width:
                raise self.err(f"width mismatch: {ty.width} vs {width}", node)
            return e
        raise self.unsupported(node)


def parse_design(sql: str) -> Design:
    """Parse the text of a design.sql file into a Design, or raise SqrtlError."""
    return _Parser(sql).parse()
