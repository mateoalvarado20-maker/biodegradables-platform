"""demo_contifico — reemplazo de contifico_client para el entorno DEMO.

Expone las MISMAS funciones públicas que contifico_client, pero alimentadas por
el dataset sintético de `demo_seed` (no toca la API real). Se engancha desde el
final de contifico_client cuando DEMO_MODE=1, así los reportes y el Data Bot
consumen datos ficticios sin cambiar una sola línea de su código.

Estrategia: `get_documentos` devuelve las facturas sintéticas del rango pedido.
Como las funciones de ventas/cumplimiento/top/saldos de contifico_client agregan
SOBRE get_documentos, esas siguen funcionando tal cual (sólo cambia la fuente).
Sólo las funciones de CARTERA se reescriben acá, porque las reales dependen de
`condiciones_credito.json` (clientes reales) que no aplica al demo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import demo_seed

# Funciones que el hook de contifico_client va a reemplazar por las de este módulo.
__all__ = [
    "get_documentos",
    "cartera_vencida_por_ciudad",
    "clientes_sin_credito_con_saldo",
    "cartera_kpis",
    "cartera_antiguedad_buckets",
]

CARTERA_SALDO_MIN = 1.0
CARTERA_BUCKETS_ORDEN = [
    ("Dentro del Plazo", 0),
    ("1-30 Días", 1),
    ("31-60 Días", 2),
    ("61-90 Días", 3),
    ("+90 Días", 4),
]


def _parse(fecha: Any) -> date:
    if isinstance(fecha, datetime):
        return fecha.date()
    if isinstance(fecha, date):
        return fecha
    return date.fromisoformat(str(fecha))


def get_documentos(
    fecha_inicial: date, fecha_final: date, tipo: str = "FAC", **_kw: Any
) -> list[dict[str, Any]]:
    """Facturas sintéticas emitidas en [fecha_inicial, fecha_final]. Forma idéntica
    a la del Contifico real (persona, vendedor, detalles, documento, saldo...)."""
    fi, ff = _parse(fecha_inicial), _parse(fecha_final)
    out: list[dict[str, Any]] = []
    for inv in demo_seed.dataset()["invoices"]:
        if fi <= inv["_fecha"] <= ff:
            # devolver sin las claves internas (_fecha/_ciudad/plazo_dias)
            out.append({k: v for k, v in inv.items() if not k.startswith("_")})
    return out


# ---- Cartera (reescrita para el demo; usa el plazo guardado en cada factura) ----

def _facturas_cartera(today: date, meses_atras: int):
    """Itera (factura, saldo, fecha_vencimiento) de facturas con saldo > 0."""
    fi = today - timedelta(days=meses_atras * 30)
    for inv in demo_seed.dataset()["invoices"]:
        if inv["anulado"]:
            continue
        saldo = float(inv.get("saldo") or 0.0)
        if saldo <= 0:
            continue
        if not (fi <= inv["_fecha"] <= today):
            continue
        plazo = int(inv.get("plazo_dias") or 0)
        if plazo <= 0:
            continue  # contado: no es cartera
        venc = inv["_fecha"] + timedelta(days=plazo)
        yield inv, saldo, venc


def cartera_vencida_por_ciudad(
    ciudad: str, n: int = 5, *, meses_atras: int = 6,
    fecha_referencia: date | None = None,
) -> list[dict[str, Any]]:
    today = _parse(fecha_referencia) if fecha_referencia else demo_seed.dataset()["today"]
    ciudad = (ciudad or "").upper()
    by_cli: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cliente": "", "saldo_vencido": 0.0, "facturas_vencidas": 0,
            "factura_mas_antigua": "", "dias_atraso_max": 0, "plazo_dias": 0,
            "fecha_emision": None, "fecha_vencimiento": None,
        }
    )
    for inv, saldo, venc in _facturas_cartera(today, meses_atras):
        if inv["_ciudad"] != ciudad or saldo <= CARTERA_SALDO_MIN:
            continue
        if venc >= today:
            continue  # aún dentro de plazo
        atraso = (today - venc).days
        cli = inv["persona"]["razon_social"]
        e = by_cli[cli]
        e["cliente"] = cli
        e["saldo_vencido"] += saldo
        e["facturas_vencidas"] += 1
        if atraso > e["dias_atraso_max"]:
            e["dias_atraso_max"] = atraso
            e["plazo_dias"] = inv["plazo_dias"]
            e["factura_mas_antigua"] = inv["documento"]
            e["fecha_emision"] = inv["_fecha"].strftime("%d/%m/%Y")
            e["fecha_vencimiento"] = venc.strftime("%d/%m/%Y")
    rows = sorted(by_cli.values(), key=lambda r: r["saldo_vencido"], reverse=True)
    for r in rows:
        r["saldo_vencido"] = round(r["saldo_vencido"], 2)
    return rows[:n]


def clientes_sin_credito_con_saldo(
    ciudad: str, n: int | None = None, *, meses_atras: int = 6,
    fecha_referencia: date | None = None,
) -> list[dict[str, Any]]:
    """Demo: clientes contado (plazo<=0) con saldo pendiente por ciudad.
    Complemento de cartera_vencida_por_ciudad (que solo mira los con plazo)."""
    today = _parse(fecha_referencia) if fecha_referencia else demo_seed.dataset()["today"]
    ciudad = (ciudad or "").upper()
    fi = today - timedelta(days=meses_atras * 30)
    by_cli: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cliente": "", "saldo_pendiente": 0.0, "facturas_pendientes": 0,
            "factura_mas_antigua": "", "dias_desde_emision_max": 0,
            "fecha_emision": None,
        }
    )
    for inv in demo_seed.dataset()["invoices"]:
        if inv["anulado"] or inv["_ciudad"] != ciudad:
            continue
        saldo = float(inv.get("saldo") or 0.0)
        if saldo <= CARTERA_SALDO_MIN:
            continue
        if not (fi <= inv["_fecha"] <= today):
            continue
        if int(inv.get("plazo_dias") or 0) > 0:
            continue  # tiene crédito → va a cartera_vencida, no acá
        dias = (today - inv["_fecha"]).days
        e = by_cli[inv["persona"]["razon_social"]]
        e["cliente"] = inv["persona"]["razon_social"]
        e["saldo_pendiente"] += saldo
        e["facturas_pendientes"] += 1
        if dias > e["dias_desde_emision_max"]:
            e["dias_desde_emision_max"] = dias
            e["factura_mas_antigua"] = inv["documento"]
            e["fecha_emision"] = inv["_fecha"].strftime("%d/%m/%Y")
    rows = sorted(by_cli.values(), key=lambda r: r["saldo_pendiente"], reverse=True)
    for r in rows:
        r["saldo_pendiente"] = round(r["saldo_pendiente"], 2)
    return rows if n is None else rows[:n]


def cartera_kpis(
    fecha_referencia: date | None = None, *, meses_atras: int = 12,
) -> dict[str, Any]:
    today = _parse(fecha_referencia) if fecha_referencia else demo_seed.dataset()["today"]
    total = vencida = no_vencida = weighted = 0.0
    for _inv, saldo, venc in _facturas_cartera(today, meses_atras):
        total += saldo
        if venc >= today:
            no_vencida += saldo
        else:
            vencida += saldo
            weighted += (today - venc).days * saldo
    pct = (vencida / total) if total else 0.0
    dias = (weighted / vencida) if vencida else 0.0
    return {
        "cartera_total": round(total, 2),
        "cartera_vencida": round(vencida, 2),
        "cartera_no_vencida": round(no_vencida, 2),
        "pct_vencida": round(pct, 4),
        "dias_atraso_promedio": round(dias, 1),
    }


def cartera_antiguedad_buckets(
    fecha_referencia: date | None = None, *, meses_atras: int = 12,
) -> list[dict[str, Any]]:
    today = _parse(fecha_referencia) if fecha_referencia else demo_seed.dataset()["today"]
    saldos = {label: 0.0 for label, _ in CARTERA_BUCKETS_ORDEN}
    for _inv, saldo, venc in _facturas_cartera(today, meses_atras):
        if venc >= today:
            saldos["Dentro del Plazo"] += saldo
            continue
        atraso = (today - venc).days
        if atraso <= 30:
            saldos["1-30 Días"] += saldo
        elif atraso <= 60:
            saldos["31-60 Días"] += saldo
        elif atraso <= 90:
            saldos["61-90 Días"] += saldo
        else:
            saldos["+90 Días"] += saldo
    return [
        {"bucket": label, "saldo": round(saldos[label], 2), "orden": orden}
        for label, orden in CARTERA_BUCKETS_ORDEN
    ]
