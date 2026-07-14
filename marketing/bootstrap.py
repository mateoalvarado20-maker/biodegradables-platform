"""Bootstrap de producción del departamento — M1.

Arma el Department + DailyContext REALES desde los datos del tenant
(`tenants/<slug>/marketing.yaml`) y el estado persistente en
`~/.ver-os/prod-<slug>/`. Es el único lugar que conoce rutas y wiring;
el resto del dominio recibe objetos.

Nota de gobernanza: los OKRs numéricos del charter siguen pendientes del
board — el charter operativo usa un placeholder EXPLÍCITO hasta entonces.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from marketing.brand import load_brand_context, load_hard_rules, load_tts_voice
from marketing.daily_run import DailyContext
from marketing.experiments import ExperimentRegistry
from marketing.pillars import active_pillars
from marketing.pipeline import PipelineServices
from marketing.playbook import Playbook
from marketing.profiles import load_profile
from marketing.queue import ContentQueue
from org.kernel import Charter, Department, TenantStore, parse_manifest

_REPO_ROOT = Path(__file__).parent.parent

MANIFEST = {
    "verops": "0.1",
    "package": {"name": "marketing-brain", "version": "0.1.0", "publisher": "ver-ia",
                "kind": "department"},
    "trust_tier": "first_party",
    "capabilities": [{"llm": {}}, "notify"],
    "contracts": {"provides": ["WeeklyDeptReport@1", "LeadHandoff@1"],
                  "consumes": ["LeadOutcome@1?", "BudgetEnvelope@1?"]},
    "events": {"emits": ["content.published@1", "content.copy_review@1",
                         "ops.daily_run@1", "playbook.rule_changed@1"]},
    "autonomy": {"max_level": "L2", "default": "L0"},
    "compliance": {"pii": "none"},
}


def _marketing_yaml(tenant_slug: str) -> dict:
    path = _REPO_ROOT / "tenants" / tenant_slug / "marketing.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_production(tenant_slug: str, base_dir: str | Path | None = None):
    """(dept, DailyContext) listos para operar. base_dir solo para tests."""
    cfg = _marketing_yaml(tenant_slug)
    daily = cfg.get("daily", {})
    state_dir = Path(base_dir) if base_dir else Path.home() / ".ver-os" / f"prod-{tenant_slug}"
    out_dir = state_dir / "out"

    store = TenantStore(tenant_slug, base_dir=state_dir)
    charter = Charter(
        okrs=("operación diaria MVP — OKRs numéricos pendientes de aprobación del board",),
        budget_usd_month=float(daily.get("budget_usd_month", 60.0)),
        approved_by="dsanchez@biodegradablesecuador.com",
        approved_at="2026-07-10",
        hard_rules=load_hard_rules(tenant_slug),
    )
    dept = Department(parse_manifest(MANIFEST), charter, store,
                      granted_capabilities={"llm", "notify"})
    if dept.state.lifecycle == "proposed":  # primera vez: instalar
        dept.lifecycle_to("installed")
        dept.lifecycle_to("onboarding")
        dept.lifecycle_to("active")

    services = PipelineServices(
        profile=load_profile("tiktok"),
        brand_context=load_brand_context(tenant_slug),
        voice=load_tts_voice(tenant_slug),
        out_dir=out_dir,
        brand_name=tenant_slug.replace("-", " ").title(),
    )
    ctx = DailyContext(
        queue=ContentQueue(dept),
        playbook=Playbook(dept),
        registry=ExperimentRegistry(dept),
        services=services,
        pillars=active_pillars(tenant_slug),
        objective_by_pillar=cfg.get("objective_by_pillar", {}),
        tenant_id=tenant_slug,
        n_briefs=int(daily.get("n_briefs", 2)),
        notify_from=str(daily.get("notify_from", "")),
        notify_to=list(daily.get("notify_to", [])),
        l0_approvers=list(daily.get("l0_approvers", [])),
    )
    return dept, ctx
