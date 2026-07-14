"""Tests M1 — tarjeta de aprobación L0 en Teams (2026-07-14).

Tres capas: el store compartido (marketing_l0_state, backend archivo), el
builder de la tarjeta (bot_cards) y el puente PC↔bot (marketing/l0_remote,
con caller inyectado — nada de red).
"""

import pytest

import marketing_l0_state as l0
from marketing.approvals import approve, pending_approval
from marketing.daily_run import DailyRunner
from marketing.l0_remote import apply_remote_decisions, push_pending_cards

from tests.test_daily_run import env  # noqa: F401 (fixture reutilizada)

DECIDERS = ["dsanchez@test", "malvarado@test"]


@pytest.fixture(autouse=True)
def _store_aislado(monkeypatch, tmp_path):
    monkeypatch.setattr(l0, "STATE_PATH", tmp_path / "l0.json")
    monkeypatch.delenv("MARKETING_L0_TABLE_CONN", raising=False)
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)


# ---------- store compartido ----------

def test_create_pending_y_get():
    entry = l0.create_pending("pkg-1", titulo="Título", formato="video",
                              deciders=DECIDERS)
    assert entry["decision"] == "" and entry["applied_at"] == ""
    assert l0.get("pkg-1")["titulo"] == "Título"
    assert "pkg-1" in l0.pending()


def test_create_pending_no_pisa_una_decision_tomada():
    l0.create_pending("pkg-1", titulo="T", formato="video", deciders=DECIDERS)
    l0.record_decision("pkg-1", "aprobar", decided_by="dsanchez@test")
    entry = l0.create_pending("pkg-1", titulo="OTRO", formato="video",
                              deciders=DECIDERS)
    assert entry["decision"] == "aprobar"  # idempotente, decisión intacta
    assert l0.get("pkg-1")["titulo"] == "T"


def test_decision_aprobar_y_anti_doble_tap():
    l0.create_pending("pkg-1", titulo="T", formato="video", deciders=DECIDERS)
    entry = l0.record_decision("pkg-1", "aprobar", decided_by="Dsanchez@Test")
    assert entry["decided_by"] == "dsanchez@test"  # normalizado
    with pytest.raises(l0.L0StateError, match="ya fue aprobar"):
        l0.record_decision("pkg-1", "rechazar", decided_by="dsanchez@test",
                           motivo="cambié de idea")


def test_rechazar_exige_motivo():
    l0.create_pending("pkg-1", titulo="T", formato="video", deciders=DECIDERS)
    with pytest.raises(l0.L0StateError, match="motivo"):
        l0.record_decision("pkg-1", "rechazar", decided_by="dsanchez@test")


def test_solo_deciders_autorizados():
    l0.create_pending("pkg-1", titulo="T", formato="video", deciders=DECIDERS)
    with pytest.raises(l0.L0StateError, match="no está autorizado"):
        l0.record_decision("pkg-1", "aprobar", decided_by="intruso@test")


def test_pieza_inexistente_falla_claro():
    with pytest.raises(l0.L0StateError, match="no está esperando"):
        l0.record_decision("pkg-fantasma", "aprobar", decided_by="dsanchez@test")


def test_flujo_unapplied_y_mark_applied():
    l0.create_pending("pkg-1", titulo="T", formato="video", deciders=DECIDERS)
    l0.create_pending("pkg-2", titulo="T2", formato="carousel", deciders=DECIDERS)
    l0.record_decision("pkg-1", "aprobar", decided_by="dsanchez@test")
    assert set(l0.unapplied_decisions()) == {"pkg-1"}  # pkg-2 sigue sin decidir
    l0.mark_applied("pkg-1")
    assert l0.unapplied_decisions() == {}
    assert set(l0.pending()) == {"pkg-2"}


# ---------- tarjeta ----------

def test_card_l0_intent_acciones_y_motivo():
    from bot_cards import build_marketing_l0_card

    act = build_marketing_l0_card({
        "package_id": "pkg-x", "titulo": "Título de prueba",
        "formato": "video", "duracion_s": 27.5,
        "hook": "el hook", "caption": "la caption",
    })
    card = act.attachments[0].content
    datas = [a["data"] for a in card["actions"]]
    assert all(d["intent"] == "mkt_l0" and d["mkt_pid"] == "pkg-x" for d in datas)
    assert {d["mkt_decision"] for d in datas} == {"aprobar", "rechazar"}
    assert any(b.get("id") == "mkt_motivo" for b in card["body"])
    textos = str(card["body"])
    assert "Título de prueba" in textos and "el hook" in textos


# ---------- puente PC ↔ bot ----------

def test_push_sin_config_es_skip_no_error(env):  # noqa: F811
    dept, ctx, _ = env
    out = push_pending_cards(dept, ctx.queue, ["pkg-x"], ["ceo@test"])
    assert out["status"] == "skip"


def test_push_manda_piezas_con_contexto(env):  # noqa: F811
    dept, ctx, _ = env
    DailyRunner(dept, ctx).run(day="2026-07-11")
    pendientes = pending_approval(ctx.queue)
    capturado = {}

    def caller(method, path, payload):
        capturado.update(method=method, path=path, payload=payload)
        return {"status": "ok", "entregas": {}}

    push_pending_cards(dept, ctx.queue, pendientes, ["ceo@test"], caller=caller)
    assert capturado["path"] == "/admin/marketing/l0-cards"
    piezas = capturado["payload"]["piezas"]
    assert len(piezas) == len(pendientes)
    assert all(p["titulo"] and p["formato"] and p["hook"] for p in piezas)
    assert capturado["payload"]["recipients"] == ["ceo@test"]


def test_aplicar_decisiones_de_teams(env):  # noqa: F811
    dept, ctx, _ = env
    DailyRunner(dept, ctx).run(day="2026-07-11")
    p1, p2 = pending_approval(ctx.queue)
    decisiones = {
        p1: {"decision": "aprobar", "decided_by": "dsanchez@test", "motivo": ""},
        p2: {"decision": "rechazar", "decided_by": "dsanchez@test",
             "motivo": "tono flojo"},
    }
    confirmados = []

    def caller(method, path, payload):
        if path == "/admin/marketing/l0-decisions":
            return {"decisiones": decisiones}
        if path == "/admin/marketing/l0-applied":
            confirmados.extend(payload["package_ids"])
            return {"status": "ok"}
        raise AssertionError(f"llamada inesperada: {path}")

    out = apply_remote_decisions(dept, ctx.queue, caller=caller)
    assert set(out["aplicadas"]) == {p1, p2}
    assert ctx.queue.get(p1).status == "scheduled"
    assert ctx.queue.get(p2).status == "qa_rejected"
    assert set(confirmados) == {p1, p2}
    # misma auditoría que el CLI, con la superficie identificada
    assert any("teams:dsanchez@test" in e["decision"]
               for e in dept.journal.entries())


def test_decision_redundante_se_confirma_sin_romper(env):  # noqa: F811
    """Si Mateo ya aprobó por CLI, la decisión de Teams es redundante: se
    confirma como aplicada (no queda en loop) y la cola no cambia."""
    dept, ctx, _ = env
    DailyRunner(dept, ctx).run(day="2026-07-11")
    pid = pending_approval(ctx.queue)[0]
    approve(dept, ctx.queue, pid, by="cli:mateo")

    confirmados = []

    def caller(method, path, payload):
        if path == "/admin/marketing/l0-decisions":
            return {"decisiones": {pid: {"decision": "rechazar",
                                         "decided_by": "dsanchez@test",
                                         "motivo": "tarde"}}}
        confirmados.extend(payload["package_ids"])
        return {"status": "ok"}

    out = apply_remote_decisions(dept, ctx.queue, caller=caller)
    assert out["redundantes"] == [pid]
    assert ctx.queue.get(pid).status == "scheduled"  # la decisión CLI manda
    assert confirmados == [pid]


def test_bot_caido_no_tumba_nada(env):  # noqa: F811
    dept, ctx, _ = env

    def caller_roto(method, path, payload):
        raise RuntimeError("bot caído")

    out = apply_remote_decisions(dept, ctx.queue, caller=caller_roto)
    assert out["status"] == "error"
    assert dept.events.fetch(types=["ops.l0_remote_failed"])
