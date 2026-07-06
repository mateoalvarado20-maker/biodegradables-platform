"""Tests del kernel VER-OS (ROADMAP.md F0). Cubren los invariantes de
PROPUESTA_VER_OS.md §1.3: aislamiento de tenant, capacidades explícitas,
auditoría inmutable, metering con corte duro, envelope de eventos idempotente,
y las máquinas de estado de autonomía/ciclo de vida.
"""

import sqlite3

import pytest

from org.contracts import ContractError, available_contracts, validate_payload
from org.kernel import (
    BudgetExceeded,
    CapabilityError,
    Charter,
    Department,
    ManifestError,
    TenantStore,
    parse_manifest,
)
from org.kernel.state import InvalidTransition


def _manifest(**overrides):
    data = {
        "verops": "0.1",
        "package": {
            "name": "test-dept",
            "version": "1.0.0",
            "publisher": "ver-ia",
            "kind": "department",
        },
        "trust_tier": "first_party",
        "capabilities": [{"llm": {"budget_usd_month": 5}}, "notify"],
        "contracts": {"provides": ["WeeklyDeptReport@1"], "consumes": ["LeadOutcome@1?"]},
        "events": {"emits": ["content.published@1"], "subscribes": []},
        "autonomy": {"max_level": "L2", "default": "L0"},
        "compliance": {"pii": "none"},
    }
    data.update(overrides)
    return data


def _charter():
    return Charter(
        okrs=("okr de prueba",),
        budget_usd_month=10.0,
        approved_by="board@test",
        approved_at="2026-07-06",
    )


@pytest.fixture
def store(tmp_path):
    s = TenantStore("tenant-a", base_dir=tmp_path)
    yield s
    s.close()


@pytest.fixture
def dept(store):
    return Department(
        parse_manifest(_manifest()), _charter(), store, granted_capabilities={"llm"}
    )


# --- manifest (F0.5) ---------------------------------------------------------


def test_manifest_valido():
    m = parse_manifest(_manifest())
    assert m.name == "test-dept"
    assert m.has_capability("llm")
    assert m.autonomy_max == "L2"


def test_manifest_invalido_reporta_todos_los_errores():
    data = _manifest(trust_tier="amigo", autonomy={"max_level": "L1", "default": "L3"})
    data["package"]["version"] = "uno"
    with pytest.raises(ManifestError) as exc:
        parse_manifest(data)
    msgs = " ".join(exc.value.errors)
    assert "trust_tier" in msgs
    assert "semver" in msgs
    assert "no puede superar" in msgs


def test_manifest_contratos_mal_formados():
    data = _manifest(contracts={"provides": ["SinVersion"]})
    with pytest.raises(ManifestError):
        parse_manifest(data)


# --- aislamiento de tenant (invariante #1) ------------------------------------


def test_aislamiento_entre_tenants(tmp_path):
    a = TenantStore("tenant-a", base_dir=tmp_path)
    b = TenantStore("tenant-b", base_dir=tmp_path)
    da = Department(parse_manifest(_manifest()), _charter(), a)
    da.emit("content.published", {"post": "x"})
    from org.kernel import EventBus

    assert EventBus(a).fetch() != []
    assert EventBus(b).fetch() == []
    assert a.path != b.path
    a.close()
    b.close()


def test_tenant_id_invalido(tmp_path):
    with pytest.raises(ValueError):
        TenantStore("Tenant Con Espacios!", base_dir=tmp_path)


# --- eventos (F0.2, invariante #6) --------------------------------------------


def test_evento_roundtrip_y_filtro(dept):
    dept.emit("content.published", {"post_id": "p1"}, correlation_id="c1")
    dept.emit("lead.captured", {"lead_id": "l1"})
    evs = dept.events.fetch(types=["lead.captured"])
    assert len(evs) == 1
    assert evs[0].payload == {"lead_id": "l1"}
    assert evs[0].tenant_id == "tenant-a"


def test_evento_type_invalido(dept):
    with pytest.raises(ValueError):
        dept.emit("sinpunto")


def test_eventos_append_only_a_nivel_sql(dept, store):
    dept.emit("content.published", {})
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE org_events SET type = 'hackeado'")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM org_events")


def test_consumo_idempotente(dept):
    ev = dept.emit("content.published", {})
    assert dept.events.process("consumidor-x", ev.event_id) is True
    assert dept.events.process("consumidor-x", ev.event_id) is False
    # otro consumidor sí lo procesa
    assert dept.events.process("consumidor-y", ev.event_id) is True


# --- journal (F0.3, invariante #4) --------------------------------------------


def test_journal_registra_y_lee(dept):
    eid = dept.decide("decisión de prueba", context_refs=["ref:1"], rule_applied="regla-7")
    entries = dept.journal.entries()
    assert entries[0]["entry_id"] == eid
    assert entries[0]["context_refs"] == ["ref:1"]
    assert entries[0]["rule_applied"] == "regla-7"


def test_journal_inmutable_a_nivel_sql(dept, store):
    dept.decide("algo")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE decision_journal SET decision = 'otra cosa'")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("DELETE FROM decision_journal")


def test_journal_rechaza_decision_vacia(dept):
    with pytest.raises(ValueError):
        dept.decide("   ")


# --- metering (F0.4, invariante #7) -------------------------------------------


def test_metering_suma_mes_y_unidades(dept):
    dept.meter.record("llm_tokens", qty=1000, usd=0.03)
    dept.meter.record("render", qty=1, usd=0.02)
    assert dept.meter.month_usd() == pytest.approx(0.05)
    assert dept.meter.month_units("llm_tokens") == 1000


def test_presupuesto_corta_en_duro(dept):
    dept.meter.record("llm_tokens", qty=1, usd=9.99)
    dept.ensure_budget(0.005)  # aún cabe
    with pytest.raises(BudgetExceeded):
        dept.ensure_budget(0.02)  # 9.99 + 0.02 > 10.00


# --- charter (invariante #5) ---------------------------------------------------


def test_charter_exige_okrs_y_aprobador():
    with pytest.raises(ValueError):
        Charter(okrs=(), budget_usd_month=10, approved_by="x", approved_at="hoy")
    with pytest.raises(ValueError):
        Charter(okrs=("a",), budget_usd_month=10, approved_by="", approved_at="hoy")


# --- capacidades (F0.7, invariante #2) -----------------------------------------


def test_capacidad_no_otorgada_bloquea(dept):
    dept.ensure_capability("llm")  # otorgada
    with pytest.raises(CapabilityError):
        dept.ensure_capability("notify")  # declarada pero NO otorgada


def test_capacidad_no_declarada_no_puede_otorgarse(store):
    with pytest.raises(CapabilityError):
        Department(
            parse_manifest(_manifest()),
            _charter(),
            store,
            granted_capabilities={"acceso_total"},
        )


# --- ciclo de vida y autonomía (F0.6) ------------------------------------------


def test_ciclo_de_vida_camino_feliz(dept):
    for to in ("installed", "onboarding", "active", "paused", "active", "retiring", "retired"):
        dept.lifecycle_to(to)
    assert dept.state.lifecycle == "retired"


def test_ciclo_de_vida_salto_invalido(dept):
    with pytest.raises(InvalidTransition):
        dept.lifecycle_to("active")  # proposed → active no existe


def test_autonomia_se_gana_de_a_un_nivel(dept):
    with pytest.raises(InvalidTransition):
        dept.promote_autonomy("L2", evidence="quiero saltar")
    dept.promote_autonomy("L1", evidence="2 semanas sin rechazos")
    assert dept.state.autonomy == "L1"


def test_autonomia_exige_evidencia_y_respeta_manifest_max(dept):
    with pytest.raises(ValueError):
        dept.promote_autonomy("L1", evidence="  ")
    dept.promote_autonomy("L1", evidence="ok")
    dept.promote_autonomy("L2", evidence="ok")
    with pytest.raises(InvalidTransition):
        dept.promote_autonomy("L3", evidence="ok")  # manifest max_level=L2


def test_demote_libre_hacia_abajo_con_razon(dept):
    dept.promote_autonomy("L1", evidence="ok")
    dept.demote_autonomy("L0", reason="incidente de contenido")
    assert dept.state.autonomy == "L0"
    with pytest.raises(ValueError):
        dept.demote_autonomy("L0", reason="")


def test_cambios_de_autonomia_quedan_auditados(dept):
    dept.promote_autonomy("L1", evidence="ok")
    decisiones = [e["decision"] for e in dept.journal.entries()]
    assert any("autonomía promovida" in d for d in decisiones)
    tipos = [e.type for e in dept.events.fetch(types=["org.autonomy_changed"])]
    assert tipos == ["org.autonomy_changed"]


# --- health (F0.7, componente #6) ----------------------------------------------


def test_health_reporta_lo_esencial(dept):
    dept.lifecycle_to("installed")
    dept.meter.record("render", usd=0.01)
    h = dept.health()
    assert h["dept_id"] == "test-dept"
    assert h["lifecycle"] == "installed"
    assert h["autonomy"] == "L0"
    assert h["month_spend_usd"] == pytest.approx(0.01)
    assert h["last_event"]["type"] == "org.lifecycle_changed"


# --- contratos (F0.8, invariante #3) --------------------------------------------


def test_contratos_fundacionales_registrados():
    regs = available_contracts()
    for c in ("LeadHandoff@1", "LeadOutcome@1", "WeeklyDeptReport@1", "EscalationRequest@1"):
        assert c in regs


def test_contrato_payload_valido():
    validate_payload(
        "LeadOutcome@1",
        {"lead_id": "l1", "outcome": "won", "value_usd": 450.0, "closed_at": "2026-07-06"},
    )


def test_contrato_payload_invalido_detalla_errores():
    with pytest.raises(ContractError) as exc:
        validate_payload(
            "LeadOutcome@1",
            {"outcome": "ganado", "value_usd": "mucho", "extra": 1, "closed_at": "x"},
        )
    msgs = " ".join(exc.value.errors)
    assert "lead_id" in msgs
    assert "outcome" in msgs
    assert "value_usd" in msgs
    assert "extra" in msgs


def test_contrato_desconocido():
    with pytest.raises(KeyError):
        validate_payload("Inexistente@9", {})
