"""
Operation-Primal Design
========================
The OPERATION is the primary unit of organisation.
Types are plain data (tagged structs); behaviour lives in standalone functions.

  Adding a new OPERATION →  cheap: one new function, nothing else changes  ✓
  Adding a new TYPE      →  only update functions that need specific logic  ★

Casey Muratori's key insight (from the debate):
  Operations divide into two kinds:

    GENERIC operations work on any type via structural recursion or a
    `case _:` fallback.  When you add a new type, these operations already
    handle it — zero changes required.

    TYPE-SPECIFIC operations have distinct logic per type.  When you add a
    new type, you add one branch to each of those — nothing else changes.

  IO-driver analogy: adding opcode RIO_trim to an OS IO subsystem means old
  drivers hit their `default: return UNSUPPORTED` branch and keep working.
  ZERO existing drivers need recompiling.  In the vtable design, adding one
  new virtual method forces every one of the 1000+ existing drivers to be
  recompiled or wrapped — even drivers that will never support trim.

In Python we replicate this with an Enum for the tag and a dataclass for
the payload.  The `match` statement (Python 3.10+) mirrors Rust's pattern
matching.  Domain: same expression tree as operand_primal.py.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto


# ── Data: tag + payload, no methods ──────────────────────────────────────

class ExprKind(Enum):
    NUM = auto()   # a literal number
    VAR = auto()   # a named variable
    ADD = auto()   # left + right
    MUL = auto()   # left * right
    NEG = auto()   # -operand
    POW = auto()   # base ^ exponent  ← added in the extension section below


@dataclass
class Expr:
    """Plain data.  The kind tag tells operations how to interpret the rest."""
    kind:     ExprKind
    value:    float       = 0.0
    name:     str         = ""
    children: list[Expr]  = field(default_factory=list)


# ── Constructors ──────────────────────────────────────────────────────────

def num(v: float)              -> Expr: return Expr(ExprKind.NUM, value=v)
def var(n: str)                -> Expr: return Expr(ExprKind.VAR, name=n)
def add(l: Expr, r: Expr)      -> Expr: return Expr(ExprKind.ADD, children=[l, r])
def mul(l: Expr, r: Expr)      -> Expr: return Expr(ExprKind.MUL, children=[l, r])
def neg(e: Expr)               -> Expr: return Expr(ExprKind.NEG, children=[e])
def pow_expr(b: Expr, e: Expr) -> Expr: return Expr(ExprKind.POW, children=[b, e])


# ── TYPE-SPECIFIC operations (need one branch per type) ──────────────────

def evaluate(expr: Expr, env: dict[str, float]) -> float:
    """Each kind has distinct evaluation semantics — explicit case required."""
    match expr.kind:
        case ExprKind.NUM: return expr.value
        case ExprKind.VAR: return env[expr.name]
        case ExprKind.ADD: return evaluate(expr.children[0], env) + evaluate(expr.children[1], env)
        case ExprKind.MUL: return evaluate(expr.children[0], env) * evaluate(expr.children[1], env)
        case ExprKind.NEG: return -evaluate(expr.children[0], env)
        case ExprKind.POW: return evaluate(expr.children[0], env) ** evaluate(expr.children[1], env)
        # Adding a future type that can't be evaluated could use:
        #   case _: return float("nan")   ← graceful degradation, like a driver's default: branch


def pretty(expr: Expr) -> str:
    """Each kind has its own display syntax — explicit case required."""
    match expr.kind:
        case ExprKind.NUM:
            v = expr.value
            return str(int(v) if v == int(v) else v)
        case ExprKind.VAR: return expr.name
        case ExprKind.ADD: return f"({pretty(expr.children[0])} + {pretty(expr.children[1])})"
        case ExprKind.MUL: return f"({pretty(expr.children[0])} * {pretty(expr.children[1])})"
        case ExprKind.NEG: return f"(-{pretty(expr.children[0])})"
        case ExprKind.POW: return f"({pretty(expr.children[0])} ^ {pretty(expr.children[1])})"
        # Graceful degradation for unknown future types:
        case _:            return f"<{expr.kind.name}({', '.join(pretty(c) for c in expr.children)})>"


# ── GENERIC operations (already work for any type) ────────────────────────

def variables(expr: Expr) -> set[str]:
    """
    Recurses over children for any compound type via `case _:`.
    Adding ExprKind.POW required ZERO changes here.
    """
    match expr.kind:
        case ExprKind.VAR: return {expr.name}
        case ExprKind.NUM: return set()
        case _:            return set().union(*(variables(c) for c in expr.children))


# ════════════════════════════════════════════════════════════════════════
#  ADDING A NEW OPERATION — the operation-primal sweet spot
#
#  depth() counts the maximum nesting level of an expression.
#  Cost: write one function.  Every constructor and every existing
#  operation above are completely untouched.
# ════════════════════════════════════════════════════════════════════════

def depth(expr: Expr) -> int:
    """
    GENERIC: cares only about tree structure, not what each node means.
    Works for every existing type AND for POW without any modification.
    Adding ExprKind.POW required ZERO changes here.
    """
    if not expr.children:
        return 0
    return 1 + max(depth(c) for c in expr.children)


# ════════════════════════════════════════════════════════════════════════
#  ADDING A NEW TYPE — the actual cost, per Casey's argument
#
#  We added ExprKind.POW.  Here is everything that changed:
#
#    1. ExprKind.POW = auto()        in the enum          ← 1 line
#    2. def pow_expr(b, e)           constructor           ← 1 line
#    3. case ExprKind.POW: ...       in evaluate()         ← 1 line  (type-specific)
#    4. case ExprKind.POW: ...       in pretty()           ← 1 line  (type-specific)
#    5. variables()                  already worked        ← 0 lines (generic via case _:)
#    6. depth()                      already worked        ← 0 lines (generic via children)
#
#  Total: 4 lines in 3 places — and two of those places are the enum and
#  constructor, which you always have to write regardless of design.
#
#  Compare operand-primal cost for adding one new OPERATION (e.g. depth()):
#    → Add abstract method to Expr base class
#    → Implement in Num, Var, Add, Mul, Neg, Pow  (N classes → N implementations)
#    → With 20 types: 21 files touched for one new operation
#
#  The IO-driver version of this (Casey's actual argument):
#  ─────────────────────────────────────────────────────────
#  1000 device drivers are compiled into an OS.
#
#  Vtable design — add virtual method trim():
#    All 1000 drivers need recompiling; vtables are the wrong size otherwise.
#    Each vendor must ship an update even if their device has no trim support.
#
#  Opcode design — add RIO_trim to the enum:
#    999 old drivers hit `default: return UNSUPPORTED` → zero changes needed.
#    1 new SSD driver handles RIO_trim explicitly.
#    No recompilation of anything existing.
# ════════════════════════════════════════════════════════════════════════


# ── Demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    expr = add(mul(var("x"), num(2)), neg(var("y")))
    env  = {"x": 3.0, "y": 1.0}

    print("┌── Operation-Primal ───────────────────────────┐")
    print(f"│  expr      = {pretty(expr):<32} │")
    print(f"│  variables = {str(variables(expr)):<32} │")
    print(f"│  evaluate  = {evaluate(expr, env):<32} │")
    print(f"│  depth     = {depth(expr):<32} │")
    print("└───────────────────────────────────────────────┘")

    print("\n  ── New OPERATION: depth() — added for free ──")
    deep = add(add(add(var("x"), num(1)), num(2)), num(3))
    print(f"  depth({pretty(deep)}) = {depth(deep)}")

    print("\n  ── New TYPE: POW — what already worked without changes ──")
    p = pow_expr(var("x"), num(3))
    print(f"  pow_expr: {pretty(p)} = {evaluate(p, env)}")
    print(f"  variables({pretty(p)}) = {variables(p)}  ← generic, zero changes")
    print(f"  depth({pretty(p)})     = {depth(p)}         ← generic, zero changes")
