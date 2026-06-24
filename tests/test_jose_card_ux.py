"""Cambios UX del bot de José (2026-06-23):
- Asistencia ya NO va en el card de ruta; va en un card dedicado (17:10).
- Las entregas hechas se colapsan en el card de ruta.
- El form ad-hoc tiene campo de observación.
- Confirmaciones cortas: helper de pendientes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

teams_bot = pytest.importorskip("teams_bot")


def _card(activity):
    return activity.attachments[0].content


def _walk_ids(node, out):
    if isinstance(node, dict):
        if node.get("id"):
            out.add(node["id"])
        for v in node.values():
            _walk_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_ids(v, out)


def _walk_intents(node, out):
    if isinstance(node, dict):
        d = node.get("data")
        if isinstance(d, dict) and d.get("intent"):
            out.add(d["intent"])
        for v in node.values():
            _walk_intents(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_intents(v, out)


def test_asistencia_card_tiene_horario_e_intent():
    card = _card(teams_bot._build_jose_asistencia_card(teams_bot.JOSE_EMAIL))
    ids, intents = set(), set()
    _walk_ids(card, ids)
    _walk_intents(card, intents)
    assert "horario_estandar" in ids
    assert "jose_asistencia" in intents


def test_ruta_card_ya_no_tiene_boton_asistencia(state_env):
    # skip_refresh=True para no llamar a Contifico
    card = _card(teams_bot._build_jose_ruta_card(teams_bot.JOSE_EMAIL, skip_refresh=True))
    intents = set()
    _walk_intents(card, intents)
    assert "jose_asistencia" not in intents
    # pero sí mantiene las acciones de ruta
    assert "jose_start_ruta" in intents or "jose_end_ruta" in intents
    assert "jose_actualizar" in intents


def test_ruta_card_form_adhoc_tiene_observacion(state_env):
    card = _card(teams_bot._build_jose_ruta_card(teams_bot.JOSE_EMAIL, skip_refresh=True))
    ids = set()
    _walk_ids(card, ids)
    assert "jose_adhoc_obs" in ids
    assert "jose_adhoc_direccion" in ids


def test_entregas_se_colapsan(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    hoy = a._today().isoformat()
    # Dos destinos ad-hoc para no depender de Contifico
    r1 = a.add_destino_adhoc(jose, cliente="ACME", direccion="Av X", tipo="entrega", fecha=hoy)
    a.add_destino_adhoc(jose, cliente="BETA", direccion="Av Y", tipo="entrega", fecha=hoy)
    # Marcar uno entregado
    a.marcar_entrega(jose, r1["factura_id"], entregado=True, cliente_label="ACME", fecha=hoy)

    card = _card(teams_bot._build_jose_ruta_card(jose, skip_refresh=True))
    import json
    blob = json.dumps(card, ensure_ascii=False)
    # El entregado aparece colapsado en la sección "Ya entregadas hoy"
    assert "Ya entregadas hoy" in blob
    # El pendiente (BETA) sigue mostrando sus botones de marcar
    intents = set()
    _walk_intents(card, intents)
    assert "jose_marcar_entrega" in intents


def test_pendientes_suffix(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    hoy = a._today().isoformat()
    a.add_destino_adhoc(jose, cliente="ACME", direccion="Av X", tipo="entrega", fecha=hoy)
    suf = teams_bot._jose_pendientes_suffix(jose, hoy)
    assert "1" in suf and "pendiente" in suf.lower()
