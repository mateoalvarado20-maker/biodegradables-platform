"""Lee las condiciones de crédito (días por cliente) desde el Excel compartido
en SharePoint, vía Microsoft Graph (Workbook API, app-only).

Fuente de verdad mantenida por el equipo comercial:
  OneDrive de malvarado → CondicionesCredito.xlsx (el del link que compartió el
  usuario). OJO (2026-06-18): este archivo tiene DOS hojas separadas por ciudad,
  'GYE' y 'UIO' (no una sola 'Hoja1'); por eso se leen TODAS las hojas. Existe
  otra copia homónima en el sitio MateoAlvarado que NO es la que el equipo edita.
Columnas por hoja: CLIENTE CON CREDITO | ENVIOS GRATIS | CUPO | DÍAS DE CREDITO | Ciudad

El bot (App Service) usa el token app-only de `graph_mail` (app
biodegradables-data-bot, permiso Files.Read.All con admin consent — 2026-06-17).

Diseño:
- El match con Contifico es por RAZÓN SOCIAL normalizada SIN acentos (arregla el
  caso "COMPAÑIA"/"COMPANIA") + un mapa de ALIAS para nombres que en Contifico
  están en otro orden. El Excel no trae RUC, por eso se empareja por nombre.
- Si la lectura de SharePoint falla (sin permiso, red, archivo movido), el
  llamador cae al último JSON bueno (`condiciones_credito.json`). Nunca rompe.
"""
from __future__ import annotations

import os
import unicodedata
from typing import Any

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEOUT = 60

# Coordenadas del archivo en SharePoint (override por env si algún día se mueve).
# Resueltas desde el link que compartió el usuario (OneDrive de malvarado) vía
# Graph /shares — NO confundir con la copia homónima del sitio MateoAlvarado.
DRIVE_ID = os.environ.get(
    "CONDICIONES_DRIVE_ID",
    "b!nfaPkpAeKkKav87S3icudNyuYgCWhxBCvDcvbq71GqVphPNYm4uVQZTrVRyJ1oHc",
)
ITEM_ID = os.environ.get(
    "CONDICIONES_ITEM_ID", "01JOGP6KBPLY452C7PMRFYJR6XIBEXJKXJ"
)

# Alias: nombre normalizado del Excel -> razón social real en Contifico.
# Confirmados contra Contifico (con RUC) el 2026-06-16.
ALIAS_EXCEL_A_CONTIFICO = {
    "EVELYN MORALES SOLORZANO": "MORALES SOLORZANO EVELYN PATRICIA",  # RUC 0914038856001
    "TANIA SAS": "TANIA S.A.S.",                                      # RUC 0993370890001
}


def normaliza_nombre(s: str | None) -> str:
    """UPPER + sin acentos + espacios colapsados. Para match insensible a
    acentos/mayúsculas (arregla 'COMPAÑIA' vs 'COMPANIA')."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def _col_index(header: list[str]) -> dict[str, int]:
    """Ubica las columnas por nombre de encabezado (robusto a reordenamientos)."""
    idx = {}
    for i, h in enumerate(header):
        hn = normaliza_nombre(h)
        if "CLIENTE" in hn:
            idx["nombre"] = i
        elif "DIAS DE CREDITO" in hn or hn == "DIAS":
            idx["dias"] = i
        elif "CIUDAD" in hn:
            idx["ciudad"] = i
    return idx


def fetch_desde_sharepoint() -> list[dict[str, Any]] | None:
    """Lee el Excel (TODAS sus hojas) y devuelve [{"nombre","plazo_dias","ciudad"}],
    con el nombre ya mapeado a la razón social de Contifico (vía ALIAS). Devuelve
    None ante cualquier fallo (sin permiso, red, formato inesperado).

    El archivo tiene una hoja por ciudad (GYE, UIO); se recorren todas y se omite
    cualquier hoja que no tenga las columnas esperadas. Dedup por nombre."""
    try:
        import graph_mail
        token = graph_mail._get_token()
    except Exception:
        return None

    h = {"Authorization": f"Bearer {token}"}
    base = f"{GRAPH_BASE}/drives/{DRIVE_ID}/items/{ITEM_ID}"

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            rh = client.get(f"{base}/workbook/worksheets?$select=name", headers=h)
        if rh.status_code >= 400:
            return None
        hojas = [w.get("name") for w in rh.json().get("value", []) if w.get("name")]
    except (httpx.RequestError, ValueError):
        return None
    if not hojas:
        return None

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hoja in hojas:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.get(
                    f"{base}/workbook/worksheets('{hoja}')/usedRange?$select=text",
                    headers=h,
                )
            if r.status_code >= 400:
                continue
            rows = r.json().get("text", [])
        except (httpx.RequestError, ValueError):
            continue
        if not rows or len(rows) < 2:
            continue
        cols = _col_index(rows[0])
        if "nombre" not in cols or "dias" not in cols:
            continue  # hoja sin las columnas esperadas → se omite

        for row in rows[1:]:
            try:
                nombre = (row[cols["nombre"]] or "").strip()
                dias_raw = (row[cols["dias"]] or "").strip()
                ciudad = (row[cols["ciudad"]].strip().upper()
                          if "ciudad" in cols and cols["ciudad"] < len(row) else "")
            except (IndexError, AttributeError):
                continue
            if not nombre:
                continue
            try:
                plazo = int(float(dias_raw))
            except (ValueError, TypeError):
                continue  # sin días válidos = no se considera (no rompe)
            nombre_final = ALIAS_EXCEL_A_CONTIFICO.get(normaliza_nombre(nombre), nombre)
            key = normaliza_nombre(nombre_final)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "nombre": nombre_final,
                "plazo_dias": plazo,
                "ciudad": ciudad or hoja.strip().upper(),
            })

    return out or None
