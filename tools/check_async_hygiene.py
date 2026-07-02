"""Gate de CI (F0 VER-IA, 2026-07-02): prohíbe red SÍNCRONA en corutinas.

Contexto: dos incidentes reales (cobranzas 2026-06-23, weekly summaries
detectado en la auditoría 2026-07-02) por llamar funciones síncronas de red
(graph_mail.send, contifico_client.*) directamente dentro de una función
async. El event loop se bloquea >120s y gunicorn mata el worker.

Regla: dentro de un `async def`, toda llamada a una función de la blocklist
debe ir vía `asyncio.to_thread(...)` (como referencia o dentro de un lambda),
nunca invocada directo. Las funciones sync anidadas (`def` interno) y los
lambdas se excluyen: no corren en el event loop al definirse.

Uso: python tools/check_async_hygiene.py teams_bot.py [otros.py...]
Exit 0 si limpio; exit 1 listando violaciones archivo:línea.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Módulos cuyos atributos llamados son SIEMPRE red/disco síncrono bloqueante.
BLOCKING_MODULES = {
    "graph_mail", "_gm",
    "contifico_client",
    "hubspot_client",
    "monthly_recap",
    "news_brief",
    "graph_calendar_app",
    "credito_excel",
    "forecasting",
}
# Funciones sueltas (importadas por nombre) que hacen red síncrona.
BLOCKING_NAMES = {
    "_send_daily_summary_email",
    "_send_weekly_summary_email",
    "_send_consolidated_daily_summary",
    "_send_cierre_caja_email",
    "_send_job_failure_alert",
    "_run_daily_report_morning",
    "_run_daily_report_test",
    "_run_logistics_morning",
    "send_team_workload_summary",
}
# Excepciones puntuales: atributos que NO bloquean (constantes, helpers puros).
SAFE_ATTRS = {
    ("news_brief", "is_brief_fresh"),
    ("news_brief", "load_brief"),
}


def _dotted(node: ast.expr) -> str | None:
    """'graph_mail.send' para Attribute(Name) — None si es más complejo."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_blocking(call: ast.Call) -> str | None:
    name = _dotted(call.func)
    if not name:
        return None
    if "." in name:
        mod, attr = name.split(".", 1)
        if mod in BLOCKING_MODULES and (mod, attr) not in SAFE_ATTRS:
            return name
        return None
    if name in BLOCKING_NAMES:
        return name
    return None


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.in_async = False
        self.violations: list[tuple[int, str]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        prev, self.in_async = self.in_async, True
        for child in node.body:
            self.visit(child)
        self.in_async = prev

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Un `def` sync anidado no corre en el event loop al definirse
        # (lo ejecuta un thread/executor o se pasa como callback).
        prev, self.in_async = self.in_async, False
        for child in node.body:
            self.visit(child)
        self.in_async = prev

    def visit_Lambda(self, node: ast.Lambda) -> None:
        prev, self.in_async = self.in_async, False
        self.visit(node.body)
        self.in_async = prev

    def visit_Call(self, node: ast.Call) -> None:
        if self.in_async:
            hit = _is_blocking(node)
            if hit:
                self.violations.append((node.lineno, hit))
        self.generic_visit(node)


def check_file(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    checker = _Checker()
    checker.visit(tree)
    return checker.violations


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv] or [Path("teams_bot.py")]
    failed = False
    for target in targets:
        for lineno, name in check_file(target):
            failed = True
            print(
                f"{target}:{lineno}: llamada síncrona de red `{name}` dentro "
                f"de async def — envolver en asyncio.to_thread(...)"
            )
    if failed:
        return 1
    print(f"OK: sin red síncrona en corutinas ({', '.join(map(str, targets))}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
