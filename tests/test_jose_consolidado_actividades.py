"""El bloque de José en el consolidado 6:30 muestra sus actividades delegadas
(2026-06-25), para que Daniel vea las de TODOS los colaboradores."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_consolidado_jose_muestra_actividades(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    hoy = "2026-06-22"  # lunes (evita ausencia rotativa sabática)
    a.add_adhoc("entregar-volantes", "Entregar volantes en ruta",
                user_email=jose, tipo="diaria", meta=20, wk=a.week_key())
    a.add_adhoc("ordenar-bodega", "Ordenar bodega GYE",
                user_email=jose, tipo="semanal", wk=a.week_key())
    a.mark_daily("entregar-volantes", 20, user_email=jose, fecha=hoy)
    a.set_weekly_progress("ordenar-bodega", 40, user_email=jose)

    html = aa._jose_consolidated_block_html(hoy)
    assert "📋 Actividades asignadas" in html
    assert "Entregar volantes en ruta" in html
    assert "Ordenar bodega GYE" in html


def test_consolidado_jose_sin_actividades_no_muestra_seccion(state_env):
    aa = pytest.importorskip("ask_agent")
    html = aa._jose_consolidated_block_html("2026-06-22")
    assert "📋 Actividades asignadas" not in html
