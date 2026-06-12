"""Cliente HubSpot REST API para el reporte diario.

Consulta contactos (leads) y deals (ventas) directamente vía HubSpot Private App.

Setup:
1. En HubSpot: Settings → Integrations → Private Apps → Create
2. Scopes: crm.objects.contacts.read + crm.objects.deals.read
3. Copia el access token y configúralo como env var HUBSPOT_TOKEN

Token Private Apps: https://developers.hubspot.com/docs/api/private-apps
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

HS_BASE = "https://api.hubapi.com"
TOKEN = os.environ.get("HUBSPOT_TOKEN", "")


def _headers() -> dict[str, str]:
    if not TOKEN:
        raise RuntimeError(
            "Falta HUBSPOT_TOKEN en variables de entorno. "
            "Crea un Private App en HubSpot y configura el token."
        )
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }


def _iso_utc(d: date | datetime) -> str:
    """Devuelve ISO 8601 en UTC para usar en filtros de HubSpot."""
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # date a las 00:00 UTC
    return d.strftime("%Y-%m-%dT00:00:00Z")


def search_objects(
    object_type: str,
    properties: list[str],
    filters: list[dict],
    sorts: list[dict] | None = None,
    limit: int = 100,
) -> dict:
    """Busca objetos en HubSpot. Devuelve el JSON de respuesta."""
    body: dict[str, Any] = {
        "properties": properties,
        "filterGroups": [{"filters": filters}],
        "limit": limit,
    }
    if sorts:
        body["sorts"] = sorts
    r = httpx.post(
        f"{HS_BASE}/crm/v3/objects/{object_type}/search",
        headers=_headers(),
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HubSpot {object_type} search → {r.status_code}: {r.text[:300]}")
    return r.json()


# ===== KPIs de Marketing / Comercial =====
def _local_yesterday_range() -> tuple[str, str]:
    """Rango UTC que cubre 'ayer' en hora Ecuador (UTC-5)."""
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)
    ayer_ec = hoy_ec - timedelta(days=1)
    return _iso_utc(ayer_ec), _iso_utc(hoy_ec)


def _last_7d_range() -> tuple[str, str]:
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)
    hace_7 = hoy_ec - timedelta(days=7)
    return _iso_utc(hace_7), _iso_utc(hoy_ec)


def leads_ayer() -> dict:
    """Cuántos contactos nuevos se crearon ayer y cuál fue la fuente top."""
    start, end = _local_yesterday_range()
    data = search_objects(
        "contacts",
        properties=["createdate", "hs_analytics_source", "hs_analytics_source_data_1"],
        filters=[
            {"propertyName": "createdate", "operator": "GTE", "value": start},
            {"propertyName": "createdate", "operator": "LT", "value": end},
        ],
        limit=100,
    )
    total = data.get("total", 0)
    results = data.get("results", [])
    # Contar fuentes
    by_source: dict[str, int] = {}
    for c in results:
        src = c.get("properties", {}).get("hs_analytics_source") or "UNKNOWN"
        by_source[src] = by_source.get(src, 0) + 1
    top_source = max(by_source.items(), key=lambda x: x[1]) if by_source else (None, 0)
    return {
        "total": total,
        "top_source": top_source[0],
        "top_source_count": top_source[1],
        "by_source": by_source,
    }


def leads_promedio_7d() -> float:
    """Promedio diario de contactos nuevos en los últimos 7 días."""
    start, end = _last_7d_range()
    data = search_objects(
        "contacts",
        properties=["createdate"],
        filters=[
            {"propertyName": "createdate", "operator": "GTE", "value": start},
            {"propertyName": "createdate", "operator": "LT", "value": end},
        ],
        limit=1,
    )
    return float(data.get("total", 0)) / 7.0


def deals_ganados_ayer() -> dict:
    """Deals que se cerraron ganados ayer (closedate) + revenue."""
    start, end = _local_yesterday_range()
    data = search_objects(
        "deals",
        properties=["amount", "deal_currency_code", "closedate", "hs_is_closed_won"],
        filters=[
            {"propertyName": "closedate", "operator": "GTE", "value": start},
            {"propertyName": "closedate", "operator": "LT", "value": end},
            {"propertyName": "hs_is_closed_won", "operator": "EQ", "value": "true"},
        ],
        limit=200,
    )
    results = data.get("results", [])
    total_count = data.get("total", 0)
    revenue = 0.0
    for d in results:
        amt = d.get("properties", {}).get("amount")
        if amt:
            try:
                revenue += float(amt)
            except (TypeError, ValueError):
                pass
    return {"count": total_count, "revenue": revenue}


def leads_por_dia_ultimos_7d() -> list[dict]:
    """Devuelve una lista de {fecha, count} para cada uno de los últimos 7 días
    (incluido hoy). Ordenada del más antiguo al más reciente."""
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)

    # Trae todos los contactos de los últimos 7 días (incluido hoy)
    start = hoy_ec - timedelta(days=6)
    end = hoy_ec + timedelta(days=1)
    data = search_objects(
        "contacts",
        properties=["createdate"],
        filters=[
            {"propertyName": "createdate", "operator": "GTE", "value": _iso_utc(start)},
            {"propertyName": "createdate", "operator": "LT", "value": _iso_utc(end)},
        ],
        limit=200,  # asume <200 leads en 7 días — típico para Biodegradables
    )

    # Bucketizar por día (en hora Ecuador)
    counts: dict[str, int] = {}
    for c in data.get("results", []):
        cd = c.get("properties", {}).get("createdate")
        if not cd:
            continue
        try:
            utc = datetime.fromisoformat(cd.replace("Z", "+00:00"))
            local = utc.astimezone(ec)
            key = local.strftime("%Y-%m-%d")
            counts[key] = counts.get(key, 0) + 1
        except Exception:
            continue

    # Llenar todos los 7 días, incluso si tienen 0
    result = []
    for i in range(7):
        d = (start + timedelta(days=i)).date()
        key = d.strftime("%Y-%m-%d")
        result.append({"fecha": key, "fecha_obj": d, "count": counts.get(key, 0)})
    return result


def pipeline_abierto() -> dict:
    """Deals no cerrados todavía + valor total."""
    data = search_objects(
        "deals",
        properties=["amount", "deal_currency_code", "dealstage", "hs_is_closed"],
        filters=[
            {"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"},
        ],
        limit=200,
    )
    results = data.get("results", [])
    total_count = data.get("total", 0)
    valor = 0.0
    for d in results:
        amt = d.get("properties", {}).get("amount")
        if amt:
            try:
                valor += float(amt)
            except (TypeError, ValueError):
                pass
    return {"count": total_count, "valor": valor}


def deals_stuck(dias_min: int = 14) -> dict:
    """Deals abiertos sin movimiento (hs_lastmodifieddate) en los últimos N días.

    Devuelve count, valor total y top 5 por monto.
    """
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = hoy_ec - timedelta(days=dias_min)
    data = search_objects(
        "deals",
        properties=[
            "amount", "dealname", "dealstage", "hs_is_closed",
            "hs_lastmodifieddate", "closedate",
        ],
        filters=[
            {"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"},
            {"propertyName": "hs_lastmodifieddate", "operator": "LT", "value": _iso_utc(cutoff)},
        ],
        sorts=[{"propertyName": "amount", "direction": "DESCENDING"}],
        limit=100,
    )
    results = data.get("results", [])
    total_count = data.get("total", 0)
    valor = 0.0
    top: list[dict] = []
    for d in results:
        props = d.get("properties", {})
        amt_raw = props.get("amount")
        try:
            amt = float(amt_raw) if amt_raw else 0.0
        except (TypeError, ValueError):
            amt = 0.0
        valor += amt
        ult_mod = props.get("hs_lastmodifieddate") or ""
        # Calcular días sin movimiento
        dias_sin_mov = None
        try:
            mod_utc = datetime.fromisoformat(ult_mod.replace("Z", "+00:00"))
            dias_sin_mov = (datetime.now(timezone.utc) - mod_utc).days
        except Exception:
            pass
        if len(top) < 5:
            top.append({
                "id": d.get("id"),
                "nombre": props.get("dealname") or "(sin nombre)",
                "monto": amt,
                "dias_sin_movimiento": dias_sin_mov,
            })
    return {
        "dias_min": dias_min,
        "count": total_count,
        "valor": valor,
        "top": top,
    }


def deals_won_30d() -> dict:
    """Deals ganados (closed won) en los últimos 30 días."""
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)
    start = hoy_ec - timedelta(days=30)
    data = search_objects(
        "deals",
        properties=["amount", "closedate", "hs_is_closed_won"],
        filters=[
            {"propertyName": "closedate", "operator": "GTE", "value": _iso_utc(start)},
            {"propertyName": "closedate", "operator": "LT", "value": _iso_utc(hoy_ec)},
            {"propertyName": "hs_is_closed_won", "operator": "EQ", "value": "true"},
        ],
        limit=200,
    )
    results = data.get("results", [])
    revenue = 0.0
    for d in results:
        amt = d.get("properties", {}).get("amount")
        if amt:
            try:
                revenue += float(amt)
            except (TypeError, ValueError):
                pass
    return {"count": data.get("total", 0), "revenue": revenue}


def deals_lost_30d() -> dict:
    """Deals perdidos (closed lost) en los últimos 30 días."""
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec).replace(hour=0, minute=0, second=0, microsecond=0)
    start = hoy_ec - timedelta(days=30)
    data = search_objects(
        "deals",
        properties=["amount", "closedate", "hs_is_closed_won", "hs_is_closed"],
        filters=[
            {"propertyName": "closedate", "operator": "GTE", "value": _iso_utc(start)},
            {"propertyName": "closedate", "operator": "LT", "value": _iso_utc(hoy_ec)},
            {"propertyName": "hs_is_closed", "operator": "EQ", "value": "true"},
            {"propertyName": "hs_is_closed_won", "operator": "EQ", "value": "false"},
        ],
        limit=200,
    )
    return {"count": data.get("total", 0)}


def conversion_rate_30d() -> dict:
    """Tasa de cierre = ganados / (ganados + perdidos) en últimos 30 días."""
    won = deals_won_30d()
    lost = deals_lost_30d()
    total_cerrados = won["count"] + lost["count"]
    rate = won["count"] / total_cerrados if total_cerrados > 0 else None
    return {
        "ganados": won["count"],
        "perdidos": lost["count"],
        "cerrados_total": total_cerrados,
        "tasa_cierre": rate,
        "revenue_30d": won["revenue"],
    }


def leads_sin_responder(horas_min: int = 24) -> dict:
    """Leads creados hace más de N horas que siguen sin contacto.

    Heurística: si lifecyclestage == 'lead' o 'subscriber' (no 'opportunity' ni
    'customer'), y el contacto no tiene actividad reciente, lo marcamos como
    'sin responder'. Esto requiere que el equipo use HubSpot.

    NOTA: depende de las propiedades disponibles. Si tu instancia no tiene
    hs_last_sales_activity_timestamp, devuelve 0 con un warning.
    """
    ec = timezone(timedelta(hours=-5))
    hoy_ec = datetime.now(ec)
    cutoff_creacion = hoy_ec - timedelta(hours=horas_min)
    cutoff_actividad = hoy_ec - timedelta(hours=horas_min)
    try:
        data = search_objects(
            "contacts",
            properties=[
                "firstname", "lastname", "email", "createdate",
                "lifecyclestage", "hs_last_sales_activity_timestamp",
            ],
            filters=[
                {"propertyName": "createdate", "operator": "LT", "value": _iso_utc(cutoff_creacion)},
                # No tiene actividad reciente
                {"propertyName": "hs_last_sales_activity_timestamp", "operator": "LT", "value": _iso_utc(cutoff_actividad)},
                # Sigue siendo lead (no convertido aún)
                {"propertyName": "lifecyclestage", "operator": "IN", "values": ["lead", "subscriber", "marketingqualifiedlead"]},
            ],
            limit=20,
        )
        return {
            "count": data.get("total", 0),
            "leads": [
                {
                    "nombre": " ".join(filter(None, [
                        c.get("properties", {}).get("firstname"),
                        c.get("properties", {}).get("lastname"),
                    ])) or "(sin nombre)",
                    "email": c.get("properties", {}).get("email"),
                    "created": c.get("properties", {}).get("createdate"),
                }
                for c in data.get("results", [])[:5]
            ],
        }
    except Exception:
        return {"count": 0, "leads": [], "_warning": "no-actividad-disponible"}


if __name__ == "__main__":
    # Smoke test
    print("=== Leads ayer ===")
    print(leads_ayer())
    print("\n=== Promedio leads 7d ===")
    print(f"{leads_promedio_7d():.1f}/día")
    print("\n=== Deals ganados ayer ===")
    print(deals_ganados_ayer())
    print("\n=== Pipeline abierto ===")
    print(pipeline_abierto())
