"""Named parameters and safe arithmetic expressions for dimensions and feature values.

    [parameters]
    width = "80"
    wall = "2.5"
    depth = "width / 4 - wall"

Expressions are parsed with Python's `ast` and only arithmetic is allowed: numbers,
parameter names, + - * / // % **, unary minus, parentheses and a few maths functions.
Anything else (attributes, calls to other functions, indexing...) is rejected, so a
shared model file cannot run code.
"""

from __future__ import annotations

import ast
import math
import operator

FUNCTIONS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)),
             "cos": lambda d: math.cos(math.radians(d)), "tan": lambda d: math.tan(math.radians(d)),
             "abs": abs, "min": min, "max": max, "round": round}
CONSTANTS = {"pi": math.pi, "inch": 25.4}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}


class ParameterError(ValueError):
    pass


def names_in(expr: str) -> set[str]:
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError as e:
        raise ParameterError(f"Not a valid expression: {expr!r}") from e
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - set(FUNCTIONS) - set(CONSTANTS)


def evaluate(expr, values: dict[str, float]) -> float:
    """Value of an expression (or a plain number) given parameter values."""
    if isinstance(expr, (int, float)):
        return float(expr)
    try:
        tree = ast.parse(str(expr).strip(), mode="eval")
    except SyntaxError as e:
        raise ParameterError(f"Not a valid expression: {expr!r}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in values:
                return float(values[node.id])
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            raise ParameterError(f"Unknown parameter {node.id!r} in {expr!r}")
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS \
                and not node.keywords:
            return float(FUNCTIONS[node.func.id](*[ev(a) for a in node.args]))
        raise ParameterError(f"Only arithmetic is allowed in expressions: {expr!r}")

    try:
        return ev(tree)
    except ZeroDivisionError as e:
        raise ParameterError(f"Division by zero in {expr!r}") from e


def resolve(parameters: dict[str, str]) -> dict[str, float]:
    """Values of all parameters, each evaluated after the ones it uses; cycles are errors."""
    values: dict[str, float] = {}
    visiting: list[str] = []

    def visit(name):
        if name in values:
            return
        if name in visiting:
            raise ParameterError("Parameters depend on each other in a loop: " + " → ".join(visiting + [name]))
        if name not in parameters:
            raise ParameterError(f"Unknown parameter {name!r}")
        visiting.append(name)
        for dep in names_in(parameters[name]):
            visit(dep)
        visiting.pop()
        values[name] = evaluate(parameters[name], values)

    for name in parameters:
        if not name.isidentifier() or name in FUNCTIONS or name in CONSTANTS:
            raise ParameterError(f"{name!r} cannot be used as a parameter name")
        visit(name)
    return values


def is_number(text) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False
