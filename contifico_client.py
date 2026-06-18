"""Cliente REST API de Contifico POS.

Doc oficial: https://api.contifico.com/sistema/api/v1/documentacion/

Auth: header `Authorization: <API_KEY>` (sin "Bearer"). El token se lee de la
variable de entorno `CONTIFICO_API_TOKEN`.

Endpoints usados:
    GET /api/v1/documento/?tipo=FAC&fecha_inicial=DD/MM/YYYY&fecha_final=DD/MM/YYYY

La respuesta incluye `persona` (con `direccion`, `razon_social`), `vendedor`
(con `razon_social`) y `detalles[]` (con `producto_nombre`, `precio`,
`cantidad`). Contifico NO expone provincia ni dirección de despacho separada
— solo la dirección del cliente.

Campos clave del documento FAC:
    - `fecha_emision` (str DD/MM/YYYY)
    - `documento` (str ej. "001-002-000012566") — prefijo 001-001=GYE, 001-002=UIO
    - `total` (float) → con IVA incluido
    - `subtotal` (float) → sin IVA
    - `saldo` (float) → 0 si cobrada, >0 si pendiente
    - `anulado` (bool) → True = anulada, excluir de totales
    - `vendedor.razon_social` → nombre del vendedor
    - `persona.razon_social` → nombre del cliente

Las funciones de alto nivel (`ventas_dia`, `top_vendedores_dia`, etc.) se usan
desde `ask_agent.py` como herramientas de Claude.
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import httpx

API_BASE = "https://api.contifico.com/sistema"
DEFAULT_TIMEOUT = 60
PAGE_SIZE = 200
MAX_RETRIES = 3  # intentos por página ante timeouts/errores de transporte transitorios

# Cache process-scoped de respuestas de /documento/.
# El daily_report invoca varias funciones que comparten el mismo rango (ej.
# cartera_kpis, antiguedad, top_deudores_ciudad UIO+GYE — los 4 con ventana
# 12 meses). Sin cache eso son 4× la misma llamada paginada. Con cache, una.
# Key = (fecha_inicial, fecha_final, tipo, tipo_registro).
# Fase 5 (auditoría R7): entradas con TIMESTAMP + TTL — este módulo también
# vive en el bot (proceso permanente) donde el comentario original ("Azure
# Function corta cada invocación") era falso: el Data Bot respondía a las
# 17:00 con datos cacheados de las 9:00 mientras prometía "en vivo".
_DOCS_CACHE: dict[tuple, tuple[float, list[dict[str, Any]]]] = {}
CACHE_TTL_SECONDS = int(os.environ.get("CONTIFICO_CACHE_TTL", "600"))  # 10 min

# Prefijos de documento por sucursal de emisión.
# Fuente: CLAUDE.md Issue #11.
PREFIJO_GYE = "001-001"
PREFIJO_UIO = "001-002"

# ===== Feriados + override PY: centralizados en core_config (Fase 5) =====
# Antes este dict estaba duplicado a mano con daily_report.py ("mantener
# sincronizado") — y el PY_OVERRIDE estaba keyed por mes sin año.
import core_config
EC_HOLIDAYS = core_config.EC_HOLIDAYS  # alias legacy
PY_OVERRIDE = core_config.PY_OVERRIDE  # alias legacy (key = (año, mes))


# ============ Bajo nivel: HTTP + paginación ============
def _token() -> str:
    t = os.environ.get("CONTIFICO_API_TOKEN", "").strip()
    if not t:
        raise RuntimeError(
            "Falta CONTIFICO_API_TOKEN. Setear con: "
            "setx CONTIFICO_API_TOKEN <api_key>"
        )
    return t


def _fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def get_documentos(
    fecha_inicial: date,
    fecha_final: date,
    *,
    tipo: str = "FAC",
    tipo_registro: str = "CLI",
) -> list[dict[str, Any]]:
    """Lista todos los documentos en el rango de fechas (paginado).

    Args:
        fecha_inicial: filtro inferior por fecha_emision (inclusivo).
        fecha_final: filtro superior (inclusivo).
        tipo: FAC=Factura, LQC=Liquidación, NCT=Nota de Crédito, etc.
        tipo_registro: CLI=Cliente, PRO=Proveedor.

    Devuelve la lista completa con `detalles[]` y `persona` embebidos.
    """
    cache_key = (fecha_inicial, fecha_final, tipo, tipo_registro)
    cached = _DOCS_CACHE.get(cache_key)
    if cached is not None:
        cached_at, payload = cached
        if time.time() - cached_at < CACHE_TTL_SECONDS:
            return payload
        del _DOCS_CACHE[cache_key]  # expirado

    token = _token()
    out: list[dict[str, Any]] = []
    page = 1
    base_params = {
        "tipo": tipo,
        "tipo_registro": tipo_registro,
        "fecha_inicial": _fmt_date(fecha_inicial),
        "fecha_final": _fmt_date(fecha_final),
        "result_size": PAGE_SIZE,
    }
    # Fix 2026-06-16: timeouts transitorios de Contifico (ReadTimeout) tumbaban
    # toda la consulta de cartera → tarjetas en blanco en el correo. Ahora cada
    # página se reintenta con backoff antes de fallar, y el read timeout es más
    # holgado (las páginas de un año de facturas pueden tardar).
    timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=60.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            params = {**base_params, "result_page": page}
            r = None
            for intento in range(1, MAX_RETRIES + 1):
                try:
                    r = client.get(
                        f"{API_BASE}/api/v1/documento/",
                        params=params,
                        headers={"Authorization": token},
                    )
                    break
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    if intento >= MAX_RETRIES:
                        raise
                    print(
                        f"[contifico_client] reintento {intento}/{MAX_RETRIES - 1} "
                        f"(página {page}) tras {type(e).__name__}: {e}",
                        file=sys.stderr,
                    )
                    time.sleep(2 * intento)
            if r.status_code >= 400:
                raise RuntimeError(
                    f"Contifico GET /documento/ → {r.status_code}: {r.text[:500]}"
                )
            data = r.json()
            batch = data if isinstance(data, list) else data.get("results", [])
            if not batch:
                break
            out.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
            if page > 100:  # bump 50→100 (Phase H) — 24 meses de hist puede pasar 50 pages
                # Fase 5 (auditoría R4): el truncamiento ya no es silencioso.
                print(
                    f"[contifico_client] ⚠️ TRUNCADO en {page - 1} páginas "
                    f"({len(out)} docs) para rango {fecha_inicial}–{fecha_final} "
                    "— los totales pueden estar subestimados.",
                    file=sys.stderr,
                )
                break
    _DOCS_CACHE[cache_key] = (time.time(), out)
    return out


# ============ Helpers de filtrado y agrupación ============
def _doc_total(d: dict[str, Any]) -> float:
    """Total del documento (con IVA), 0 si anulado o sin valor."""
    if d.get("anulado"):
        return 0.0
    try:
        return float(d.get("total") or 0)
    except (TypeError, ValueError):
        return 0.0


def _doc_subtotal(d: dict[str, Any]) -> float:
    """Subtotal sin IVA."""
    if d.get("anulado"):
        return 0.0
    try:
        return float(d.get("subtotal") or 0)
    except (TypeError, ValueError):
        return 0.0


def _doc_saldo(d: dict[str, Any]) -> float:
    """Saldo pendiente del documento (0 si cobrado)."""
    if d.get("anulado"):
        return 0.0
    try:
        return float(d.get("saldo") or 0)
    except (TypeError, ValueError):
        return 0.0


def _doc_ciudad(d: dict[str, Any]) -> str:
    """Devuelve 'UIO', 'GYE' o '?' según el prefijo del documento."""
    doc = (d.get("documento") or "").strip()
    if doc.startswith(PREFIJO_UIO):
        return "UIO"
    if doc.startswith(PREFIJO_GYE):
        return "GYE"
    return "?"


def _doc_vendedor_nombre(d: dict[str, Any]) -> str:
    vend = d.get("vendedor") or {}
    if isinstance(vend, dict):
        return (vend.get("razon_social") or "").strip() or "Sin vendedor"
    return "Sin vendedor"


def _doc_cliente_nombre(d: dict[str, Any]) -> str:
    pers = d.get("persona") or {}
    if isinstance(pers, dict):
        return (pers.get("razon_social") or "").strip() or "Sin cliente"
    return "Sin cliente"


def _filter_validas(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Solo facturas vigentes (no anuladas)."""
    return [d for d in docs if not d.get("anulado")]


# ============ Días hábiles (replica daily_report.py) ============
def _is_workday(d: date) -> bool:
    if d.weekday() == 6:  # domingo
        return False
    if d in core_config.holidays_for(d.year):
        return False
    return True


def _workdays_in_range(start: date, end: date) -> int:
    if start > end:
        return 0
    cur, count = start, 0
    while cur <= end:
        if _is_workday(cur):
            count += 1
        cur += timedelta(days=1)
    return count


def _workdays_in_month(year: int, month: int) -> int:
    from calendar import monthrange
    _, last_day = monthrange(year, month)
    return _workdays_in_range(date(year, month, 1), date(year, month, last_day))


def _workdays_passed(today: date) -> int:
    return _workdays_in_range(date(today.year, today.month, 1), today)


# ============ Funciones de alto nivel para el bot ============
def ventas_dia(fecha: date) -> dict[str, Any]:
    """Ventas de un día específico.

    Devuelve:
        {
            "fecha": "2026-05-28",
            "total": 1234.56,
            "subtotal": 1102.29,
            "num_facturas": 29,
            "ticket_promedio": 42.57,
            "clientes_unicos": 18,
        }
    """
    docs = _filter_validas(get_documentos(fecha, fecha))
    if not docs:
        return {
            "fecha": fecha.isoformat(),
            "total": 0.0, "subtotal": 0.0,
            "num_facturas": 0, "ticket_promedio": 0.0,
            "clientes_unicos": 0,
        }
    total = sum(_doc_total(d) for d in docs)
    subtotal = sum(_doc_subtotal(d) for d in docs)
    clientes = {(d.get("persona") or {}).get("id") for d in docs}
    return {
        "fecha": fecha.isoformat(),
        "total": round(total, 2),
        "subtotal": round(subtotal, 2),
        "num_facturas": len(docs),
        "ticket_promedio": round(total / len(docs), 2) if docs else 0.0,
        "clientes_unicos": len({c for c in clientes if c}),
    }


def ventas_rango(fecha_inicial: date, fecha_final: date) -> dict[str, Any]:
    """Ventas en un rango de fechas (ej. MTD, semana, etc.)."""
    docs = _filter_validas(get_documentos(fecha_inicial, fecha_final))
    if not docs:
        return {
            "fecha_inicial": fecha_inicial.isoformat(),
            "fecha_final": fecha_final.isoformat(),
            "total": 0.0, "subtotal": 0.0,
            "num_facturas": 0, "ticket_promedio": 0.0,
            "clientes_unicos": 0,
        }
    total = sum(_doc_total(d) for d in docs)
    subtotal = sum(_doc_subtotal(d) for d in docs)
    clientes = {(d.get("persona") or {}).get("id") for d in docs}
    return {
        "fecha_inicial": fecha_inicial.isoformat(),
        "fecha_final": fecha_final.isoformat(),
        "total": round(total, 2),
        "subtotal": round(subtotal, 2),
        "num_facturas": len(docs),
        "ticket_promedio": round(total / len(docs), 2) if docs else 0.0,
        "clientes_unicos": len({c for c in clientes if c}),
    }


def ventas_por_ciudad(fecha: date, fecha_final: date | None = None) -> dict[str, Any]:
    """Ventas separadas por ciudad (UIO vs GYE) en un día o rango."""
    fecha_final = fecha_final or fecha
    docs = _filter_validas(get_documentos(fecha, fecha_final))
    agg: dict[str, dict[str, Any]] = {
        "UIO": {"ciudad": "Quito", "total": 0.0, "num_facturas": 0},
        "GYE": {"ciudad": "Guayaquil", "total": 0.0, "num_facturas": 0},
        "?":   {"ciudad": "Sin identificar", "total": 0.0, "num_facturas": 0},
    }
    for d in docs:
        c = _doc_ciudad(d)
        agg[c]["total"] += _doc_total(d)
        agg[c]["num_facturas"] += 1
    for k in agg:
        agg[k]["total"] = round(agg[k]["total"], 2)
    return {
        "fecha_inicial": fecha.isoformat(),
        "fecha_final": fecha_final.isoformat(),
        "por_ciudad": agg,
    }


def top_vendedores(
    fecha_inicial: date, fecha_final: date, n: int = 5
) -> list[dict[str, Any]]:
    """Top N vendedores por monto vendido en el rango."""
    docs = _filter_validas(get_documentos(fecha_inicial, fecha_final))
    by_vend: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"vendedor": "", "total": 0.0, "num_facturas": 0}
    )
    for d in docs:
        v = _doc_vendedor_nombre(d)
        by_vend[v]["vendedor"] = v
        by_vend[v]["total"] += _doc_total(d)
        by_vend[v]["num_facturas"] += 1
    rows = sorted(by_vend.values(), key=lambda r: r["total"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
    return rows[:n]


def top_clientes(
    fecha_inicial: date, fecha_final: date, n: int = 10
) -> list[dict[str, Any]]:
    """Top N clientes por monto comprado en el rango."""
    docs = _filter_validas(get_documentos(fecha_inicial, fecha_final))
    by_cli: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cliente": "", "total": 0.0, "num_facturas": 0}
    )
    for d in docs:
        c = _doc_cliente_nombre(d)
        by_cli[c]["cliente"] = c
        by_cli[c]["total"] += _doc_total(d)
        by_cli[c]["num_facturas"] += 1
    rows = sorted(by_cli.values(), key=lambda r: r["total"], reverse=True)
    for r in rows:
        r["total"] = round(r["total"], 2)
    return rows[:n]


def cumplimiento_mes(fecha_referencia: date | None = None) -> dict[str, Any]:
    """Calcula MTD, meta, brecha, % cumplimiento para el mes actual.

    Meta = ventas del mismo mes año anterior × 1.20 (overridable por
    PY_OVERRIDE para meses donde Contifico difiere de PBI).

    Replica la lógica de `daily_report._recalcular_python`.
    """
    today = fecha_referencia or date.today()
    year, month = today.year, today.month

    # Ventas MTD (mes actual hasta hoy)
    primer_dia_mes = date(year, month, 1)
    docs_mtd = _filter_validas(get_documentos(primer_dia_mes, today))
    mtd = sum(_doc_total(d) for d in docs_mtd)

    # Ventas mismo mes año anterior (PY)
    primer_dia_py = date(year - 1, month, 1)
    from calendar import monthrange
    _, last_day_py = monthrange(year - 1, month)
    ultimo_dia_py = date(year - 1, month, last_day_py)
    py_dax = core_config.py_override_for(year, month)
    if py_dax is None:
        docs_py = _filter_validas(get_documentos(primer_dia_py, ultimo_dia_py))
        py = sum(_doc_total(d) for d in docs_py)
    else:
        py = py_dax

    # Días hábiles
    wd_total = _workdays_in_month(year, month)
    wd_passed = _workdays_passed(today)
    wd_rest = wd_total - wd_passed
    if wd_total == 0:
        wd_total = 1  # safety

    meta = py * 1.20
    meta_diaria_base = meta / wd_total
    meta_esp_hoy = meta_diaria_base * wd_passed
    brecha = meta - mtd
    cumpl_mes = (mtd / meta) if meta else None
    cumpl_hoy = (mtd / meta_esp_hoy) if meta_esp_hoy else None
    meta_dia_restante = (max(brecha, 0) / wd_rest) if wd_rest else 0.0

    return {
        "fecha": today.isoformat(),
        "mes": month,
        "anio": year,
        "ventas_mtd": round(mtd, 2),
        "ventas_mismo_mes_anio_anterior": round(py, 2),
        "meta_mes": round(meta, 2),
        "meta_esperada_hoy": round(meta_esp_hoy, 2),
        "brecha": round(brecha, 2),
        "cumplimiento_mes_pct": round(cumpl_mes * 100, 1) if cumpl_mes is not None else None,
        "cumplimiento_hoy_pct": round(cumpl_hoy * 100, 1) if cumpl_hoy is not None else None,
        "meta_diaria_base": round(meta_diaria_base, 2),
        "meta_diaria_restante": round(meta_dia_restante, 2),
        "dias_habiles_pasados": wd_passed,
        "dias_habiles_restantes": wd_rest,
        "dias_habiles_total_mes": wd_total,
    }


def _parse_fecha_vencimiento(s: str | None) -> date | None:
    """Parsea 'DD/MM/YYYY' a date. None si no parsea."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


# ============ Plazos de crédito por cliente ============
# Fuente de verdad (2026-06-17): Excel compartido en SharePoint, leído vía
# credito_excel.fetch_desde_sharepoint(). Si falla (sin permiso/red), cae al
# último JSON bueno (condiciones_credito.json). Cliente que NO está en la lista
# = sin crédito (contado) y NO aparece como cobranza.
# Match por nombre normalizado SIN acentos (ver credito_excel.normaliza_nombre).
_CONDICIONES_CACHE: dict[str, Any] | None = None
_CONDICIONES_CACHE_AT: float = 0.0
CONDICIONES_TTL = int(os.environ.get("CONDICIONES_TTL", "600"))  # 10 min


def _condiciones_json_path():
    from pathlib import Path as _Path
    return _Path(__file__).parent / "condiciones_credito.json"


def _persistir_condiciones(entradas: list[dict[str, Any]]) -> None:
    """Guarda el último Excel bueno como JSON (fallback ante fallo de SharePoint)."""
    import json as _json
    data = {
        "_fuente": "sharepoint:CondicionesCredito.xlsx",
        "_actualizado": datetime.now().isoformat(timespec="seconds"),
        "clientes": {
            e["nombre"]: {"plazo_dias": e["plazo_dias"], "ciudad": e.get("ciudad", "")}
            for e in entradas
        },
    }
    try:
        _condiciones_json_path().write_text(
            _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _leer_condiciones_json() -> list[dict[str, Any]]:
    import json as _json
    path = _condiciones_json_path()
    if not path.exists():
        return []
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
        return [
            {"nombre": k, "plazo_dias": v.get("plazo_dias", 0), "ciudad": v.get("ciudad", "")}
            for k, v in data.get("clientes", {}).items()
        ]
    except (OSError, _json.JSONDecodeError):
        return []


def _cargar_condiciones_credito() -> dict[str, Any]:
    """Dict {nombre_normalizado: {plazo_dias, ciudad}}.

    SharePoint-first (Excel del equipo) con fallback al último JSON bueno.
    Cache en proceso con TTL para no leer SharePoint en cada función de cartera.
    """
    global _CONDICIONES_CACHE, _CONDICIONES_CACHE_AT
    if _CONDICIONES_CACHE is not None and (time.time() - _CONDICIONES_CACHE_AT) < CONDICIONES_TTL:
        return _CONDICIONES_CACHE

    entradas: list[dict[str, Any]] | None = None
    try:
        import credito_excel
        entradas = credito_excel.fetch_desde_sharepoint()
        if entradas:
            _persistir_condiciones(entradas)  # refresca el fallback
    except Exception:
        entradas = None
    if not entradas:
        entradas = _leer_condiciones_json()  # último bueno

    import credito_excel as _ce
    cond: dict[str, Any] = {}
    for e in entradas:
        cond[_ce.normaliza_nombre(e["nombre"])] = {
            "plazo_dias": int(e.get("plazo_dias", 0)),
            "ciudad": e.get("ciudad", ""),
        }
    _CONDICIONES_CACHE = cond
    _CONDICIONES_CACHE_AT = time.time()
    return cond


def _get_plazo_cliente(nombre_cliente: str) -> int | None:
    """Devuelve plazo en días para un cliente, o None si NO tiene crédito."""
    if not nombre_cliente:
        return None
    import credito_excel as _ce
    cond = _cargar_condiciones_credito()
    entry = cond.get(_ce.normaliza_nombre(nombre_cliente))
    if not entry:
        return None
    return int(entry.get("plazo_dias", 0))


def _parse_fecha_emision(s: str | None) -> date | None:
    """Parsea fecha_emision DD/MM/YYYY a date."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None


CARTERA_SALDO_MIN = 1.0  # Phase V (2026-06-11): excluir saldos < $1 (centavos)


def cartera_vencida_por_ciudad(
    ciudad: str,
    n: int = 5,
    *,
    meses_atras: int = 6,
    fecha_referencia: date | None = None,
) -> list[dict[str, Any]]:
    """Top N clientes con cartera VENCIDA en una ciudad (UIO o GYE).

    Vencimiento (fix 2026-06-16): `fecha_vencimiento = fecha_emision + plazo_dias`
    del cliente (condiciones_credito.json), MISMO criterio que `cartera_kpis`.
      - Cliente sin plazo en el JSON = sin crédito (contado) → no entra a cartera.
      - Cuenta como vencida si saldo > $1 y `fecha_emision + plazo < hoy`.
      - Excluye saldos ≤ $1 (centavos de redondeo, no cobranza real).
      - Por cliente se reporta la factura MÁS atrasada (emisión/plazo/venc de esa).

    Devuelve lista de dicts:
        [{"cliente": "X SA", "saldo_vencido": 1234.56, "facturas_vencidas": 3,
          "factura_mas_antigua": "001-002-...", "dias_atraso_max": 45,
          "plazo_dias": 30, "fecha_emision": "02/05/2026",
          "fecha_vencimiento": "01/06/2026"}, ...]
    """
    today = fecha_referencia or date.today()
    fecha_inicial = today - timedelta(days=meses_atras * 30)
    ciudad = (ciudad or "").upper()

    docs = _filter_validas(get_documentos(fecha_inicial, today))
    by_cli: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cliente": "",
            "saldo_vencido": 0.0,
            "facturas_vencidas": 0,
            "factura_mas_antigua": "",
            "dias_atraso_max": 0,
            "plazo_dias": 0,
            "fecha_emision": None,
            "fecha_vencimiento": None,
        }
    )
    for d in docs:
        if _doc_ciudad(d) != ciudad:
            continue
        saldo = _doc_saldo(d)
        if saldo <= CARTERA_SALDO_MIN:
            continue  # excluye centavos / saldos triviales
        cli = _doc_cliente_nombre(d)
        # Fix 2026-06-16: vencimiento = fecha_emision + plazo del cliente
        # (condiciones_credito.json), MISMO criterio que cartera_kpis. Antes se
        # usaba el campo fecha_vencimiento de Contifico, que viene = fecha de
        # emisión -> las facturas aparecían vencidas desde el día 1 y los días
        # de atraso eran erróneos. Cliente sin plazo en el JSON = sin crédito
        # (contado) y NO entra a cartera.
        plazo = _get_plazo_cliente(cli)
        if plazo is None:
            continue
        emis = _parse_fecha_emision(d.get("fecha_emision"))
        if emis is None:
            continue
        venc = emis + timedelta(days=plazo)
        if venc >= today:
            continue  # aún dentro del plazo de crédito (no vencida)
        atraso = (today - venc).days

        entry = by_cli[cli]
        entry["cliente"] = cli
        entry["saldo_vencido"] += saldo
        entry["facturas_vencidas"] += 1
        # La factura "representativa" del cliente es la más atrasada: de ella
        # tomamos emisión, plazo y vencimiento para mostrar en la tabla.
        if atraso > entry["dias_atraso_max"]:
            entry["dias_atraso_max"] = atraso
            entry["plazo_dias"] = plazo
            entry["factura_mas_antigua"] = d.get("documento", "")
            entry["fecha_emision"] = emis.strftime("%d/%m/%Y")
            entry["fecha_vencimiento"] = venc.strftime("%d/%m/%Y")

    rows = sorted(by_cli.values(), key=lambda r: r["saldo_vencido"], reverse=True)
    for r in rows:
        r["saldo_vencido"] = round(r["saldo_vencido"], 2)
    return rows[:n]


def _cartera_facturas_iter(
    fecha_inicial: date,
    fecha_final: date,
) -> "list[tuple[dict[str, Any], float, date]]":
    """Itera sobre las facturas con saldo > 0 de clientes en condiciones_credito.json.

    Para cada match devuelve la tupla (doc, saldo, fecha_vencimiento_efectiva).
    `fecha_vencimiento_efectiva` = fecha_emision + plazo del JSON (si no hay
    fecha_emision, cae a `fecha_vencimiento` del doc).
    """
    docs = _filter_validas(get_documentos(fecha_inicial, fecha_final))
    condiciones = _cargar_condiciones_credito()
    out: list[tuple[dict[str, Any], float, date]] = []
    for d in docs:
        saldo = _doc_saldo(d)
        if saldo <= 0:
            continue
        cli = _doc_cliente_nombre(d)
        import credito_excel as _ce
        entry_cond = condiciones.get(_ce.normaliza_nombre(cli))
        if not entry_cond:
            continue
        plazo = int(entry_cond.get("plazo_dias", 0))
        emis = _parse_fecha_emision(d.get("fecha_emision"))
        if emis is None:
            venc = _parse_fecha_vencimiento(d.get("fecha_vencimiento"))
        else:
            venc = emis + timedelta(days=plazo)
        if venc is None:
            continue
        out.append((d, saldo, venc))
    return out


def cartera_kpis(
    fecha_referencia: date | None = None,
    *,
    meses_atras: int = 12,
) -> dict[str, Any]:
    """KPIs globales de cartera (clientes con crédito).

    Devuelve cartera_total, cartera_vencida, cartera_no_vencida, pct_vencida
    y dias_atraso_promedio (ponderado por saldo).
    """
    today = fecha_referencia or date.today()
    fecha_inicial = today - timedelta(days=meses_atras * 30)
    cartera_total = 0.0
    cartera_vencida = 0.0
    cartera_no_vencida = 0.0
    weighted_atraso = 0.0
    for _doc, saldo, venc in _cartera_facturas_iter(fecha_inicial, today):
        cartera_total += saldo
        if venc >= today:
            cartera_no_vencida += saldo
        else:
            cartera_vencida += saldo
            weighted_atraso += (today - venc).days * saldo
    pct_vencida = (cartera_vencida / cartera_total) if cartera_total else 0.0
    dias_atraso = (weighted_atraso / cartera_vencida) if cartera_vencida else 0.0
    return {
        "cartera_total": round(cartera_total, 2),
        "cartera_vencida": round(cartera_vencida, 2),
        "cartera_no_vencida": round(cartera_no_vencida, 2),
        "pct_vencida": round(pct_vencida, 4),
        "dias_atraso_promedio": round(dias_atraso, 1),
    }


# Orden y label de los buckets que el correo y el bot conocen.
CARTERA_BUCKETS_ORDEN = [
    ("Dentro del Plazo", 0),
    ("1-30 Días", 1),
    ("31-60 Días", 2),
    ("61-90 Días", 3),
    ("+90 Días", 4),
]


def cartera_antiguedad_buckets(
    fecha_referencia: date | None = None,
    *,
    meses_atras: int = 12,
) -> list[dict[str, Any]]:
    """Antigüedad de cartera (todos los clientes con crédito) en 5 buckets.

    Replica los buckets del modelo PBI ('Cobranzas'[Antiguedad Cartera]):
    'Dentro del Plazo' / '1-30 Días' / '31-60 Días' / '61-90 Días' / '+90 Días'.
    Devuelve la lista ordenada con el mismo orden visual del reporte original.
    """
    today = fecha_referencia or date.today()
    fecha_inicial = today - timedelta(days=meses_atras * 30)
    saldos: dict[str, float] = {label: 0.0 for label, _ in CARTERA_BUCKETS_ORDEN}
    for _doc, saldo, venc in _cartera_facturas_iter(fecha_inicial, today):
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


def saldos_pendientes_clientes(
    fecha_inicial: date | None = None,
    fecha_final: date | None = None,
    n: int = 10,
) -> list[dict[str, Any]]:
    """Top N clientes con saldo pendiente (cartera por cliente).

    Suma el `saldo` de las facturas en el rango. Si no se da rango, usa los
    últimos 6 meses como ventana razonable. NO incluye facturas anuladas.

    NOTA: esto es una aproximación de cartera basada en FAC. Para cartera
    completa con antigüedad, ver Phase C donde se conectará el endpoint
    `/api/v1/cuenta_por_cobrar/` de Contifico.
    """
    if fecha_final is None:
        fecha_final = date.today()
    if fecha_inicial is None:
        fecha_inicial = fecha_final - timedelta(days=180)
    docs = _filter_validas(get_documentos(fecha_inicial, fecha_final))
    by_cli: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cliente": "", "saldo": 0.0, "num_facturas_pendientes": 0}
    )
    for d in docs:
        s = _doc_saldo(d)
        if s <= 0:
            continue
        c = _doc_cliente_nombre(d)
        by_cli[c]["cliente"] = c
        by_cli[c]["saldo"] += s
        by_cli[c]["num_facturas_pendientes"] += 1
    rows = sorted(by_cli.values(), key=lambda r: r["saldo"], reverse=True)
    for r in rows:
        r["saldo"] = round(r["saldo"], 2)
    return rows[:n]


# ============ Phase U (2026-06-09): Envíos del día para José (asistente 2 GYE) ============


def _tiene_transporte_item(d: dict[str, Any]) -> bool:
    """True si alguno de los detalles del documento es un producto de
    transporte/envío.

    Match flexible:
    - producto_codigo empieza con 'TRANSP' (raro: viene vacío en muchos docs)
    - producto_nombre contiene 'TRANSP' (cubre 'TRANSP. EXT.-CB. 12%',
      'TRANSP. B.E.', 'TRANSPORTE', 'Transporte', etc.)
    """
    detalles = d.get("detalles") or []
    for det in detalles:
        nombre = (det.get("producto_nombre") or "").upper()
        codigo = (det.get("producto_codigo") or "").upper()
        if codigo.startswith("TRANSP") or "TRANSP" in nombre:
            return True
    return False


def envios_dia_gye(
    fecha: date | None = None,
    dias_atras: int = 0,
) -> list[dict[str, Any]]:
    """Lista de envíos a entregar desde Guayaquil.

    Filtra facturas:
      - Emitidas en [fecha - dias_atras, fecha]
      - Con prefijo de documento `001-001` (sucursal GYE)
      - Con al menos un item de transporte (urbano TRANSP.B.E. o terminal TRANSP.EXT.)
      - No anuladas

    Phase V (2026-06-10): por defecto trae solo `fecha`, pero `dias_atras=1`
    trae ayer + hoy (útil para el card de José).

    Para cada factura retorna:
        factura_id, documento, cliente, direccion_factura, total, fecha_emision
    """
    fecha = fecha or date.today()
    fecha_ini = fecha - timedelta(days=max(0, dias_atras))
    docs = _filter_validas(get_documentos(fecha_ini, fecha))
    envios: list[dict[str, Any]] = []
    for d in docs:
        doc_num = (d.get("documento") or "").strip()
        if not doc_num.startswith(PREFIJO_GYE):
            continue
        if not _tiene_transporte_item(d):
            continue
        persona = d.get("persona") or {}
        envios.append({
            "factura_id": d.get("id") or doc_num,
            "documento": doc_num,
            "cliente": (persona.get("razon_social") or "").strip()
                or _doc_cliente_nombre(d),
            "direccion_factura": (persona.get("direccion") or "").strip(),
            "telefono": (persona.get("telefonos") or "").strip(),
            "total": _doc_total(d),
            "fecha_emision": d.get("fecha_emision", ""),
        })
    envios.sort(key=lambda e: e["documento"])
    return envios


# ============ CLI para debugging ============
if __name__ == "__main__":
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    cmd = sys.argv[1] if len(sys.argv) >= 2 else "help"

    if cmd == "ventas-hoy":
        print(json.dumps(ventas_dia(date.today()), indent=2, ensure_ascii=False))
    elif cmd == "ventas-ayer":
        print(json.dumps(ventas_dia(date.today() - timedelta(days=1)),
                         indent=2, ensure_ascii=False))
    elif cmd == "mes":
        print(json.dumps(cumplimiento_mes(), indent=2, ensure_ascii=False))
    elif cmd == "ciudad":
        print(json.dumps(ventas_por_ciudad(date.today() - timedelta(days=1)),
                         indent=2, ensure_ascii=False))
    elif cmd == "vendedores":
        ayer = date.today() - timedelta(days=1)
        print(json.dumps(top_vendedores(ayer, ayer, 5),
                         indent=2, ensure_ascii=False))
    elif cmd == "clientes":
        primer = date.today().replace(day=1)
        print(json.dumps(top_clientes(primer, date.today(), 10),
                         indent=2, ensure_ascii=False))
    elif cmd == "saldos":
        print(json.dumps(saldos_pendientes_clientes(n=10),
                         indent=2, ensure_ascii=False))
    elif cmd == "cartera":
        print(json.dumps(cartera_kpis(), indent=2, ensure_ascii=False))
    elif cmd == "antiguedad":
        print(json.dumps(cartera_antiguedad_buckets(),
                         indent=2, ensure_ascii=False))
    else:
        print("Comandos: ventas-hoy ventas-ayer mes ciudad vendedores "
              "clientes saldos cartera antiguedad")
