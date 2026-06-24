"""El bloque de José en el consolidado diario debe mostrar su asistencia
(antes no salía aunque la marcara). Fix 2026-06-23."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_bloque_jose_muestra_asistencia_estandar(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    # Un día laborable (lunes) para no caer en la rama de ausencia rotativa.
    hoy = "2026-06-22"  # lunes
    a.set_day_schedule(jose, hoy, estandar=True)
    html = aa._jose_consolidated_block_html(hoy)
    assert "⏰ Asistencia" in html
    assert "estándar" in html


def test_bloque_jose_muestra_asistencia_no_estandar(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    hoy = "2026-06-22"
    a.set_day_schedule(jose, hoy, estandar=False, desde="9:30", hasta="11:00",
                       razon="reunión médica")
    html = aa._jose_consolidated_block_html(hoy)
    assert "⏰ Asistencia" in html
    assert "9:30" in html and "11:00" in html
    assert "reunión médica" in html


def test_bloque_jose_sin_asistencia_dice_sin_reportar(state_env):
    aa = pytest.importorskip("ask_agent")
    html = aa._jose_consolidated_block_html("2026-06-22")
    assert "⏰ Asistencia" in html
    assert "Sin reportar" in html
