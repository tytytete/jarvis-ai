"""Безопасный вычислитель арифметики (без eval)."""

import ast
import operator

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def evaluate_math(expression):
    """Возвращает числовой результат или None, если выражение некорректно."""
    expr = expression.replace(",", ".").replace("^", "**").replace("х", "*").replace("×", "*")
    try:
        node = ast.parse(expr, mode="eval")
        result = _eval_node(node.body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, ArithmeticError, TypeError):
        return None
    if isinstance(result, complex):
        return None
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result
