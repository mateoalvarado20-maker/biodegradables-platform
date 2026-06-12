"""Tests de identidad y autorización (Fase 2).

La matriz que habría detectado el bug de producción: usuarios que resolvían
al email equivocado por display name, bucket compartido `unknown`, allowlist
fail-open y colaboradores fantasma.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    """teams_bot recargado con STATE_DIR aislado y colaboradores de prueba."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "KNOWN_COLLABORATORS",
        "mateo:malvarado@biodegradablesecuador.com,"
        "gabriela:gsanchez@biodegradablesecuador.com,"
        "quito:quito@biodegradablesecuador.com",
    )
    monkeypatch.delenv("AAD_ID_TO_EMAIL", raising=False)
    import safe_json
    importlib.reload(safe_json)
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    import teams_bot
    teams_bot = importlib.reload(teams_bot)
    return SimpleNamespace(teams_bot=teams_bot, ask_agent=ask_agent)


def _fake_context(aad_id="", name="", channel_email="", props=None):
    """TurnContext mínimo con los atributos que usa _user_email."""
    from_prop = SimpleNamespace(
        aad_object_id=aad_id,
        name=name,
        additional_properties=props or {},
    )
    activity = SimpleNamespace(
        from_property=from_prop,
        channel_data={"email": channel_email} if channel_email else {},
    )
    return SimpleNamespace(activity=activity)


# ---------- Resolución de identidad ----------

def test_channel_data_resuelve_y_aprende(bot):
    tb = bot.teams_bot
    ctx = _fake_context(
        aad_id="1b4a872f-0000-0000-0000-000000000000",
        name="Biodegradables Ecuador",
        channel_email="info@biodegradablesecuador.com",
    )
    assert tb._user_email(ctx) == "info@biodegradablesecuador.com"
    # Aprendió el mapeo: la próxima resolución no depende de channel_data
    ctx2 = _fake_context(aad_id="1b4a872f-9999-8888-7777-666666666666")
    assert tb._user_email(ctx2) == "info@biodegradablesecuador.com"


def test_display_name_YA_NO_resuelve_identidad(bot):
    """El bug de producción (auditoría A1): 'Gabriela Bravo' → gsanchez@.

    Ahora un display name que matchea un alias NO asigna identidad: el
    usuario queda aislado por su AAD id.
    """
    tb = bot.teams_bot
    ctx = _fake_context(
        aad_id="aaaa1111-0000-0000-0000-000000000000",
        name="Gabriela Bravo",
    )
    email = tb._user_email(ctx)
    assert email != "gsanchez@biodegradablesecuador.com", (
        "CONTAMINACIÓN: display name volvió a asignar identidad ajena"
    )
    assert email == "unidentified-aaaa1111@biodegradablesecuador.com"


def test_sin_aad_id_se_rechaza_no_bucket_compartido(bot):
    """Auditoría A4: dos personas sin AAD id compartían `unidentified-unknown@`."""
    tb = bot.teams_bot
    ctx = _fake_context(aad_id="", name="Persona Misteriosa")
    assert tb._user_email(ctx) == ""  # rechazado — el caller no crea state


def test_dos_no_identificados_no_comparten_bucket(bot):
    tb = bot.teams_bot
    a = tb._user_email(_fake_context(aad_id="11111111-a", name="Persona Uno"))
    b = tb._user_email(_fake_context(aad_id="22222222-b", name="Persona Dos"))
    assert a and b and a != b


def test_env_override_gana_sobre_todo(bot, monkeypatch):
    monkeypatch.setenv(
        "AAD_ID_TO_EMAIL", "deadbeef:quito@biodegradablesecuador.com"
    )
    import teams_bot as tb
    tb = importlib.reload(tb)
    ctx = _fake_context(
        aad_id="deadbeef-0000", name="Otro Nombre",
        channel_email="otro@biodegradablesecuador.com",
    )
    assert tb._user_email(ctx) == "quito@biodegradablesecuador.com"


def test_aad_lookup_no_se_sobrescribe_en_conflicto(bot):
    tb = bot.teams_bot
    tb._remember_aad_email("abc12345", "info@biodegradablesecuador.com", "test")
    tb._remember_aad_email("abc12345", "otro@biodegradablesecuador.com", "test")
    assert tb._load_aad_lookup()["abc12345"] == "info@biodegradablesecuador.com"


# ---------- Autorización ----------

def test_data_allowlist_fail_closed(bot, monkeypatch):
    """Auditoría C1: env var vacía abría el Data Bot a todo el tenant."""
    monkeypatch.setenv("BOT_ALLOWED_USERS_DATA", "")
    import teams_bot as tb
    tb = importlib.reload(tb)
    assert tb._is_allowed_data("cualquiera@biodegradablesecuador.com") is False


def test_data_allowlist_normal(bot):
    tb = bot.teams_bot
    assert tb._is_allowed_data("dsanchez@biodegradablesecuador.com") is True
    assert tb._is_allowed_data("intruso@biodegradablesecuador.com") is False
    assert tb._is_allowed_data("") is False


def test_email_vacio_nunca_autorizado(bot):
    tb = bot.teams_bot
    assert tb._is_allowed_activities("") is False


# ---------- Colaboradores: no más usuarios fantasma ----------

def test_resolve_collaborator_alias(bot):
    ra = bot.ask_agent
    assert ra._resolve_collaborator("Mateo") == "malvarado@biodegradablesecuador.com"
    assert ra._resolve_collaborator("GABRIELA") == "gsanchez@biodegradablesecuador.com"


def test_resolve_collaborator_email_registrado(bot):
    ra = bot.ask_agent
    assert (
        ra._resolve_collaborator("quito@biodegradablesecuador.com")
        == "quito@biodegradablesecuador.com"
    )


def test_resolve_collaborator_rechaza_email_fantasma(bot):
    """Auditoría A6/C4: cualquier string con '@' creaba un usuario fantasma."""
    ra = bot.ask_agent
    assert ra._resolve_collaborator("gbravo@biodegradables.com") is None
    assert ra._resolve_collaborator("alguien@otrodominio.com") is None
    assert ra._resolve_collaborator("desconocido") is None
    assert ra._resolve_collaborator("") is None


# ---------- Contexto embebido en cards (auditoría A5) ----------

def test_checkin_card_lleva_contexto(bot, monkeypatch):
    tb = bot.teams_bot
    import activity_state
    activity_state = importlib.reload(activity_state)
    card_activity = tb._build_checkin_card("quito@biodegradablesecuador.com")
    card = card_activity.attachments[0].content
    submit = [a for a in card["actions"] if a["data"].get("intent") == "submit_checkin"][0]
    assert submit["data"]["ctx_user"] == "quito@biodegradablesecuador.com"
    assert submit["data"]["ctx_fecha"] == activity_state._today().isoformat()
    assert submit["data"]["ctx_wk"] == activity_state.week_key()


# ---------- Admin token ----------

def test_admin_token_propio_y_fail_closed(bot, monkeypatch):
    tb = bot.teams_bot

    class FakeRequest:
        def __init__(self, token):
            self.headers = {"x-admin-token": token}

    monkeypatch.setattr(tb, "ADMIN_API_TOKEN", "secreto-test")
    tb._require_admin(FakeRequest("secreto-test"))  # no lanza
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        tb._require_admin(FakeRequest("incorrecto"))
    with pytest.raises(HTTPException):
        tb._require_admin(FakeRequest(""))
    # Sin token configurado: fail-closed
    monkeypatch.setattr(tb, "ADMIN_API_TOKEN", "")
    with pytest.raises(HTTPException):
        tb._require_admin(FakeRequest(""))
