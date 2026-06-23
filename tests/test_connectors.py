"""Tests de los conectores (Acción 5, base). Usan fakes — NO tocan red ni los bots.

Verifican dos cosas: (1) cada adaptador delega en su cliente subyacente, y
(2) satisface la interfaz Protocol correspondiente (runtime_checkable).
"""
from __future__ import annotations

from datetime import date

import pytest

from connectors.contifico_erp import ContificoErp
from connectors.factory import build_crm, build_erp, build_mail
from connectors.graph_mailer import GraphMailer
from connectors.hubspot_crm import HubspotCrm
from core.connectors.base import CrmConnector, ErpConnector, MailConnector


class _FakeContifico:
    def ventas_dia(self, fecha):
        return {"fecha": fecha, "total": 100.0}

    def ventas_rango(self, a, b):
        return {"total": 200.0}

    def ventas_por_ciudad(self, fecha, fecha_final=None):
        return {"UIO": 1, "GYE": 2}

    def cumplimiento_mes(self, fecha_referencia=None):
        return {"mtd": 50.0}


class _FakeHubspot:
    def leads_ayer(self):
        return {"total": 3}

    def leads_promedio_7d(self):
        return 2.5

    def deals_ganados_ayer(self):
        return {"total": 1}

    def pipeline_abierto(self):
        return {"valor": 1000.0}


class _FakeGraph:
    def __init__(self):
        self.sent = []

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return "ok"


def test_contifico_erp_delegates_and_satisfies_interface():
    erp = ContificoErp(client=_FakeContifico())
    assert isinstance(erp, ErpConnector)
    assert erp.ventas_dia(date(2026, 6, 1))["total"] == 100.0
    assert erp.ventas_rango(date(2026, 6, 1), date(2026, 6, 2))["total"] == 200.0
    assert erp.ventas_por_ciudad(date(2026, 6, 1))["GYE"] == 2
    assert erp.cumplimiento_mes()["mtd"] == 50.0


def test_hubspot_crm_delegates_and_satisfies_interface():
    crm = HubspotCrm(client=_FakeHubspot())
    assert isinstance(crm, CrmConnector)
    assert crm.leads_ayer()["total"] == 3
    assert crm.leads_promedio_7d() == 2.5
    assert crm.pipeline_abierto()["valor"] == 1000.0


def test_graph_mailer_delegates_and_satisfies_interface():
    fake = _FakeGraph()
    mail = GraphMailer(client=fake)
    assert isinstance(mail, MailConnector)
    assert mail.send(to=["a@b.com"], subject="x", html_body="<p>hi</p>") == "ok"
    assert fake.sent[0]["subject"] == "x"


def test_factory_builds_known_providers():
    assert isinstance(build_erp("contifico", client=_FakeContifico()), ContificoErp)
    assert isinstance(build_crm("hubspot", client=_FakeHubspot()), HubspotCrm)
    assert isinstance(build_mail("graph", client=_FakeGraph()), GraphMailer)


def test_factory_rejects_unknown_provider():
    # Un proveedor sin adaptador falla claro, no en silencio.
    with pytest.raises(ValueError):
        build_erp("sap-inexistente")
    with pytest.raises(ValueError):
        build_crm("zoho-inexistente")
