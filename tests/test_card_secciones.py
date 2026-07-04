"""Secciones del check-in card (2026-07-04): actividades diarias / tareas
puntuales (tipo unica) / cartera (cobranzas) / proyectos semanales."""
from __future__ import annotations

import datetime as dt
import importlib
import json

import pytest


@pytest.fixture()
def cards(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import bot_cards
    return importlib.reload(bot_cards)


def test_card_separa_secciones(cards, monkeypatch):
    import activity_state
    user = "info@biodegradablesecuador.com"
    # Día hábil (miércoles) para que las cobranzas no se filtren por sábado
    monkeypatch.setattr(activity_state, "_today", lambda: dt.date(2026, 7, 8))

    activity_state.add_adhoc(
        "cobranza-acme", "📞 Cobranza: ACME SA — $500 (10d atraso)",
        user_email=user, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    activity_state.add_adhoc(
        "diaria-inventario", "Revisar inventario",
        user_email=user, tipo="diaria", meta=1, unidad="revisión",
    )
    activity_state.add_adhoc(
        "unica-manual", "Armar manual de bodega",
        user_email=user, tipo="unica",
    )
    activity_state.add_adhoc(
        "semanal-orden", "Ordenar percha",
        user_email=user, tipo="semanal",
    )

    card = cards._build_checkin_card(user)
    body = card.attachments[0].content["body"]
    txt = json.dumps(card.attachments[0].content, ensure_ascii=False)

    # Las 4 secciones existen con sus títulos
    titulos = [
        it.get("text", "") for cont in body if cont.get("type") == "Container"
        for it in cont.get("items", []) if it.get("type") == "TextBlock"
    ]
    assert any("📅 Actividades diarias" in t for t in titulos)
    assert any("🗂️ Tareas puntuales" in t for t in titulos)
    assert any("📞 Cartera" in t for t in titulos)
    assert any("📌 Proyectos semanales" in t for t in titulos)

    # Cada activity cae en la sección correcta: buscamos el Container que
    # contiene cada título y verificamos qué inputs tiene.
    def _container_con(titulo: str) -> str:
        for cont in body:
            if cont.get("type") != "Container":
                continue
            blob = json.dumps(cont, ensure_ascii=False)
            if titulo in blob:
                return blob
        return ""

    c_diarias = _container_con("📅 Actividades diarias")
    c_unicas = _container_con("🗂️ Tareas puntuales")
    c_cartera = _container_con("📞 Cartera")
    c_sem = _container_con("📌 Proyectos semanales")

    assert "estado__diaria-inventario" in c_diarias
    assert "cobranza-acme" not in c_diarias        # cobranza YA NO va en diarias
    assert "estado__cobranza-acme" in c_cartera    # va en cartera
    assert "avance__unica-manual" in c_unicas
    assert "avance__unica-manual" not in c_sem     # unica ya no va en semanales
    assert "avance__semanal-orden" in c_sem

    # El UI de cobranza mantiene Contactado/No contactado
    assert "Contactado" in c_cartera
    assert txt  # sanity
