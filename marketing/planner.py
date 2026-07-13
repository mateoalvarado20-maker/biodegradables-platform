"""Planificador — F3.5 (reglas #22 y #13 del board): piensa como Media Manager.

Cada brief del plan diario existe por UNA de dos razones (regla #22):
- **explotar**: aplica una regla del playbook, ponderada por su MADUREZ
  (consolidada 1.5 > validada 1.0 > experimental 0.5) — vender más con lo que
  sabemos. Su hipótesis re-testea la regla con piezas nuevas: alimenta el KPI
  Learning Accuracy.
- **explorar** (~20%): produce exactamente los datos que el registro de
  experimentos declaró que faltan (veredictos `requiere_mas_datos` e
  `inconclusa` — la agenda de exploración no es aleatoria, es la lista de
  huecos de evidencia), o prueba valores nunca medidos del catálogo.

El plan completo puede EXPLICARSE (`DailyPlan.explain()`): qué publicar mañana,
por qué, qué hipótesis valida cada pieza, qué conocimiento explota, qué %
explora y qué se aprende aunque una pieza tenga pocas views. Determinista con
rng inyectable; sin LLM: planificar es asignar propósito, no redactar.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from marketing.experiments import MIN_N_PER_GROUP
from marketing.guionista import ScriptBrief
from marketing.models import Hypothesis, Pillar, PlatformProfile
from org.kernel.department import Department

VERSION = "0.1"
EXPLORE_RATIO = 0.2  # 80/20 del diseño original
CAROUSEL_EVERY = 5  # 1 de cada 5 piezas es carrusel

# Catálogo de valores explorables por dimensión (crece con el uso)
CATALOG = {
    "hook_type": ["pregunta", "dato-sorprendente", "error-comun", "lista",
                  "demostracion", "mito-realidad", "storytelling", "antes-despues"],
    "cta_type": ["contacto-directo", "aprender-mas", "seguir-cuenta"],
}


@dataclass(frozen=True)
class PlannedBrief:
    brief: ScriptBrief
    intent: str  # "explotar" | "explorar"
    rationale: str
    knowledge_used: list[str] = field(default_factory=list)
    expected_learning: str = ""


@dataclass
class DailyPlan:
    briefs: list[PlannedBrief]

    @property
    def explore_ratio(self) -> float:
        if not self.briefs:
            return 0.0
        return sum(1 for b in self.briefs if b.intent == "explorar") / len(self.briefs)

    def explain(self) -> str:
        lineas = [
            f"PLAN: {len(self.briefs)} piezas — "
            f"{self.explore_ratio:.0%} explora / {1 - self.explore_ratio:.0%} explota."
        ]
        for i, p in enumerate(self.briefs, 1):
            b = p.brief
            lineas.append(
                f"[{i}] {b.format} · pilar {b.pillar_id} · gancho {b.hook_type} · "
                f"franja {b.time_slot} — {p.intent.upper()}"
            )
            lineas.append(f"    por qué: {p.rationale}")
            lineas.append(f"    hipótesis: {b.hypothesis.question}")
            if p.knowledge_used:
                lineas.append(f"    conocimiento explotado: {', '.join(p.knowledge_used)}")
            lineas.append(f"    aprendemos aunque rinda poco: {p.expected_learning}")
        return "\n".join(lineas)


def _exploration_agenda(
    latest_verdicts: dict[str, dict], rules: list[dict], default_objective: str
) -> list[dict]:
    """Los huecos de evidencia, priorizados: requiere_mas_datos > inconclusa >
    valores del catálogo jamás medidos. Cada entrada lleva su objetivo (#23)."""
    agenda = [v for v in latest_verdicts.values() if v["verdict"] == "requiere_mas_datos"]
    agenda += [v for v in latest_verdicts.values() if v["verdict"] == "inconclusa"]
    conocidos = {v["hypothesis_key"] for v in latest_verdicts.values()}
    conocidos |= {f"{r['dimension']}={r['value']}" for r in rules}
    for dim, values in CATALOG.items():
        for value in values:
            if f"{dim}={value}" not in conocidos:
                agenda.append(
                    {
                        "hypothesis_key": f"{dim}={value}",
                        "objective": None,  # se asigna por pilar al armar el brief
                        "verdict": "sin_medir",
                        "next_data_needed": f"primeras {MIN_N_PER_GROUP} piezas con {dim}={value}",
                    }
                )
    return agenda


def _base_brief(tenant_id, pillar, fmt, hook, cta, slot, objective, hypothesis) -> ScriptBrief:
    return ScriptBrief(
        tenant_id=tenant_id, pillar_id=pillar, format=fmt, hook_type=hook,
        cta_type=cta, time_slot=slot, objective=objective, hypothesis=hypothesis,
    )


def plan_day(
    dept: Department,
    *,
    tenant_id: str,
    pillars: list[Pillar],
    rules: list[dict],  # playbook.active_rules(): dicts con dimension/value/status/weight
    latest_verdicts: dict[str, dict],  # registry.latest_verdicts()
    profile: PlatformProfile,
    n_briefs: int = 2,
    explore_ratio: float = EXPLORE_RATIO,
    objective_by_pillar: dict[str, str] | None = None,
    default_objective: str = "awareness",
    rng: random.Random | None = None,
) -> DailyPlan:
    """El plan de mañana. Recibe conocimiento y evidencia como DATOS (regla #20:
    el Planificador lee, no escribe). `objective_by_pillar` mapea cada pilar a
    su prioridad de negocio (regla #23) — dato del tenant."""
    if not pillars:
        raise ValueError("sin pilares activos no hay plan (Brand Brain vacío)")
    rng = rng or random.Random(0)
    obj_map = objective_by_pillar or {}
    slots = list(profile.posting_windows)
    agenda = _exploration_agenda(latest_verdicts, rules, default_objective)
    n_explore = max(1, round(n_briefs * explore_ratio)) if n_briefs > 1 else (1 if not rules else 0)
    if not rules:
        n_explore = n_briefs  # sin conocimiento aún: todo es aprendizaje honesto

    planned: list[PlannedBrief] = []
    usados: set[tuple] = set()

    def pick_pillar(i):  # rotación simple de pilares activos
        return pillars[i % len(pillars)].id

    for i in range(n_briefs):
        slot = slots[i % len(slots)]
        fmt = "carousel" if (i + 1) % CAROUSEL_EVERY == 0 else "video"
        explorar = i < n_explore
        pillar = pick_pillar(i)

        if explorar and agenda:
            target = agenda[i % len(agenda)]
            dim, value = target["hypothesis_key"].split("=", 1)
            objective = target.get("objective") or obj_map.get(pillar, default_objective)
            hook = value if dim == "hook_type" else rng.choice(CATALOG["hook_type"])
            cta = value if dim == "cta_type" else rng.choice(CATALOG["cta_type"])
            if dim == "pillar":
                pillar = value
            hyp = Hypothesis(
                question=f"¿{target['hypothesis_key']} rinde mejor que el resto (objetivo {objective})?",
                metric=f"score compuesto del objetivo {objective}",
                success_criteria=f"veredicto del Analista con n≥{MIN_N_PER_GROUP} por grupo",
                decision_if_true="el Analista propondrá la regla al Knowledge Manager",
                decision_if_false="descartar la variante y no volver a explorarla pronto",
            )
            planned.append(
                PlannedBrief(
                    brief=_base_brief(tenant_id, pillar, fmt, hook, cta, slot, objective, hyp),
                    intent="explorar",
                    rationale=(
                        f"el registro marca {target['hypothesis_key']} (objetivo {objective}) "
                        f"como '{target['verdict']}' — esta pieza produce el dato que falta"
                    ),
                    expected_learning=target.get("next_data_needed", "sumar muestra al hueco de evidencia"),
                )
            )
            continue

        # EXPLOTAR: regla del playbook elegida por peso de madurez
        if rules:
            weights = [r["weight"] for r in rules]
            rule = rng.choices(rules, weights=weights, k=1)[0]
            dim, value = rule["dimension"], rule["value"]
            hook = value if dim == "hook_type" else rng.choice(CATALOG["hook_type"])
            cta = value if dim == "cta_type" else rng.choice(CATALOG["cta_type"])
            if dim == "pillar":
                pillar = value
            combo = (pillar, hook, fmt)
            if combo in usados and len(rules) > 1:  # anti-repetición simple
                rule = rng.choices(rules, weights=weights, k=1)[0]
                dim, value = rule["dimension"], rule["value"]
                hook = value if dim == "hook_type" else rng.choice(CATALOG["hook_type"])
            usados.add((pillar, hook, fmt))
            objective = rule["objective"]
            hyp = Hypothesis(
                question=f"¿la regla {rule['rule_id']} ({rule['status']}) sigue rindiendo con piezas nuevas?",
                metric=f"score compuesto del objetivo {objective}",
                success_criteria="mantener el efecto medido en la próxima evaluación",
                decision_if_true="la racha de confirmaciones sostiene o sube su madurez",
                decision_if_false="el Analista propondrá degradarla (alimenta el KPI LA)",
            )
            planned.append(
                PlannedBrief(
                    brief=_base_brief(tenant_id, pillar, fmt, hook, cta, slot, objective, hyp),
                    intent="explotar",
                    rationale=(
                        f"aplica {rule['rule_id']} en estado {rule['status']} "
                        f"(peso {rule['weight']}): {rule['action']}"
                    ),
                    knowledge_used=[rule["rule_id"]],
                    expected_learning=(
                        "re-testear la regla con piezas nuevas — cada pieza de "
                        "explotación es también evidencia para Learning Accuracy"
                    ),
                )
            )

    plan = DailyPlan(briefs=planned)
    dept.decide(
        f"plan diario: {len(planned)} piezas ({plan.explore_ratio:.0%} explora)",
        context_refs=[f"[{p.intent}] {p.brief.pillar_id}/{p.brief.hook_type}: {p.rationale[:90]}"
                      for p in planned][:10],
        correlation_id="plan-diario",
    )
    return plan
