"""Anti-fuga de tenant en textos de cara al usuario.

Incidente 2026-07-06: el Data Bot de la instancia DEMO (Andex) saludaba
"Soy el Data Bot de Biodegradables Ecuador" — la bienvenida estaba
hardcodeada en teams_bot.py y se escapó del de-hardcode F2.4.

Regla: en los módulos que producen texto visible al usuario (bienvenidas,
cards, errores, tool descriptions, system prompts), NINGÚN string literal
que NO sea docstring puede nombrar a la empresa del tenant #1 ni usar a su
gente como contacto de soporte. Los textos deben derivarse de core_config
(COMPANY_NAME, PEOPLE, horarios) o ser neutrales ("el administrador").

Los docstrings quedan exentos: son para desarrolladores, no para usuarios.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Módulos cuyos strings llegan al usuario final (chat, cards, correos).
USER_FACING = ["teams_bot.py", "bot_cards.py", "team_reports.py",
               "admin_api.py", "ask_agent.py", "reply_agent.py"]

# Nunca en un string no-docstring de esos módulos:
FORBIDDEN = [
    "Biodegradables",            # nombre del tenant #1
    "biodegradablesecuador.com",  # su dominio
    "avisale a Mateo",           # contacto de soporte hardcodeado
    "Pedile a Mateo",
    "pedile a Mateo",
    "Mateo ya está al tanto",
    "Mateo está al tanto",
]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids de los Constant que son docstrings (módulo/clase/función)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


@pytest.mark.parametrize("fname", USER_FACING)
def test_sin_fugas_de_tenant_en_strings(fname):
    tree = ast.parse((ROOT / fname).read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    fugas = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docs):
            for pat in FORBIDDEN:
                if pat in node.value:
                    fugas.append(
                        f"{fname}:{node.lineno}: {pat!r} en {node.value[:70]!r}"
                    )
    assert not fugas, (
        "Texto del tenant #1 hardcodeado en módulo user-facing (usar "
        "core_config.COMPANY_NAME / PEOPLE o redacción neutral):\n"
        + "\n".join(fugas)
    )
