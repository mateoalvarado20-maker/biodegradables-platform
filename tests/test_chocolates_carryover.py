"""Chocolates: carry-over semanal + contador visible con cualquier dato de
stock (2026-07-17). Bug: cada lunes la semana arrancaba en 0 y el card volvía
a pedir stock inicial; y si el colaborador cargaba su stock como 'recarga'
(inicial=0), el contador desaparecía. También: la actividad TikTok volvió al
template (la sección del card se gatea por ese aid)."""
from __future__ import annotations

import importlib
import json

import pytest

INFO = "info@biodegradablesecuador.com"


@pytest.fixture()
def cards(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import bot_cards
    return importlib.reload(bot_cards)


def test_stock_se_arrastra_entre_semanas(cards):
    import activity_state as a
    a.set_chocolates_stock_inicial(INFO, 10, wk="2026-W10")
    a.add_chocolates_entrega(INFO, "2026-03-04", 3, wk="2026-W10")  # quedan 7

    # Semana siguiente SIN registro → bloque virtual con el stock arrastrado
    choco = a.get_chocolates_semana(INFO, wk="2026-W11")
    assert choco is not None
    assert choco["stock_actual"] == 7
    assert choco.get("carryover") is True

    # Una entrega en W11 materializa el entry arrancando de 7 (no de 0)
    a.add_chocolates_entrega(INFO, "2026-03-10", 2, wk="2026-W11")
    choco = a.get_chocolates_semana(INFO, wk="2026-W11")
    assert choco["stock_inicial"] == 7
    assert choco["stock_actual"] == 5
    assert not choco.get("carryover")

    # Y encadena: W13 (salta W12 vacía) sigue viendo 5
    choco13 = a.get_chocolates_semana(INFO, wk="2026-W13")
    assert choco13 is not None and choco13["stock_actual"] == 5


def test_sin_datos_historicos_devuelve_none(cards):
    import activity_state as a
    assert a.get_chocolates_semana("quito@biodegradablesecuador.com",
                                   wk="2026-W10") is None


def test_card_muestra_contador_con_solo_recarga(cards):
    """Caso info@ real: stock_inicial=0 pero recarga de 8 → el card debe
    mostrar el CONTADOR (8), no volver a pedir stock inicial."""
    import activity_state as a
    hoy = a._today().isoformat()
    a.add_chocolates_recarga(INFO, hoy, 8)  # entry con inicial=0 (sin historia)

    card = cards._build_checkin_card(INFO)
    txt = json.dumps(card.attachments[0].content, ensure_ascii=False)
    assert "Stock actual: 8" in txt
    assert "¿Con cuántos chocolates arrancás" not in txt


def test_template_mateo_tiene_tiktok_y_card_muestra_seccion(cards):
    import activity_state as a
    template = json.load(open("activities_template.json", encoding="utf-8"))
    aids = {x["id"]: x for x in template["activities"]}
    assert "tiktok-videos-diarios" in aids
    assert aids["tiktok-videos-diarios"]["tipo"] == "diaria"

    # init_week de Mateo siembra la diaria y la sección TikTok aparece
    mateo = "malvarado@biodegradablesecuador.com"
    wk = a.get_week(mateo)
    assert "tiktok-videos-diarios" in wk["activities"]
    card = cards._build_checkin_card(mateo)
    txt = json.dumps(card.attachments[0].content, ensure_ascii=False)
    assert "TikTok" in txt
