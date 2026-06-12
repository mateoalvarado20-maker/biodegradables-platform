"""Cliente Power BI para Azure Functions (app-only auth).

Ejecuta queries DAX contra el dataset Contifico vía REST API. Auth es
client_credentials con el mismo App Registration que el resto del Function App
(`biodegradables-azure-functions`).

REQUISITOS DE CONFIGURACIÓN EN POWER BI (vos los hacés UNA vez):
1. https://app.powerbi.com → gear/settings → Admin portal
2. Tenant settings → "Service principals can use Fabric APIs"
   → Enabled → "Apply to: Specific security groups"
   → Crear o usar grupo (ej. PBI-ServicePrincipals)
3. Agregar el service principal `biodegradables-azure-functions` a ese grupo
   vía Entra ID → Groups → ese grupo → Add member
4. En el workspace donde está el dataset Contifico → Manage access →
   Add → buscar `biodegradables-azure-functions` → rol "Member"

Hasta que esto esté hecho, las tools que usan PBI van a devolver un error
explicativo en lugar de crashear.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("pbi_query")

# Dataset Contifico (definido en CLAUDE.md). Si cambia, actualizar.
DATASET_ID = os.environ.get(
    "PBI_DATASET_ID", "5b04e54f-4c15-4c67-9fcf-a0aad424a17f"
)
WORKSPACE_ID = os.environ.get("PBI_WORKSPACE_ID", "")  # vacío = "Mi área de trabajo"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


class PBINotConfigured(Exception):
    """Indica que el service principal no tiene acceso a PBI todavía."""
    pass


def _get_pbi_token() -> str:
    now = time.time()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["token"]

    tenant = os.environ["GRAPH_TENANT_ID"]
    client_id = os.environ["GRAPH_CLIENT_ID"]
    client_secret = os.environ["GRAPH_CLIENT_SECRET"]

    r = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": PBI_SCOPE,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise PBINotConfigured(
            f"No pude obtener token PBI: {r.status_code}. "
            f"Probablemente el service principal aún no está habilitado en PBI Admin Portal. "
            f"Detalle: {r.text[:200]}"
        )
    data = r.json()
    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + data["expires_in"]
    return data["access_token"]


def execute_dax(dax: str) -> dict:
    """Ejecuta una query DAX y devuelve el JSON crudo de PBI."""
    token = _get_pbi_token()
    if WORKSPACE_ID:
        url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
            f"/datasets/{DATASET_ID}/executeQueries"
        )
    else:
        url = (
            f"https://api.powerbi.com/v1.0/myorg/datasets/{DATASET_ID}/executeQueries"
        )
    body = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    if r.status_code == 401:
        raise PBINotConfigured(
            "PBI rechazó la autenticación. El service principal puede que no esté "
            "agregado al workspace donde vive el dataset."
        )
    if r.status_code == 404:
        raise PBINotConfigured(
            f"Dataset {DATASET_ID} no encontrado o el service principal no tiene "
            f"acceso al workspace. Verificá en PBI: Workspace → Manage access → "
            f"agregar `biodegradables-azure-functions` como Member."
        )
    if r.status_code >= 400:
        raise RuntimeError(f"PBI executeQueries {r.status_code}: {r.text[:500]}")
    return r.json()


def dax_rows(response: dict) -> list[dict]:
    """Extrae las filas planas de un response de executeQueries."""
    try:
        return response["results"][0]["tables"][0]["rows"]
    except (KeyError, IndexError):
        return []


# ===== Queries DAX comunes (encapsuladas como funciones) =====
def cartera_total() -> dict:
    """Total de cartera (deuda viva total de clientes)."""
    dax = """EVALUATE ROW(
        "CarteraTotal", [Cartera Total],
        "CarteraVencida", [Cartera Vencida],
        "CarteraNoVencida", [Cartera No Vencida],
        "PctVencida", [% Cartera Vencida]
    )"""
    rows = dax_rows(execute_dax(dax))
    return rows[0] if rows else {}


def cartera_por_antiguedad() -> dict:
    """Cartera agrupada por buckets de antigüedad."""
    dax = """EVALUATE ROW(
        "Cartera_1_30", [Cartera 1-30 Días],
        "Cartera_31_60", [Cartera 31-60 Días],
        "Cartera_61_90", [Cartera 61-90 Días],
        "Cartera_mas_90", [Cartera +90 Días]
    )"""
    rows = dax_rows(execute_dax(dax))
    return rows[0] if rows else {}


def top_deudores(ciudad: str = "", limit: int = 10) -> list[dict]:
    """Top deudores opcionalmente filtrados por ciudad (UIO o GYE)."""
    filtro = ""
    if ciudad:
        c = ciudad.upper()
        if "UIO" in c or "QUITO" in c:
            filtro = ', FILTER(Cobranzas, SEARCH("UIO", Cobranzas[vendedor.direccion], 1, 0) > 0)'
        elif "GYE" in c or "GUAYAQUIL" in c:
            filtro = ', FILTER(Cobranzas, SEARCH("GYE", Cobranzas[vendedor.direccion], 1, 0) > 0)'
    dax = f"""EVALUATE
        TOPN({limit},
            SUMMARIZECOLUMNS(
                Cobranzas[persona.razon_social],
                "Deuda", [Deuda Vencida por Cliente]
                {filtro}
            ),
            [Deuda], DESC
        )
        ORDER BY [Deuda] DESC
    """
    return dax_rows(execute_dax(dax))


def cumplimiento_mes() -> dict:
    """Cumplimiento de meta del mes en curso."""
    dax = """EVALUATE ROW(
        "VentasMTD", [Ventas MTD],
        "Meta", [Meta Mensual],
        "Cumplimiento", [Cumplimiento %],
        "Brecha", [Brecha Meta],
        "RitmoNecesario", [Ritmo Diario Necesario]
    )"""
    rows = dax_rows(execute_dax(dax))
    return rows[0] if rows else {}


def last_refresh() -> str | None:
    """Devuelve ISO timestamp del último refresh exitoso del dataset, o None."""
    try:
        token = _get_pbi_token()
        if WORKSPACE_ID:
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/datasets/{DATASET_ID}/refreshes?$top=5"
        else:
            url = f"https://api.powerbi.com/v1.0/myorg/datasets/{DATASET_ID}/refreshes?$top=5"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if r.status_code >= 400:
            return None
        for ref in r.json().get("value", []):
            if ref.get("status") == "Completed":
                return ref.get("endTime") or ref.get("startTime")
    except Exception:
        return None
    return None
