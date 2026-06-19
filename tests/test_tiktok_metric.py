"""Tests de la métrica de videos TikTok de Mateo en el resumen diario.

Pedido por gerencia (2026-06-19): el bloque de Mateo en el consolidado debe
mostrar la meta semanal de videos TikTok (5) y cuántos lleva completados.
El conteo sale de sumar las marcas diarias de la activity `video-tiktok`.
"""
from __future__ import annotations

import importlib
from datetime import date


def _reload_ask_agent():
    import ask_agent
    return importlib.reload(ask_agent)


# Miércoles dentro de una semana laboral normal (evita ramas de sábado).
_WED = date(2026, 6, 17)
_DIAS = ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19"]


def _mateo(a):
    return a.DEFAULT_USER  # Mateo — único user con el template default (video-tiktok)


def test_meta_semanal_se_copia_del_template(state_env):
    """init_week debe copiar meta_semanal del template a la activity de la semana."""
    a = state_env.activity_state
    week = a.init_week(_mateo(a), wk=a.week_key(_WED))
    tt = week["activities"]["video-tiktok"]
    assert tt.get("meta_semanal") == 5


def test_tiktok_videos_parcial(state_env):
    a = state_env.activity_state
    ask = _reload_ask_agent()
    mateo = _mateo(a)
    a.init_week(mateo, wk=a.week_key(_WED))
    for f in _DIAS[:3]:  # 3 videos
        a.mark_daily("video-tiktok", 1, user_email=mateo, fecha=f)

    html = ask._collaborator_block_html_v2(mateo, target_date=_WED)
    assert "Meta TikTok:" in html
    assert "5 videos" in html
    assert "3/5 completados" in html
    # parcial → naranja, sin check verde
    assert "✅</span>" not in html.split("3/5 completados")[1][:5]


def test_tiktok_videos_completo(state_env):
    a = state_env.activity_state
    ask = _reload_ask_agent()
    mateo = _mateo(a)
    a.init_week(mateo, wk=a.week_key(_WED))
    for f in _DIAS:  # 5 videos = meta cumplida
        a.mark_daily("video-tiktok", 1, user_email=mateo, fecha=f)

    html = ask._collaborator_block_html_v2(mateo, target_date=_WED)
    assert "5/5 completados ✅" in html
