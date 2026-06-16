"""Tests del flujo de delegación de actividades/recordatorios (Activity Bot).

Regresión del incidente 2026-06-15: Daniel intentó delegar a "Gabriela Sánchez"
(nombre completo, con acento) y el sistema no la encontró porque
`_resolve_collaborator` solo hacía match EXACTO contra los alias de
KNOWN_COLLABORATORS ('gabriela'), no contra el nombre completo.

Cubre: usuario válido, múltiples actividades, nombre completo con acentos,
usuario inexistente, datos incompletos, actividad ya asignada, variantes de
mayúsculas/espacios/acentos y desambiguación de nombres repetidos.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_with(monkeypatch, tmp_path, known_collaborators):
    """Recarga la cadena de módulos con STATE_DIR aislado y un directorio de
    colaboradores específico. Devuelve el módulo ask_agent recargado."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KNOWN_COLLABORATORS", known_collaborators)
    monkeypatch.delenv("AAD_ID_TO_EMAIL", raising=False)
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import reminders
    importlib.reload(reminders)
    import ask_agent
    return importlib.reload(ask_agent)


# Directorio realista: las dos Gabrielas + Mateo + asistentes.
_KNOWN = (
    "mateo:malvarado@biodegradablesecuador.com,"
    "gabriela:gsanchez@biodegradablesecuador.com,"
    "info:info@biodegradablesecuador.com,"
    "quito:quito@biodegradablesecuador.com,"
    "gye:info@biodegradablesecuador.com,"
    "uio:quito@biodegradablesecuador.com"
)


@pytest.fixture()
def ra(tmp_path, monkeypatch):
    return _reload_with(monkeypatch, tmp_path, _KNOWN)


# ---------- Resolución de nombres ----------

def test_resolve_alias_simple(ra):
    assert ra._resolve_collaborator("Mateo") == "malvarado@biodegradablesecuador.com"
    assert ra._resolve_collaborator("GABRIELA") == "gsanchez@biodegradablesecuador.com"


def test_resolve_nombre_completo_con_acento(ra):
    """EL bug del incidente: 'Gabriela Sánchez' debe resolver, no fallar."""
    assert (
        ra._resolve_collaborator("Gabriela Sánchez")
        == "gsanchez@biodegradablesecuador.com"
    )
    # Sin acento, distinto case, espacios extra
    assert (
        ra._resolve_collaborator("  gabriela   sanchez  ")
        == "gsanchez@biodegradablesecuador.com"
    )
    assert (
        ra._resolve_collaborator("GABRIELA SANCHEZ")
        == "gsanchez@biodegradablesecuador.com"
    )


def test_resolve_segunda_gabriela_por_apellido(ra):
    """'Gabriela Bravo' es la asistente de GYE (info@), distinta de gsanchez@."""
    assert (
        ra._resolve_collaborator("Gabriela Bravo")
        == "info@biodegradablesecuador.com"
    )


def test_resolve_local_part_y_apellido(ra):
    assert (
        ra._resolve_collaborator("gsanchez") == "gsanchez@biodegradablesecuador.com"
    )
    assert (
        ra._resolve_collaborator("Sánchez") == "gsanchez@biodegradablesecuador.com"
    )


def test_resolve_email_registrado_y_fantasma(ra):
    assert (
        ra._resolve_collaborator("quito@biodegradablesecuador.com")
        == "quito@biodegradablesecuador.com"
    )
    # Email no registrado → None (garantía A6/C4, sin usuario fantasma)
    assert ra._resolve_collaborator("gbravo@otrodominio.com") is None
    assert ra._resolve_collaborator("desconocido") is None
    assert ra._resolve_collaborator("") is None
    assert ra._resolve_collaborator(None) is None


def test_resolve_ambiguo_devuelve_candidatos(tmp_path, monkeypatch):
    """Sin alias 'gabriela', 'gabriela' a secas matchea a las DOS → ambiguo."""
    known = (
        "gsanchez:gsanchez@biodegradablesecuador.com,"
        "info:info@biodegradablesecuador.com"
    )
    ra = _reload_with(monkeypatch, tmp_path, known)
    detail = ra._resolve_collaborator_detail("Gabriela")
    assert detail["status"] == "ambiguous"
    emails = {c["email"] for c in detail["candidates"]}
    assert emails == {
        "gsanchez@biodegradablesecuador.com",
        "info@biodegradablesecuador.com",
    }
    # El wrapper de compat devuelve None ante ambigüedad (no elige al azar)
    assert ra._resolve_collaborator("Gabriela") is None


# ---------- Handler: add_activity_for_collaborator ----------

def _call(ra, name, args, who="dsanchez@biodegradablesecuador.com"):
    return json.loads(ra._call_tool(name, args, user_email=who))


def test_delegar_actividad_valida(ra):
    out = _call(ra, "add_activity_for_collaborator", {
        "target_user": "Mateo",
        "activity_id": "reporte-trimestral",
        "nombre": "Preparar reporte trimestral",
    })
    assert out["ok"] is True
    assert out["target"] == "malvarado@biodegradablesecuador.com"
    # Quedó persistida en el state de Mateo
    import activity_state
    wk = activity_state.get_week("malvarado@biodegradablesecuador.com")
    assert "reporte-trimestral" in wk["activities"]


def test_delegar_a_gabriela_sanchez_nombre_completo(ra):
    """Reproduce el incidente y verifica que ahora SÍ funciona."""
    out = _call(ra, "add_activity_for_collaborator", {
        "target_user": "Gabriela Sánchez",
        "activity_id": "reunion-pardux",
        "nombre": "Reunión con Pardux 4:30 PM",
    })
    assert out["ok"] is True
    assert out["target"] == "gsanchez@biodegradablesecuador.com"


def test_delegar_multiples_actividades(ra):
    ids = ["tarea-a", "tarea-b", "tarea-c"]
    for aid in ids:
        out = _call(ra, "add_activity_for_collaborator", {
            "target_user": "gabriela",
            "activity_id": aid,
            "nombre": aid.upper(),
        })
        assert out["ok"] is True
    import activity_state
    wk = activity_state.get_week("gsanchez@biodegradablesecuador.com")
    for aid in ids:
        assert aid in wk["activities"]


def test_delegar_usuario_inexistente(ra):
    out = _call(ra, "add_activity_for_collaborator", {
        "target_user": "Pepito Pérez",
        "activity_id": "x",
        "nombre": "X",
    })
    assert "error" in out
    assert "no encontré" in out["error"].lower()
    # El mensaje lista colaboradores disponibles (accionable)
    assert "biodegradablesecuador.com" in out["error"]


def test_delegar_datos_incompletos(ra):
    # Falta activity_id y nombre
    out = _call(ra, "add_activity_for_collaborator", {"target_user": "Mateo"})
    assert "error" in out
    assert "activity_id" in out["error"] and "nombre" in out["error"]
    # Falta target_user
    out2 = _call(ra, "add_activity_for_collaborator", {
        "activity_id": "x", "nombre": "X",
    })
    assert "error" in out2
    assert "qui" in out2["error"].lower()  # "a QUIÉN"


def test_delegar_actividad_ya_asignada(ra):
    args = {
        "target_user": "Mateo",
        "activity_id": "duplicada",
        "nombre": "Duplicada",
    }
    first = _call(ra, "add_activity_for_collaborator", args)
    assert first["ok"] is True
    second = _call(ra, "add_activity_for_collaborator", args)
    assert "error" in second
    assert "ya está asignada" in second["error"].lower() or "ya existe" in second["error"].lower()


def test_delegar_ambiguo_handler(tmp_path, monkeypatch):
    known = (
        "gsanchez:gsanchez@biodegradablesecuador.com,"
        "info:info@biodegradablesecuador.com"
    )
    ra = _reload_with(monkeypatch, tmp_path, known)
    out = _call(ra, "add_activity_for_collaborator", {
        "target_user": "Gabriela",
        "activity_id": "x",
        "nombre": "X",
    })
    assert "error" in out
    assert "varios" in out["error"].lower() or "cuál" in out["error"].lower()


# ---------- Handler: schedule_reminder_for_collaborator ----------

def test_delegar_recordatorio_valido(ra):
    out = _call(ra, "schedule_reminder_for_collaborator", {
        "target_user": "Gabriela Sánchez",
        "send_at": "2026-06-20T16:30",
        "message": "Reunión con Pardux",
    })
    assert out["ok"] is True
    assert out["target"] == "gsanchez@biodegradablesecuador.com"


def test_recordatorio_sin_fecha(ra):
    out = _call(ra, "schedule_reminder_for_collaborator", {
        "target_user": "Mateo",
        "message": "algo",
    })
    assert "error" in out
    assert "send_at" in out["error"]


def test_recordatorio_usuario_inexistente(ra):
    out = _call(ra, "schedule_reminder_for_collaborator", {
        "target_user": "Nadie",
        "send_at": "2026-06-20T16:30",
        "message": "x",
    })
    assert "error" in out


# ---------- list_team_collaborators ----------

def test_list_team_collaborators_agrupa_por_email(ra):
    out = json.loads(ra._call_tool("list_team_collaborators", {}))
    by_email = {c["email"]: c for c in out}
    # info@ aparece una sola vez con alias 'info' y 'gye'
    assert "info@biodegradablesecuador.com" in by_email
    info = by_email["info@biodegradablesecuador.com"]
    assert set(info["alias"]) >= {"info", "gye"}
    assert info["nombre"]  # trae nombre humano, no solo email
