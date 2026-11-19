"""Small, array-aware mathematical expressions, without executing Python code."""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from functools import lru_cache
from typing import Any, cast

import numpy as np

MODE_EXPRESSIONS = {
    "identity": "1",
    "gaussian_linear": "t * exp(-0.5 * (2*pi*f*t)**2)",
    "gaussian_linear_cosine": "t * cos(2*pi*f*t) * exp(-0.5 * (2*pi*f*t)**2)",
    "lab_gaussian_linear_cosine": "t * cos(f*t) * exp(-0.5 * (2*pi*f*t)**2)",
}

_FUNCTIONS = {name: getattr(np, name) for name in (
    "exp", "sin", "cos", "tan", "tanh", "sqrt", "log", "log10", "abs",
    "where", "minimum", "maximum", "clip",
)}
_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
           ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
           ast.BitAnd: operator.and_, ast.BitOr: operator.or_}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert,
}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
            ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
            ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "np"):
        return node.attr
    return ""


@lru_cache(maxsize=128)
def parse_mode_expression(expression: str) -> ast.AST:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 2048:
        raise ValueError("Mode function must be a mathematical expression of 1-2048 characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("Invalid mode function: use Python mathematical notation") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise ValueError("Mode function is too complex")

    def check(node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = cast(int | float, node.value)
            if abs(value) > 1e100 or not np.isfinite(float(value)):
                raise ValueError("Mode function constants must be finite and at most 1e100")
        elif _name(node) in {"t", "T", "f", "pi"}:
            # Only pi may use the np. prefix.
            if isinstance(node, ast.Attribute) and node.attr != "pi":
                raise ValueError("Mode function allows only np.pi and supported np functions")
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            check(node.left)
            check(node.right)
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            check(node.operand)
        elif (isinstance(node, ast.Compare) and len(node.ops) == 1
              and type(node.ops[0]) in _COMPARE):
            check(node.left)
            check(node.comparators[0])
        elif (isinstance(node, ast.Call) and _name(node.func) in _FUNCTIONS
              and not node.keywords and 1 <= len(node.args) <= 3):
            for argument in node.args:
                check(argument)
        else:
            raise ValueError("Invalid mode function: use t, T, f, pi, arithmetic and "
                             "supported math functions (exp, sin, cos, sqrt, abs, where, …)")

    check(tree.body)
    return tree.body


def evaluate_mode_expression(
    expression: str, time: np.ndarray, width: float, frequency: float,
) -> np.ndarray:
    tree = parse_mode_expression(expression)
    variables = {"t": time, "T": np.float64(width), "f": np.float64(frequency), "pi": np.pi}

    def calculate(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return np.float64(cast(int | float, node.value))
        if isinstance(node, (ast.Name, ast.Attribute)):
            return variables[_name(node)]
        if isinstance(node, ast.BinOp):
            return _BINARY[type(node.op)](calculate(node.left), calculate(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](calculate(node.operand))
        if isinstance(node, ast.Compare):
            return _COMPARE[type(node.ops[0])](calculate(node.left), calculate(node.comparators[0]))
        if isinstance(node, ast.Call):
            return _FUNCTIONS[_name(node.func)](*(calculate(arg) for arg in node.args))
        raise ValueError("Invalid mode function")

    try:
        with np.errstate(all="ignore"):
            result = np.broadcast_to(np.asarray(calculate(tree), dtype=np.float64), time.shape)
    except (ValueError, TypeError, OverflowError, ArithmeticError) as exc:
        raise ValueError(f"Invalid mode function: {exc}") from exc
    if not np.all(np.isfinite(result)):
        raise ValueError("Mode function must produce finite values throughout the temporal mode")
    return result
