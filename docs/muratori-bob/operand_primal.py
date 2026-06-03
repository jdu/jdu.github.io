"""
Operand-Primal Design
=====================
The TYPE is the primary unit of organisation.
Each type owns all the operations it will ever participate in.

  Adding a new TYPE      →  cheap: one new class, nothing else changes   ✓
  Adding a new OPERATION →  expensive: open every existing class          ✗

This is Bob Martin's preferred default.  In OOP languages it is also the
natural grain of the language (inheritance, virtual dispatch, polymorphism).

Domain: a simple expression tree.  The same problem is solved identically
in operation_primal.py — compare the two to see the tradeoff directly.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


# ── The contract: every Expr supports these operations ───────────────────
#    Adding an operation here forces a change in EVERY concrete class below.

class Expr(ABC):
    @abstractmethod
    def evaluate(self, env: dict[str, float]) -> float: ...

    @abstractmethod
    def pretty(self) -> str: ...

    @abstractmethod
    def variables(self) -> set[str]: ...


# ── Concrete types ───────────────────────────────────────────────────────
#    Each type is self-contained: it owns its own behaviour.

@dataclass
class Num(Expr):
    value: float

    def evaluate(self, env): return self.value
    def pretty(self):        return str(int(self.value) if self.value == int(self.value) else self.value)
    def variables(self):     return set()


@dataclass
class Var(Expr):
    name: str

    def evaluate(self, env): return env[self.name]
    def pretty(self):        return self.name
    def variables(self):     return {self.name}


@dataclass
class Add(Expr):
    left:  Expr
    right: Expr

    def evaluate(self, env): return self.left.evaluate(env) + self.right.evaluate(env)
    def pretty(self):        return f"({self.left.pretty()} + {self.right.pretty()})"
    def variables(self):     return self.left.variables() | self.right.variables()


@dataclass
class Mul(Expr):
    left:  Expr
    right: Expr

    def evaluate(self, env): return self.left.evaluate(env) * self.right.evaluate(env)
    def pretty(self):        return f"({self.left.pretty()} * {self.right.pretty()})"
    def variables(self):     return self.left.variables() | self.right.variables()


@dataclass
class Neg(Expr):
    operand: Expr

    def evaluate(self, env): return -self.operand.evaluate(env)
    def pretty(self):        return f"(-{self.operand.pretty()})"
    def variables(self):     return self.operand.variables()


# ════════════════════════════════════════════════════════════════════════
#  ADDING A NEW TYPE — the operand-primal sweet spot
#
#  Drop in one new class below.  evaluate(), pretty(), variables() are all
#  implemented here.  The Expr base class and every other type are untouched.
# ════════════════════════════════════════════════════════════════════════

@dataclass
class Pow(Expr):
    base:     Expr
    exponent: Expr

    def evaluate(self, env): return self.base.evaluate(env) ** self.exponent.evaluate(env)
    def pretty(self):        return f"({self.base.pretty()} ^ {self.exponent.pretty()})"
    def variables(self):     return self.base.variables() | self.exponent.variables()


# ════════════════════════════════════════════════════════════════════════
#  ADDING A NEW OPERATION — the operand-primal pain point
#
#  Goal: add depth() — the maximum nesting level of an expression.
#
#  To do this cleanly you must:
#    1. Add `def depth(self) -> int: ...` to the Expr abstract base class
#    2. Implement it in Num, Var, Add, Mul, Neg, AND Pow
#
#  That is 7 files touched for one new operation (in a real codebase, many more).
#  Below we add it as a standalone function using isinstance matching as a
#  workaround — but this bypasses the type system and doesn't scale.
# ════════════════════════════════════════════════════════════════════════

def depth(expr: Expr) -> int:
    """Standalone workaround — avoids opening every class, but is not OOP."""
    match expr:
        case Num() | Var():                      return 0
        case Add(l, r) | Mul(l, r) | Pow(l, r): return 1 + max(depth(l), depth(r))
        case Neg(operand):                       return 1 + depth(operand)
        case _:                                  raise TypeError(f"Unknown expr: {type(expr)}")


# ── Demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # (x * 2 + (-y))  with x=3, y=1  →  5.0
    expr = Add(Mul(Var("x"), Num(2)), Neg(Var("y")))
    env  = {"x": 3.0, "y": 1.0}

    print("┌── Operand-Primal ─────────────────────────────┐")
    print(f"│  expr      = {expr.pretty():<32} │")
    print(f"│  variables = {str(expr.variables()):<32} │")
    print(f"│  evaluate  = {expr.evaluate(env):<32} │")
    print(f"│  depth     = {depth(expr):<32} │")
    print("└───────────────────────────────────────────────┘")

    # Extension: Pow was added for free — nothing else changed
    expr2 = Pow(Var("x"), Num(3))
    print(f"\n  New type Pow: {expr2.pretty()} = {expr2.evaluate(env)}")
