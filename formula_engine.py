"""Evaluation of formula graphs, independent of Tkinter and spectrum storage."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Mapping

UNARY_OPERATIONS = frozenset({"abs", "√", "ln", "log₁₀", "exp", "round", "−x"})
OPERATIONS = (
    "+",
    "−",
    "×",
    "÷",
    "^",
    "%",
    "abs",
    "√",
    "ln",
    "log₁₀",
    "exp",
    "round",
    "−x",
    "min",
    "max",
)


@dataclass(frozen=True)
class FormulaResult:
    value: float | None
    status: str = "ready"
    message: str = "Готово"


def format_value(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"


class FormulaEngine:
    """One evaluation context with memoized nodes and explicit diagnostics.

    The provider returns a physical integral area, or None for a missing area.
    Missing areas and zero division preserve NOX's zero-result convention,
    but warnings propagate to the output instead of looking like valid data.
    """

    def __init__(self, blocks: Mapping[int, dict], area_provider: Callable[[dict], float | None]):
        self.blocks = blocks
        self.area_provider = area_provider
        self._cache: dict[int, FormulaResult] = {}
        self._visiting: set[int] = set()

    def evaluate(self, block_id: object) -> FormulaResult:
        if not isinstance(block_id, int) or block_id not in self.blocks:
            return FormulaResult(None, "error", "Вход не подключён")
        if block_id in self._cache:
            return self._cache[block_id]
        if block_id in self._visiting:
            return FormulaResult(None, "error", "Циклическая формула")
        self._visiting.add(block_id)
        try:
            result = self._evaluate(self.blocks[block_id])
        except (ValueError, TypeError, OverflowError, ZeroDivisionError, ArithmeticError):
            result = FormulaResult(None, "error", "Операция не определена для этих значений")
        finally:
            self._visiting.remove(block_id)
        self._cache[block_id] = result
        return result

    def _evaluate(self, block: dict) -> FormulaResult:
        kind = block.get("kind")
        if kind == "area":
            value = self.area_provider(block)
            if value is None:
                number = block.get("area_number", "?")
                return FormulaResult(0.0, "warning", f"I{number} отсутствует: используется 0")
            return self._finite(float(value))
        if kind == "constant":
            return self._finite(float(block.get("value", 0)))
        if kind == "output":
            return self.evaluate(block.get("input"))
        if kind != "operation":
            return FormulaResult(None, "error", "Неизвестный тип блока")
        op = block.get("operation")
        if op not in OPERATIONS:
            return FormulaResult(None, "error", "Неизвестная операция")
        count = 1 if op in UNARY_OPERATIONS else 2
        inputs = block.get("inputs", [])
        if len(inputs) != count:
            return FormulaResult(None, "error", "Неверное количество входов")
        results = [self.evaluate(bid) for bid in inputs]
        invalid = next((r for r in results if r.value is None), None)
        if invalid is not None:
            return invalid
        a = results[0].value
        b = results[-1].value
        if op in {"÷", "%"} and b == 0:
            return FormulaResult(0.0, "warning", "Делитель равен 0: результат принят за 0")
        if op == "√" and a < 0:
            return FormulaResult(None, "error", "Корень из отрицательного значения")
        if op in {"ln", "log₁₀"} and a <= 0:
            return FormulaResult(None, "error", "Логарифм требует положительного значения")
        functions = {
            "+": lambda: a + b,
            "−": lambda: a - b,
            "×": lambda: a * b,
            "÷": lambda: a / b,
            "^": lambda: a**b,
            "%": lambda: a % b,
            "abs": lambda: abs(a),
            "√": lambda: math.sqrt(a),
            "ln": lambda: math.log(a),
            "log₁₀": lambda: math.log10(a),
            "exp": lambda: math.exp(a),
            "round": lambda: round(a),
            "−x": lambda: -a,
            "min": lambda: min(a, b),
            "max": lambda: max(a, b),
        }
        result = self._finite(float(functions[op]()))
        warning = next((r for r in results if r.status != "ready"), None)
        if result.status == "ready" and warning is not None:
            return FormulaResult(result.value, warning.status, warning.message)
        return result

    @staticmethod
    def _finite(value: float) -> FormulaResult:
        return (
            FormulaResult(value)
            if math.isfinite(value)
            else FormulaResult(None, "error", "Результат не является конечным числом")
        )

    def outputs(self) -> list[tuple[int, str, FormulaResult]]:
        return [
            (
                bid,
                str(b.get("name") or ("Вывод" if n == 0 else f"Вывод {n + 1}")),
                self.evaluate(bid),
            )
            for n, (bid, b) in enumerate(
                (item for item in self.blocks.items() if item[1].get("kind") == "output")
            )
        ]
