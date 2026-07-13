"""Tests M1 parte 2 — aprobación L0 + notificaciones (sender inyectado)."""

import pytest

from marketing.approvals import ApprovalError, approve, pending_approval, reject
from marketing.daily_run import DailyRunner

from tests.test_daily_run import env  # noqa: F401 (fixture reutilizada)


def _con_notificaciones(ctx, buzon):
    def sender(from_user, to, subject, html):
        buzon.append({"to": to, "subject": subject, "html": html})

    ctx.notify_from = "bot@test"
    ctx.notify_to = ["ceo@test"]
    ctx.sender = sender
    return ctx


def test_resumen_diario_con_pendientes_l0(env):  # noqa: F811
    dept, ctx, _ = env
    buzon = []
    _con_notificaciones(ctx, buzon)
    DailyRunner(dept, ctx).run(day="2026-07-11")

    assert len(buzon) == 1
    mail = buzon[0]
    assert "Corrida diaria 2026-07-11" in mail["subject"]
    assert "aprobación L0" in mail["html"]
    assert mail["html"].count("<tr>") == 2  # las 2 piezas pendientes listadas


def test_alerta_cuando_la_corrida_queda_incompleta(env):  # noqa: F811
    dept, ctx, _ = env
    buzon = []
    _con_notificaciones(ctx, buzon)

    def runner_roto(args):
        raise RuntimeError("render caído")

    ctx.services.runner = runner_roto
    DailyRunner(dept, ctx).run(day="2026-07-11")
    assert any("ALERTA" in m["subject"] for m in buzon)


def test_notificar_jamas_tumba_la_corrida(env):  # noqa: F811
    dept, ctx, _ = env

    def sender_roto(*a):
        raise RuntimeError("smtp caído")

    ctx.notify_from, ctx.notify_to, ctx.sender = "bot@test", ["ceo@test"], sender_roto
    result = DailyRunner(dept, ctx).run(day="2026-07-11")
    assert result["completa"] is True  # la corrida terminó igual
    assert dept.events.fetch(types=["ops.notify_failed"])  # y el fallo quedó medido


def test_aprobar_transiciona_y_audita(env):  # noqa: F811
    dept, ctx, _ = env
    DailyRunner(dept, ctx).run(day="2026-07-11")
    pendientes = pending_approval(ctx.queue)
    assert len(pendientes) == 2

    approve(dept, ctx.queue, pendientes[0], by="daniel@test")
    assert ctx.queue.get(pendientes[0]).status == "scheduled"
    reject(dept, ctx.queue, pendientes[1], by="daniel@test", reason="tono flojo")
    assert ctx.queue.get(pendientes[1]).status == "qa_rejected"
    assert pending_approval(ctx.queue) == []
    refs = " ".join(e["decision"] for e in dept.journal.entries())
    assert "L0 APROBADA" in refs and "L0 RECHAZADA" in refs
    assert dept.events.fetch(types=["content.l0_approved"])


def test_guardas_de_aprobacion(env):  # noqa: F811
    dept, ctx, _ = env
    DailyRunner(dept, ctx).run(day="2026-07-11")
    pid = pending_approval(ctx.queue)[0]
    with pytest.raises(ApprovalError, match="motivo"):
        reject(dept, ctx.queue, pid, by="x", reason="  ")
    approve(dept, ctx.queue, pid, by="x")
    with pytest.raises(ApprovalError, match="solo se aprueba"):
        approve(dept, ctx.queue, pid, by="x")  # ya no está qa_approved