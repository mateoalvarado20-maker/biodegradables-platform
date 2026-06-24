"""El bloque de José en el consolidado diario agrupa los envíos POR SALIDA
(2026-06-23): cada salida muestra lo que José marcó en ella, y los que nunca
salieron quedan en 'Pendientes'."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup_dos_salidas(a, jose, hoy):
    # 3 destinos ad-hoc (no dependen de Contifico)
    r1 = a.add_destino_adhoc(jose, cliente="ACME", direccion="Av 1", tipo="entrega", fecha=hoy)
    r2 = a.add_destino_adhoc(jose, cliente="BETA", direccion="Av 2", tipo="entrega", fecha=hoy)
    a.add_destino_adhoc(jose, cliente="GAMMA", direccion="Av 3", tipo="entrega", fecha=hoy)
    # Salida 1: entrega ACME
    a.start_ruta(jose, hoy)
    a.marcar_entrega(jose, r1["factura_id"], entregado=True, cliente_label="ACME", fecha=hoy)
    a.end_ruta(jose, fecha=hoy)
    # Salida 2: entrega BETA, GAMMA queda pendiente
    a.start_ruta(jose, hoy)
    a.marcar_entrega(jose, r2["factura_id"], entregado=True, cliente_label="BETA", fecha=hoy)
    a.end_ruta(jose, fecha=hoy)


def test_envios_agrupados_por_salida(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    hoy = "2026-06-22"  # lunes — evita rama de ausencia sabática
    _setup_dos_salidas(a, jose, hoy)

    html = aa._jose_consolidated_block_html(hoy)
    assert "📦 Envíos por salida" in html
    assert "Salida #1" in html
    assert "Salida #2" in html
    # GAMMA nunca se entregó → aparece en pendientes
    assert "Pendientes — no salieron en ninguna salida" in html
    assert "GAMMA" in html and "ACME" in html and "BETA" in html


def test_sin_actividad_muestra_placeholder(state_env):
    aa = pytest.importorskip("ask_agent")
    html = aa._jose_consolidated_block_html("2026-06-22")
    assert "no salió a ruta" in html
