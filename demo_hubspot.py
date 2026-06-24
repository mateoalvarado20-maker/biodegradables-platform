"""demo_hubspot — reemplazo de hubspot_client para el entorno DEMO.

Expone las mismas funciones públicas que hubspot_client con datos sintéticos
deterministas (semilla fija, relativo a hoy). Se engancha desde el final de
hubspot_client cuando DEMO_MODE=1. Los leads/deals son un funnel aparte (no se
derivan de las facturas), pero internamente consistentes (ganados+perdidos =
cerrados, etc.). Nada real.
"""
from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

_EC_TZ = timezone(timedelta(hours=-5))
_SEED = 73															# semilla fija

__all__ = [
    "leads_ayer", "leads_promedio_7d", "leads_30d", "deals_ganados_ayer",
    "leads_por_dia_ultimos_7d", "pipeline_abierto", "deals_stuck",
    "deals_won_30d", "deals_lost_30d", "conversion_rate_30d", "leads_sin_responder",
]

_FUENTES = ["Website", "WhatsApp", "Feria comercial", "Referido", "Google Ads", "Instagram"]
_EMPRESAS = [
    "Comercial Su Despensa", "Hotel Costa Azul", "Minimarket El Ahorro",
    "Restaurante La Sazón", "Distribuidora La Bahía", "Cafetería El Trébol",
    "Autoservicio Akí", "Bazar La Perla", "Despensa Doña Marta",
]
_NOMBRES = [
    "Ana Torres", "Pedro Jiménez", "Sofía Castro", "Diego Paredes", "Elena Vaca",
    "Raúl Mendoza", "Karla Núñez", "Tomás Reyes",
]


def _today() -> date:
    override = os.environ.get("DEMO_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return datetime.now(_EC_TZ).date()


def _rng(salt: str) -> random.Random:
    # determinista por día + función, estable dentro del día
    return random.Random(f"{_SEED}-{_today().isoformat()}-{salt}")


def leads_ayer() -> dict:
    r = _rng("leads_ayer")
    by_source: dict[str, int] = {}
    for _ in range(r.randint(3, 9)):
        f = r.choice(_FUENTES)
        by_source[f] = by_source.get(f, 0) + 1
    total = sum(by_source.values())
    top = max(by_source.items(), key=lambda kv: kv[1]) if by_source else ("Website", 0)
    return {
        "total": total,
        "top_source": top[0],
        "top_source_count": top[1],
        "by_source": by_source,
    }


def leads_promedio_7d() -> float:
    r = _rng("prom7")
    return round(r.uniform(3.5, 6.5), 1)


def leads_30d() -> dict:
    r = _rng("leads30")
    total = r.randint(95, 150)
    anterior = r.randint(85, 140)
    delta = ((total - anterior) / anterior * 100) if anterior else 0.0
    return {
        "total": total,
        "anterior": anterior,
        "delta_pct": round(delta, 1),
        "top_source": "Website",
        "top_source_count": int(total * 0.4),
    }


def leads_por_dia_ultimos_7d() -> list[dict]:
    r = _rng("porDia")
    out = []
    for i in range(7, 0, -1):
        d = _today() - timedelta(days=i)
        out.append({"fecha": d.isoformat(), "fecha_obj": d, "count": r.randint(2, 8)})
    return out


def deals_ganados_ayer() -> dict:
    r = _rng("ganAyer")
    count = r.randint(0, 3)
    return {"count": count, "revenue": round(count * r.uniform(1200, 4800), 2)}


def pipeline_abierto() -> dict:
    r = _rng("pipe")
    count = r.randint(14, 26)
    return {"count": count, "valor": round(count * r.uniform(2500, 5500), 2)}


def deals_stuck(dias_min: int = 14) -> dict:
    r = _rng("stuck")
    n = r.randint(2, 5)
    top = []
    for i in range(n):
        top.append({
            "id": f"deal-{1000+i}",
            "nombre": r.choice(_EMPRESAS),
            "monto": round(r.uniform(1500, 9000), 2),
            "dias_sin_movimiento": r.randint(dias_min, 45),
        })
    top.sort(key=lambda d: d["monto"], reverse=True)
    return {
        "dias_min": dias_min,
        "count": n,
        "valor": round(sum(d["monto"] for d in top), 2),
        "top": top[:5],
    }


def deals_won_30d() -> dict:
    r = _rng("won30")
    count = r.randint(8, 16)
    return {"count": count, "revenue": round(count * r.uniform(1800, 4200), 2)}


def deals_lost_30d() -> dict:
    r = _rng("lost30")
    return {"count": r.randint(2, 6)}


def conversion_rate_30d() -> dict:
    won = deals_won_30d()
    lost = deals_lost_30d()
    cerrados = won["count"] + lost["count"]
    tasa = (won["count"] / cerrados) if cerrados else 0.0
    return {
        "ganados": won["count"],
        "perdidos": lost["count"],
        "cerrados_total": cerrados,
        "tasa_cierre": round(tasa, 2),
        "revenue_30d": won["revenue"],
    }


def leads_sin_responder(horas_min: int = 24, dias_ventana: int = 7) -> dict:
    r = _rng("sinResp")
    n = r.randint(0, 4)
    leads = []
    for i in range(n):
        dias = r.randint(1, dias_ventana)
        created = (datetime.now(_EC_TZ) - timedelta(days=dias)).isoformat()
        nombre = r.choice(_NOMBRES)
        leads.append({
            "nombre": nombre,
            "email": nombre.lower().replace(" ", ".").replace("í", "i").replace("á", "a")
                     + "@empresa-demo.com",
            "created": created,
            "dias": dias,
        })
    return {"count": n, "leads": leads[:5]}
