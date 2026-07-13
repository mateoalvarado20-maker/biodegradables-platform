"""KPIs de aprendizaje y reporte semanal — F3.6 (reglas #18 y #23 del board).

- **Learning Velocity (LV):** qué tan rápido aprende el sistema — hipótesis
  evaluadas/confirmadas/rechazadas/inconclusas/pendientes de datos, y cambios
  REALES del playbook (reglas nacidas, promovidas, degradadas, revertidas).
- **Learning Accuracy (LA):** qué tan CORRECTAMENTE aprende — % de
  confirmaciones que sobreviven a la siguiente evaluación con más datos.
  **LV nunca se reporta sin LA** (no se premia aprender rápido lo que luego
  hay que corregir).
- **Reporte semanal:** responde las preguntas del board — qué aprendimos, qué
  dejamos de creer, qué reglas nacieron/degradaron, qué experimentos tuvieron
  mayor retorno de aprendizaje (incertidumbre resuelta) y cuáles generaron
  valor comercial (objetivos sales/leads/conversations).

Todo determinista, desde el registro de experimentos y las revisiones del
playbook — cero LLM.
"""

from __future__ import annotations

from marketing.experiments import ExperimentRegistry
from marketing.playbook import Playbook
from org.kernel.department import Department

COMMERCIAL_OBJECTIVES = ("sales", "leads", "conversations")
UNCERTAIN = ("requiere_mas_datos", "inconclusa")


def _playbook_changes_since(playbook: Playbook, since_iso: str) -> dict[str, list[str]]:
    changes = {"nacidas": [], "promovidas": [], "degradadas": [], "revertidas": []}
    for rule_id in playbook.rules(include_obsolete=True):
        for rev in playbook.history(rule_id):
            if rev["created_at"] < since_iso:
                continue
            r = rev["rationale"]
            if rev["revision"] == 1:
                changes["nacidas"].append(f"{rule_id} ({rev['status']})")
            elif r.startswith("REVERT"):
                changes["revertidas"].append(f"{rule_id} → rev. anterior")
            elif r.startswith("promovida"):
                changes["promovidas"].append(f"{rule_id} → {rev['status']}")
            elif r.startswith("degradada"):
                changes["degradadas"].append(f"{rule_id} → {rev['status']}")
    return changes


def learning_velocity(
    registry: ExperimentRegistry, playbook: Playbook, since_iso: str
) -> dict:
    evals = registry.since(since_iso)
    verdicts = {"confirmada": 0, "rechazada": 0, "inconclusa": 0, "requiere_mas_datos": 0}
    for e in evals:
        verdicts[e["verdict"]] = verdicts.get(e["verdict"], 0) + 1
    changes = _playbook_changes_since(playbook, since_iso)
    return {
        "since": since_iso,
        "hypotheses_evaluated": len(evals),
        "unique_hypotheses": len({(e["objective"], e["hypothesis_key"]) for e in evals}),
        **verdicts,
        "playbook_changes": {k: len(v) for k, v in changes.items()},
        "playbook_changes_detail": changes,
    }


def learning_accuracy(registry: ExperimentRegistry) -> dict:
    """% de confirmaciones que siguen confirmadas en la evaluación siguiente.
    None = aún no hay re-evaluaciones de confirmadas (sin datos ≠ 100%)."""
    keys = {
        (v["objective"], v["hypothesis_key"]) for v in registry.latest_verdicts().values()
    }
    pairs = survived = 0
    for objective, key in keys:
        hist = registry.history(key, objective)
        for prev, nxt in zip(hist, hist[1:]):
            if prev["verdict"] == "confirmada":
                pairs += 1
                survived += int(nxt["verdict"] == "confirmada")
    return {
        "confirmations_reevaluated": pairs,
        "still_correct": survived,
        "la": round(survived / pairs, 3) if pairs else None,
    }


def weekly_learning_report(
    dept: Department,
    registry: ExperimentRegistry,
    playbook: Playbook,
    since_iso: str,
) -> dict:
    """Las preguntas del board, respondidas con datos. Queda en el journal."""
    evals = registry.since(since_iso)
    lv = learning_velocity(registry, playbook, since_iso)
    la = learning_accuracy(registry)

    aprendimos = [
        f"{e['objective']}/{e['hypothesis_key']} (efecto {e['effect']:+.1%}, {e['confidence']})"
        for e in evals
        if e["verdict"] == "confirmada"
    ]
    # dejamos de creer: rechazos de hipótesis que ANTES estaban confirmadas
    dejamos = []
    for e in evals:
        if e["verdict"] != "rechazada":
            continue
        hist = registry.history(e["hypothesis_key"], e["objective"])
        if any(h["verdict"] == "confirmada" for h in hist[:-1]):
            dejamos.append(f"{e['objective']}/{e['hypothesis_key']}")
    dejamos += lv["playbook_changes_detail"]["degradadas"]

    # mayor retorno de aprendizaje: incertidumbre RESUELTA (transiciones
    # requiere_mas_datos/inconclusa → confirmada/rechazada) en hipótesis
    # tocadas dentro de la ventana
    retorno = []
    keys_in_window = {(e["objective"], e["hypothesis_key"]) for e in evals}
    for objective, key in sorted(keys_in_window):
        hist = registry.history(key, objective)
        for prev, nxt in zip(hist, hist[1:]):
            if prev["verdict"] in UNCERTAIN and nxt["verdict"] in ("confirmada", "rechazada"):
                retorno.append(f"{objective}/{key}: {prev['verdict']} → {nxt['verdict']}")

    valor_comercial = [
        f"{e['objective']}/{e['hypothesis_key']} (efecto {e['effect']:+.1%})"
        for e in evals
        if e["verdict"] == "confirmada" and e["objective"] in COMMERCIAL_OBJECTIVES
    ]

    report = {
        "since": since_iso,
        "que_aprendimos": aprendimos,
        "que_dejamos_de_creer": sorted(set(dejamos)),
        "reglas_nuevas": lv["playbook_changes_detail"]["nacidas"],
        "reglas_promovidas": lv["playbook_changes_detail"]["promovidas"],
        "reglas_degradadas": lv["playbook_changes_detail"]["degradadas"],
        "mayor_retorno_de_aprendizaje": retorno,
        "valor_comercial": valor_comercial,
        "lv": lv,
        "la": la,  # LV nunca sin LA (regla #18)
    }
    dept.decide(
        f"reporte de aprendizaje desde {since_iso}: {len(aprendimos)} aprendizajes, "
        f"{len(report['que_dejamos_de_creer'])} creencias retiradas, "
        f"LA={la['la']} ({la['confirmations_reevaluated']} re-evaluaciones)",
        context_refs=(
            [f"aprendimos: {a}" for a in aprendimos[:4]]
            + [f"dejamos de creer: {d}" for d in report["que_dejamos_de_creer"][:3]]
            + [f"retorno de aprendizaje: {r}" for r in retorno[:3]]
        ),
        correlation_id="learning-report",
    )
    return report


def render_report(report: dict) -> str:
    """Texto para el self-report semanal a gerencia."""
    la = report["la"]
    lv = report["lv"]
    lineas = [
        f"APRENDIZAJE desde {report['since']}",
        f"LV: {lv['hypotheses_evaluated']} evaluaciones ({lv['unique_hypotheses']} hipótesis) — "
        f"{lv['confirmada']} confirmadas, {lv['rechazada']} rechazadas, "
        f"{lv['inconclusa']} inconclusas, {lv['requiere_mas_datos']} esperando datos",
        f"LA: {la['la'] if la['la'] is not None else 'sin re-evaluaciones aún'} "
        f"({la['still_correct']}/{la['confirmations_reevaluated']} confirmaciones sobreviven)",
        "¿Qué aprendimos?: " + ("; ".join(report["que_aprendimos"]) or "nada nuevo esta semana"),
        "¿Qué dejamos de creer?: " + ("; ".join(report["que_dejamos_de_creer"]) or "nada"),
        "Reglas nuevas: " + ("; ".join(report["reglas_nuevas"]) or "ninguna"),
        "Reglas degradadas: " + ("; ".join(report["reglas_degradadas"]) or "ninguna"),
        "Mayor retorno de aprendizaje: "
        + ("; ".join(report["mayor_retorno_de_aprendizaje"]) or "sin incertidumbres resueltas"),
        "Valor comercial: " + ("; ".join(report["valor_comercial"]) or "sin confirmaciones comerciales"),
    ]
    return "\n".join(lineas)
