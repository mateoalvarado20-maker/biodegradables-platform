"""Tests F4.2 (VER-IA 2026-07-03): extracción de team_reports desde ask_agent.

Contrato de la extracción:
- ask_agent re-exporta los nombres del bloque movido (compat: teams_bot y
  código existente siguen importando desde ask_agent sin cambios).
- La dependencia es UNIDIRECCIONAL: ask_agent → team_reports. team_reports
  jamás importa ask_agent (evita ciclos y mantiene la capa de reportes
  independiente del agente).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Los nombres que teams_bot importa desde ask_agent (verificado por grep al
# momento de la extracción) — el re-export debe cubrirlos SIEMPRE.
NOMBRES_CONSUMIDOS_POR_TEAMS_BOT = [
    "_send_daily_summary_email",
    "_send_weekly_summary_email",
    "_send_apertura_caja_email",
    "_send_confirmacion_cierre_email",
    "_send_consolidated_daily_summary",
    "send_saturday_recap_summary",
    "send_team_workload_summary",
    "_workload_text_for_chat",
    "SUPERVISORS_ONLY_EMAILS",
    "COLLABORATORS",
]


def test_ask_agent_reexporta_el_mismo_objeto():
    import ask_agent
    import team_reports
    for name in NOMBRES_CONSUMIDOS_POR_TEAMS_BOT:
        assert hasattr(team_reports, name), f"team_reports perdió {name}"
        assert getattr(ask_agent, name) is getattr(team_reports, name), (
            f"ask_agent.{name} no es el MISMO objeto que team_reports.{name} "
            "(el re-export se rompió o alguien redefinió el nombre)"
        )


def test_team_reports_no_importa_ask_agent():
    """Dependencia unidireccional — un import de ask_agent en team_reports
    sería un ciclo (ask_agent importa team_reports a nivel de módulo)."""
    src = (ROOT / "team_reports.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        else:
            continue
        assert "ask_agent" not in mods, (
            f"team_reports.py:{node.lineno} importa ask_agent — ciclo prohibido"
        )


def test_ask_agent_quedo_en_tamano_de_agente():
    """El agente conversacional ya no carga con ~2.500 líneas de HTML de
    correos. Umbral holgado: si vuelve a crecer sobre 2.500 líneas, algo se
    está agregando en el módulo equivocado."""
    lineas = len((ROOT / "ask_agent.py").read_text(encoding="utf-8").splitlines())
    assert lineas < 2500, (
        f"ask_agent.py tiene {lineas} líneas — las funciones de reporte van "
        "en team_reports.py, no aquí"
    )
