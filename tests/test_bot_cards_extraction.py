"""Tests F4.4a (VER-IA 2026-07-04): extracción de bot_cards y tenant_roles.

Contrato:
- teams_bot re-exporta los builders y los aliases de identidad (compat).
- Dependencias UNIDIRECCIONALES: teams_bot → bot_cards → tenant_roles →
  core_config. Ni bot_cards ni tenant_roles importan teams_bot.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUILDERS = [
    "_build_checkin_card",
    "_build_done_activities_card",
    "_build_task_confirmation_card",
    "_build_confirmacion_cierre_card",
    "_build_jose_ruta_card",
    "_build_jose_ruta_card_closed",
    "_build_jose_asistencia_card",
    "_build_apertura_caja_card",
]
ROLES = [
    "INFO_EMAIL", "QUITO_EMAIL", "JOSE_EMAIL", "CIERRE_CAJA_USERS",
    "SUCURSAL_POR_USER", "ROUTE_USERS", "VALIDADOR_CIERRE_POR_CIUDAD",
    "SUPERVISORS_ONLY", "WORKLOAD_SUPERVISORS", "JOSE_SUMMARY_TO",
]


def _imports_de(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_reexport_identico_objeto_a_objeto():
    import bot_cards
    import teams_bot
    import tenant_roles
    for name in BUILDERS:
        assert getattr(teams_bot, name) is getattr(bot_cards, name), name
    for name in ROLES:
        assert getattr(teams_bot, name) is getattr(tenant_roles, name), name


def test_dependencias_unidireccionales():
    assert "teams_bot" not in _imports_de(ROOT / "bot_cards.py")
    assert "teams_bot" not in _imports_de(ROOT / "tenant_roles.py")
    assert "bot_cards" not in _imports_de(ROOT / "tenant_roles.py")
    # tenant_roles es la capa más baja: solo core_config
    assert _imports_de(ROOT / "tenant_roles.py") <= {"core_config", "__future__"}


def test_teams_bot_va_bajando_de_tamano():
    """Umbral post-F4.4a: los builders (~1.700 líneas) ya no viven aquí.
    Si vuelve a crecer sobre 5.600, algo se está agregando en el módulo
    equivocado (cards→bot_cards, reportes→team_reports, roles→tenant_roles)."""
    lineas = len((ROOT / "teams_bot.py").read_text(encoding="utf-8").splitlines())
    assert lineas < 5600, f"teams_bot.py tiene {lineas} líneas"
