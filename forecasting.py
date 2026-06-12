"""Forecasting module para Data Bot (Phase H — 2026-05-31).

Proyecciones de ventas basadas en histórico de Contifico + ajuste por
factores externos vía razonamiento de Claude.

Math básica (sin scikit-learn / statsmodels) — suficiente para PYMES con 1-2
años de historia. Para sofisticación futura, considerar Prophet o SARIMA.

Funciones exportadas:
- `historical_monthly_sales(months_back)` — ventas mensuales del histórico
- `forecast_baseline(year, month)` — proyección pesimista/probable/optimista
- `product_mix_breakdown(keywords, months_back)` — top productos + filtro

Honesto:
- El "forecast" es ESTADÍSTICA SIMPLE + Claude razona. No es una predicción
  precisa. Es input para conversaciones gerenciales y planificación de
  contingencia.
- Los valores son rangos, no targets.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import contifico_client


# Cache en memoria del proceso. TTL 30 min — datos históricos no cambian
# constantemente. Evita pegar a Contifico 24 meses cada query del bot.
_HISTORY_CACHE: dict[int, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30 * 60


def historical_monthly_sales(months_back: int = 12) -> dict[str, dict[str, Any]]:
    """Ventas mensuales agregadas de los últimos N meses cerrados.

    Cacheado 30 min en memoria del proceso para que las queries del bot no
    pulleen 12-24 meses de Contifico en cada llamada (eso tomaba 30-60s y
    excedía el timeout de 15s de Bot Framework).

    NO incluye el mes en curso (no es comparable). Si months_back=12 y hoy es
    31/may/2026 → trae mayo 2025 .. abril 2026.
    """
    import time
    now = time.time()
    cached = _HISTORY_CACHE.get(months_back)
    if cached:
        ts, data = cached
        if now - ts < _CACHE_TTL_SECONDS:
            return data

    today = date.today()
    primer_dia_mes_actual = date(today.year, today.month, 1)
    end = primer_dia_mes_actual - timedelta(days=1)
    start_month = end.month - (months_back - 1)
    start_year = end.year
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    start = date(start_year, start_month, 1)

    docs = contifico_client._filter_validas(
        contifico_client.get_documentos(start, end)
    )

    by_month_total: dict[str, float] = defaultdict(float)
    by_month_count: dict[str, int] = defaultdict(int)
    for d in docs:
        fecha_str = d.get("fecha_emision", "") or ""
        try:
            dia, mes, anio = fecha_str.split("/")
            ym = f"{anio}-{mes.zfill(2)}"
            by_month_total[ym] += contifico_client._doc_total(d)
            by_month_count[ym] += 1
        except (ValueError, AttributeError):
            pass

    result = {
        ym: {"total": round(v, 2), "num_facturas": by_month_count[ym]}
        for ym, v in sorted(by_month_total.items())
    }
    _HISTORY_CACHE[months_back] = (now, result)
    return result


def forecast_baseline(target_year: int, target_month: int) -> dict[str, Any]:
    """Proyección baseline para un mes futuro o actual.

    Método: same-month-last-year × (1 + YoY growth promedio últimos 12 meses).
    Rango: pesimista/probable/optimista = ± 15% de la probable.

    Returns:
        {
            "target_period": "2026-06",
            "ventas_mismo_mes_anio_anterior": 38000.0,
            "yoy_growth_estimado_pct": 18.5,
            "proyeccion": {"pesimista": ..., "probable": ..., "optimista": ...},
            "metodo": "...",
            "historia_meses_usados": 24
        }
    """
    # Usamos 18 meses (no 24) — más rápido y suficiente para YoY decent
    history = historical_monthly_sales(months_back=18)
    sorted_months = sorted(history.keys())

    target_key = f"{target_year:04d}-{target_month:02d}"
    py_key = f"{target_year - 1:04d}-{target_month:02d}"

    py_sales = history.get(py_key, {}).get("total", 0)
    if py_sales == 0:
        return {
            "error": (
                f"No hay datos para el mismo mes del año anterior ({py_key}). "
                f"No puedo proyectar con el método baseline. Meses disponibles: "
                f"{sorted_months}"
            ),
            "target_period": target_key,
        }

    # YoY growth: si tenemos al menos 13 meses, comparar el mes target vs el
    # mes anterior PY (ej. mayo 2026 vs mayo 2025). Si tenemos los 6 últimos vs
    # los 6 anteriores, usamos eso como tendencia.
    if len(sorted_months) >= 18:
        recent_6 = sorted_months[-6:]
        prior_6 = sorted_months[-18:-12]
        recent_total = sum(history[m]["total"] for m in recent_6)
        prior_total = sum(history[m]["total"] for m in prior_6)
        if prior_total > 0:
            yoy_growth = (recent_total - prior_total) / prior_total
        else:
            yoy_growth = 0.20
    else:
        yoy_growth = 0.20

    # Clamp a un rango razonable
    yoy_growth = max(-0.30, min(yoy_growth, 0.60))

    probable = py_sales * (1 + yoy_growth)
    pesimista = probable * 0.85
    optimista = probable * 1.15

    return {
        "target_period": target_key,
        "ventas_mismo_mes_anio_anterior": round(py_sales, 2),
        "yoy_growth_estimado_pct": round(yoy_growth * 100, 1),
        "proyeccion": {
            "pesimista": round(pesimista, 2),
            "probable": round(probable, 2),
            "optimista": round(optimista, 2),
        },
        "metodo": (
            "same-month-last-year × (1 + YoY growth). YoY calculado de los "
            "últimos 12 meses vs los 12 anteriores. Rango ± 15%."
        ),
        "historia_meses_usados": len(sorted_months),
        "history_summary": {
            "primero": sorted_months[0] if sorted_months else None,
            "ultimo": sorted_months[-1] if sorted_months else None,
        },
    }


def product_mix_breakdown(
    keywords: list[str] | None = None,
    months_back: int = 6,
    top_n: int = 20,
) -> dict[str, Any]:
    """Top productos por facturación. Opcionalmente filtrá por keywords.

    Útil para responder preguntas como:
    - "¿qué % de mi mix son productos PLA?" → keywords=['PLA']
    - "¿qué % de mi facturación es plato importado?" → keywords=['plato', 'importado']
    - "¿top 10 productos del último semestre?" → keywords=None
    """
    end = date.today()
    start = end - timedelta(days=months_back * 30)
    docs = contifico_client._filter_validas(
        contifico_client.get_documentos(start, end)
    )

    by_producto: dict[str, dict[str, float]] = defaultdict(
        lambda: {"revenue": 0.0, "cantidad": 0.0}
    )
    total_all = 0.0
    for d in docs:
        for det in d.get("detalles", []) or []:
            nombre = (det.get("producto_nombre") or "").strip()
            if not nombre:
                continue
            try:
                cantidad = float(det.get("cantidad") or 0)
                precio = float(det.get("precio") or 0)
            except (ValueError, TypeError):
                continue
            revenue = cantidad * precio
            by_producto[nombre]["revenue"] += revenue
            by_producto[nombre]["cantidad"] += cantidad
            total_all += revenue

    if keywords:
        kw_lower = [k.lower() for k in keywords if k]
        filtered = {
            n: v for n, v in by_producto.items()
            if any(kw in n.lower() for kw in kw_lower)
        }
    else:
        filtered = dict(by_producto)

    sorted_prods = sorted(
        filtered.items(), key=lambda x: x[1]["revenue"], reverse=True
    )[:top_n]

    matched_total = sum(v["revenue"] for v in filtered.values())
    pct_of_total = (matched_total / total_all * 100) if total_all > 0 else 0.0

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "keywords_filter": keywords,
        "total_facturacion_periodo": round(total_all, 2),
        "matched_facturacion": round(matched_total, 2),
        "matched_pct_de_total": round(pct_of_total, 1),
        "productos_unicos_total": len(by_producto),
        "productos_matched": len(filtered),
        "top_productos": [
            {
                "producto": n,
                "revenue": round(v["revenue"], 2),
                "cantidad": round(v["cantidad"], 1),
                "pct_de_matched": round((v["revenue"] / matched_total * 100), 1)
                if matched_total else 0,
            }
            for n, v in sorted_prods
        ],
    }


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    cmd = sys.argv[1] if len(sys.argv) >= 2 else "help"

    if cmd == "history":
        n = int(sys.argv[2]) if len(sys.argv) >= 3 else 12
        print(json.dumps(historical_monthly_sales(months_back=n), indent=2, ensure_ascii=False))
    elif cmd == "forecast":
        # ej: python forecasting.py forecast 2026 6
        y, m = int(sys.argv[2]), int(sys.argv[3])
        print(json.dumps(forecast_baseline(y, m), indent=2, ensure_ascii=False))
    elif cmd == "mix":
        kw = sys.argv[2:] if len(sys.argv) >= 3 else None
        print(json.dumps(product_mix_breakdown(keywords=kw), indent=2, ensure_ascii=False))
    else:
        print("Comandos: history [N], forecast YYYY MM, mix [keyword1 keyword2 ...]")
