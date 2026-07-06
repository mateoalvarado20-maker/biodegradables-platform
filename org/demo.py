"""Demo ejecutable del kernel VER-OS (ROADMAP.md F0.9).

Uso:  python -m org.demo
Corre sin red y sin secrets: crea un departamento de juguete en un tenant
efímero, lo instala, decide, emite, mide, escala autonomía y muestra health.
"""

from __future__ import annotations

import json
import sys
import tempfile

from org.contracts import validate_payload
from org.kernel import (
    BudgetExceeded,
    CapabilityError,
    Charter,
    Department,
    TenantStore,
    parse_manifest,
)

MANIFEST = {
    "verops": "0.1",
    "package": {"name": "demo-brain", "version": "0.1.0", "publisher": "ver-ia", "kind": "department"},
    "trust_tier": "first_party",
    "capabilities": [{"llm": {"budget_usd_month": 5}}, "notify"],
    "contracts": {"provides": ["WeeklyDeptReport@1"], "consumes": ["BudgetEnvelope@1?"]},
    "events": {"emits": ["demo.did_something@1"], "subscribes": []},
    "autonomy": {"max_level": "L2", "default": "L0"},
    "compliance": {"pii": "none"},
}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # consola Windows cp1252
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = TenantStore("demo-tenant", base_dir=tmp)
        manifest = parse_manifest(MANIFEST)
        charter = Charter(
            okrs=("demostrar el kernel end-to-end",),
            budget_usd_month=10.0,
            approved_by="board@demo",
            approved_at="2026-07-06",
        )
        dept = Department(manifest, charter, store, granted_capabilities={"llm"})

        print(f"[1] instalado como {dept.dept_id} v{manifest.version} (tier {manifest.trust_tier})")
        dept.lifecycle_to("installed")
        dept.lifecycle_to("onboarding")
        dept.lifecycle_to("active")
        print(f"[2] ciclo de vida: {dept.state.lifecycle} · autonomía: {dept.state.autonomy}")

        eid = dept.decide(
            "publicar contenido de prueba",
            context_refs=["playbook:regla-000"],
            alternatives=["no publicar"],
            correlation_id="demo-1",
        )
        ev = dept.emit("demo.did_something", {"nota": "hola VER-OS"}, correlation_id="demo-1")
        dept.meter.record("llm_tokens", qty=1200, usd=0.02, meta={"model": "demo"})
        print(f"[3] decisión #{eid} + evento {ev.type} ({ev.event_id[:8]}…) + gasto medido")

        dept.ensure_budget(0.5)
        try:
            dept.ensure_budget(9_999)
        except BudgetExceeded as e:
            print(f"[4] presupuesto corta en duro: {e}")

        try:
            dept.ensure_capability("notify")
        except CapabilityError as e:
            print(f"[5] capacidad declarada pero NO otorgada → bloqueada: {e}")

        report = {
            "dept_id": dept.dept_id,
            "week": "2026-W28",
            "okr_status": [{"okr": charter.okrs[0], "status": "on_track"}],
            "month_spend_usd": dept.meter.month_usd(),
        }
        validate_payload("WeeklyDeptReport@1", report)
        print("[6] WeeklyDeptReport@1 válido contra su contrato")

        dept.promote_autonomy("L1", evidence="2 semanas en L0 con <10% de rechazo humano")
        print(f"[7] autonomía ganada: {dept.state.autonomy}")

        print("[8] health:")
        print(json.dumps(dept.health(), indent=2, ensure_ascii=False))
        store.close()


if __name__ == "__main__":
    main()
