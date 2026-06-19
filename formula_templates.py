"""
Конструктор LaTeX-формул по шаблонам.

Идея: вместо того чтобы пользователь писал \\frac{a}{b} руками,
он выбирает шаблон («Дробь»), бот спрашивает по очереди значения
полей (числитель, знаменатель) и склеивает LaTeX.

Шаблон — это:
  • name    — что показывать на кнопке;
  • fields  — список (key, prompt) — порядок и подсказки для FSM-шагов;
  • render  — функция dict[str,str] -> str, собирает финальный LaTeX.

Чтобы добавить новый шаблон — просто допиши строку в TEMPLATES.
"""
from typing import Callable, NamedTuple


class Template(NamedTuple):
    name: str
    fields: list[tuple[str, str]]
    render: Callable[[dict], str]


# Хелперы для рендера: оборачиваем поле в {…}, чтобы LaTeX корректно
# группировал многосимвольные выражения (n+1, не только n).
def _g(s: str) -> str:
    s = s.strip()
    return "{" + s + "}"


def _system_render(values: dict) -> str:
    """Система уравнений: каждая строка — отдельное уравнение, & не используем."""
    lines = [l.strip() for l in values["lines"].splitlines() if l.strip()]
    body = " \\\\ ".join(lines)
    return f"\\begin{{cases}} {body} \\end{{cases}}"


def _matrix_render(values: dict) -> str:
    """Матрица: строки через перенос, ячейки через ';'."""
    rows = [r.strip() for r in values["rows"].splitlines() if r.strip()]
    parsed = [[c.strip() for c in r.split(";")] for r in rows]
    body = " \\\\ ".join(" & ".join(row) for row in parsed)
    brackets = values.get("brackets", "p")  # p — круглые, b — квадратные, v — модуль
    return f"\\begin{{{brackets}matrix}} {body} \\end{{{brackets}matrix}}"


TEMPLATES: dict[str, Template] = {
    "frac": Template(
        name="Дробь  a/b",
        fields=[("num", "Числитель"), ("den", "Знаменатель")],
        render=lambda v: f"\\frac{_g(v['num'])}{_g(v['den'])}",
    ),
    "pow": Template(
        name="Степень  aᵇ",
        fields=[("base", "Основание"), ("exp", "Показатель")],
        render=lambda v: f"{_g(v['base'])}^{_g(v['exp'])}",
    ),
    "sub": Template(
        name="Индекс  aᵢ",
        fields=[("base", "База"), ("idx", "Индекс")],
        render=lambda v: f"{_g(v['base'])}_{_g(v['idx'])}",
    ),
    "sqrt": Template(
        name="Корень  √a",
        fields=[("radicand", "Подкоренное выражение")],
        render=lambda v: f"\\sqrt{_g(v['radicand'])}",
    ),
    "nroot": Template(
        name="Корень n-й  ⁿ√a",
        fields=[("n", "Степень корня (n)"), ("radicand", "Подкоренное выражение")],
        render=lambda v: f"\\sqrt[{v['n'].strip()}]{_g(v['radicand'])}",
    ),
    "sum": Template(
        name="Сумма  Σ",
        fields=[
            ("idx", "Индекс (например i)"),
            ("lo", "Нижний предел (например 1)"),
            ("hi", "Верхний предел (например n)"),
            ("expr", "Выражение под Σ"),
        ],
        render=lambda v: (
            f"\\sum_{{{v['idx'].strip()}={v['lo'].strip()}}}"
            f"^{_g(v['hi'])} {v['expr'].strip()}"
        ),
    ),
    "prod": Template(
        name="Произведение  ∏",
        fields=[
            ("idx", "Индекс"),
            ("lo", "Нижний предел"),
            ("hi", "Верхний предел"),
            ("expr", "Выражение"),
        ],
        render=lambda v: (
            f"\\prod_{{{v['idx'].strip()}={v['lo'].strip()}}}"
            f"^{_g(v['hi'])} {v['expr'].strip()}"
        ),
    ),
    "int": Template(
        name="Интеграл  ∫",
        fields=[
            ("lo", "Нижний предел"),
            ("hi", "Верхний предел"),
            ("expr", "Подынтегральная функция"),
            ("dx", "Переменная (например x)"),
        ],
        render=lambda v: (
            f"\\int_{_g(v['lo'])}^{_g(v['hi'])} {v['expr'].strip()}"
            f"\\,d{v['dx'].strip()}"
        ),
    ),
    "lim": Template(
        name="Предел  lim",
        fields=[
            ("var", "Переменная (например x)"),
            ("to", "К чему стремится"),
            ("expr", "Выражение"),
        ],
        render=lambda v: (
            f"\\lim_{{{v['var'].strip()} \\to {v['to'].strip()}}} {v['expr'].strip()}"
        ),
    ),
    "system": Template(
        name="Система  {",
        fields=[("lines", "Уравнения — каждое с новой строки")],
        render=_system_render,
    ),
    "matrix": Template(
        name="Матрица  (·)",
        fields=[("rows", "Строки — каждая с новой, ячейки через «;»")],
        render=_matrix_render,
    ),
    "eq": Template(
        name="Уравнение  a = b",
        fields=[("lhs", "Левая часть"), ("rhs", "Правая часть")],
        render=lambda v: f"{v['lhs'].strip()} = {v['rhs'].strip()}",
    ),
}
