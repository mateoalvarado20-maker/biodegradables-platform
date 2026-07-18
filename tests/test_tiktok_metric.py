"""Tests de la métrica de videos TikTok de Mateo en el resumen diario.

Pedido por gerencia (2026-06-19): el bloque de Mateo en el consolidado debe
mostrar la meta semanal de videos TikTok y cuántos lleva completados. El conteo
sale de sumar las marcas diarias de la actividad de videos TikTok.

2026-06-24: se borró la actividad vieja `video-tiktok` (meta 1) del template;
la métrica se repuntó a `tiktok-videos-diarios` (meta 6/día → 30/semana).
"""
from __future__ import annotations

import importlib
from datetime import date

TT_AID = "tiktok-videos-diarios"
TT_NOMBRE = "Videos diarios TikTok BIOdegradables"


def _reload_ask_agent():
    import ask_agent
    return importlib.reload(ask_agent)


# Miércoles dentro de una semana laboral normal (evita ramas de sábado).
_WED = date(2026, 6, 17)
_DIAS = ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19"]


def _mateo(a):
    return a.DEFAULT_USER


def _add_tiktok(a, mateo, wk):
    a.init_week(mateo, wk=wk)
    # 2026-07-17: el template default vuelve a sembrar tiktok-videos-diarios
    # (meta 1) — estos tests necesitan su propia versión con meta=6, así que
    # reemplazan la sembrada.
    a.remove_activity(TT_AID, user_email=mateo, wk=wk)
    a.add_adhoc(TT_AID, TT_NOMBRE, user_email=mateo, tipo="diaria", meta=6, wk=wk)


def test_meta_semanal_default_diaria_por_cinco(state_env):
    """Sin meta_semanal explícita, la meta = meta diaria (6) × 5 días = 30."""
    a = state_env.activity_state
    ask = _reload_ask_agent()
    mateo = _mateo(a)
    wk = a.week_key(_WED)
    _add_tiktok(a, mateo, wk)
    html = ask._collaborator_block_html_v2(mateo, target_date=_WED)
    assert "Meta TikTok:" in html
    assert "30 videos" in html


def test_tiktok_videos_parcial(state_env):
    a = state_env.activity_state
    ask = _reload_ask_agent()
    mateo = _mateo(a)
    wk = a.week_key(_WED)
    _add_tiktok(a, mateo, wk)
    for f in _DIAS[:3]:  # 3 días × 6 = 18 videos
        a.mark_daily(TT_AID, 6, user_email=mateo, fecha=f, wk=wk)

    html = ask._collaborator_block_html_v2(mateo, target_date=_WED)
    assert "18/30 completados" in html
    # parcial → naranja, sin check verde
    assert "✅</span>" not in html.split("18/30 completados")[1][:5]


def test_tiktok_videos_completo(state_env):
    a = state_env.activity_state
    ask = _reload_ask_agent()
    mateo = _mateo(a)
    wk = a.week_key(_WED)
    _add_tiktok(a, mateo, wk)
    for f in _DIAS:  # 5 días × 6 = 30 = meta cumplida
        a.mark_daily(TT_AID, 6, user_email=mateo, fecha=f, wk=wk)

    html = ask._collaborator_block_html_v2(mateo, target_date=_WED)
    assert "30/30 completados ✅" in html
