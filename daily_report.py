"""Reporte diario por correo con datos directos de Contifico REST API.

Migrado de Power BI cloud a Contifico (2026-06-10) — los queries DAX se
reemplazaron por las funciones de alto nivel de `contifico_client.py`. El
dashboard de Power BI sigue linkeado en el footer para drill-down visual.

Modos:
    python daily_report.py morning      # 8:00 AM - envia a jefe (cc tu)
    python daily_report.py test-morning # envia apertura solo a ti
    python daily_report.py dry-morning  # imprime HTML, no envia

Umbrales semáforo (ajustables abajo):
    CUMPL_VERDE = 1.00   # >= 100% -> verde
    CUMPL_AMARILLO = 0.85 # 85-99% -> amarillo, <85% -> rojo
    AYER_VERDE = 1.00
    AYER_AMARILLO = 0.80
    MORA_VERDE = 0.05   # <5% -> verde
    MORA_AMARILLO = 0.10 # 5-10% -> amarillo, >10% -> rojo
"""
from __future__ import annotations

import json as json_module
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import contifico_client

# Email backend: en azfunc (Azure App Service) usamos graph_mail (Service
# Principal app-only — sin MSAL token cache). En la PC local de Mateo no existe
# `graph_mail.send_email` (la copia local define `send`, no `send_email`), así
# que cae al backend pbi_cloud con device-code/MSAL cache para que `python
# daily_report.py test-morning` siga funcionando sin setup extra.
try:
    from graph_mail import send_email  # type: ignore[attr-defined]
    _EMAIL_BACKEND = "graph_mail"
except ImportError:
    from pbi_cloud import send_email  # type: ignore[no-redef]
    _EMAIL_BACKEND = "pbi_cloud_msal"

# HubSpot opcional — si falla, el correo se envía sin la sección Marketing
try:
    import hubspot_client
    HUBSPOT_OK = True
except Exception:
    HUBSPOT_OK = False

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador (UTC-5)

# ===== Feriados Ecuador =====
# Editable: si cambian fechas trasladadas o agregas un feriado regional,
# actualiza este dict por año.
from datetime import date

EC_HOLIDAYS: dict[int, list[date]] = {
    2025: [
        date(2025, 1, 1),    # Año Nuevo
        date(2025, 3, 3),    # Carnaval (lunes)
        date(2025, 3, 4),    # Carnaval (martes)
        date(2025, 4, 18),   # Viernes Santo
        date(2025, 5, 1),    # Día del Trabajo
        date(2025, 5, 23),   # Batalla de Pichincha (trasladado del 24)
        date(2025, 8, 10),   # Primer Grito de Independencia
        date(2025, 10, 10),  # Independencia de Guayaquil (trasladado del 9)
        date(2025, 11, 3),   # Día de los Difuntos / Independencia Cuenca
        date(2025, 12, 25),  # Navidad
    ],
    2026: [
        date(2026, 1, 1),    # Año Nuevo
        date(2026, 2, 16),   # Carnaval (lunes)
        date(2026, 2, 17),   # Carnaval (martes)
        date(2026, 4, 3),    # Viernes Santo
        date(2026, 5, 1),    # Día del Trabajo
        date(2026, 5, 25),   # Batalla de Pichincha (trasladado, lunes)
        date(2026, 8, 10),   # Primer Grito de Independencia
        date(2026, 10, 9),   # Independencia de Guayaquil
        date(2026, 11, 2),   # Día de los Difuntos
        date(2026, 11, 3),   # Independencia de Cuenca
        date(2026, 12, 25),  # Navidad
    ],
}


def _holidays(year: int) -> set[date]:
    return set(EC_HOLIDAYS.get(year, []))


def _is_workday(d: date) -> bool:
    """Día hábil = lunes a sábado, y no es feriado Ecuador."""
    if d.weekday() == 6:  # Domingo
        return False
    if d in _holidays(d.year):
        return False
    return True


def workdays_in_range(start: date, end: date) -> int:
    if start > end:
        return 0
    count = 0
    cur = start
    while cur <= end:
        if _is_workday(cur):
            count += 1
        cur = cur + timedelta(days=1)
    return count


def workdays_in_month(year: int, month: int) -> int:
    from calendar import monthrange
    _, last_day = monthrange(year, month)
    return workdays_in_range(date(year, month, 1), date(year, month, last_day))


def workdays_remaining(today: date) -> int:
    from calendar import monthrange
    _, last_day = monthrange(today.year, today.month)
    return workdays_in_range(today, date(today.year, today.month, last_day))


def workdays_passed(today: date) -> int:
    """Días hábiles transcurridos del mes hasta hoy (inclusive si hoy es hábil)."""
    return workdays_in_range(date(today.year, today.month, 1), today)


def previous_workday(today: date) -> date:
    """Phase R (2026-06-06): día hábil ANTERIOR a `today` saltando domingos y
    feriados Ecuador. Lunes retorna sábado (si sábado fue hábil), sino retrocede.

    Ejemplos (asumiendo no hay feriado adyacente):
      - Si hoy es martes → retorna lunes
      - Si hoy es lunes → retorna sábado
      - Si hoy es sábado → retorna viernes
    """
    d = today - timedelta(days=1)
    # Retrocede hasta encontrar un día hábil (max 14 días para no loopear infinito)
    for _ in range(14):
        if _is_workday(d):
            return d
        d = d - timedelta(days=1)
    return today - timedelta(days=1)  # fallback

# ===== Override del valor "ventas mismo mes año anterior" =====
# Si quieres forzar un valor (porque Contifico no lo está calculando como
# esperabas, ej. facturas anuladas mal clasificadas), pon aquí
# {mes_numero: valor_total_del_mes_anterior_año}. Si el mes no está en este
# dict, se usa el cálculo directo de Contifico (sum de facturas no anuladas
# del mismo mes año anterior).
PY_OVERRIDE = {
    5: 38000.0,   # mayo: usuario reporta $38K en Contifico (PBI muestra $33,956)
    # 6: 42000.0,  # ejemplo: para cuando llegue junio, editar aquí
}


# ===== Umbrales semáforo =====
CUMPL_VERDE = 1.00     # >= 100% del cumplimiento esperado a hoy
CUMPL_AMARILLO = 0.85  # 85-99% amarillo, < 85% rojo
AYER_VERDE = 1.00      # ayer >= meta diaria base
AYER_AMARILLO = 0.80   # 80-99% amarillo, < 80% rojo
MORA_VERDE = 0.05      # mora < 5% verde
MORA_AMARILLO = 0.10   # 5-10% amarillo, > 10% rojo


def color_cumpl(ratio: float | None) -> str:
    if ratio is None:
        return ""
    if ratio >= CUMPL_VERDE:
        return "ok"
    if ratio >= CUMPL_AMARILLO:
        return "warn"
    return "bad"


def color_ayer(ratio: float | None) -> str:
    if ratio is None:
        return ""
    if ratio >= AYER_VERDE:
        return "ok"
    if ratio >= AYER_AMARILLO:
        return "warn"
    return "bad"


def color_mora(ratio: float | None) -> str:
    if ratio is None:
        return ""
    if ratio < MORA_VERDE:
        return "ok"
    if ratio < MORA_AMARILLO:
        return "warn"
    return "bad"


def _as_ratio(v) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
        # Si viene > 1 (ej. 134.69), asumimos que es percentage y dividimos
        return x / 100 if abs(x) > 2 else x
    except (TypeError, ValueError):
        return None

REPORT_URL = (
    "https://app.powerbi.com/groups/me/reports/"
    "de5387d4-8203-4a93-8eaf-04212041fece"
)
JEFE = [
    "dsanchez@biodegradablesecuador.com",
    "gsanchez@biodegradablesecuador.com",
]
MIO = "malvarado@biodegradablesecuador.com"

MESES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre",
    12: "diciembre",
}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def fecha_humana() -> str:
    now = datetime.now()
    return f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month]} de {now.year}"


def now_humano() -> str:
    """Hora actual en Ecuador. Reemplaza al ex-`last_refresh_humano` que leía
    el último refresh del dataset PBI — ahora los datos vienen directo de
    Contifico, así que el correo siempre tiene info en tiempo real."""
    local = datetime.now(LOCAL_TZ)
    return local.strftime("%d/%m/%Y %H:%M") + " (hora Ecuador)"


def fmt_money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(v: Any) -> str:
    """Formatea un porcentaje. Heurística: si el valor es 'pequeño' (entre -10 y 10),
    asumimos que viene como ratio decimal (ej. 1.347 = 134.7%) y multiplicamos.
    Si es grande (ej. 74.4) asumimos que ya viene como porcentaje.

    OJO: para ratios que sabemos que son ratios (calculados en Python como
    division) usar `fmt_pct_ratio` que no hace adivinanza.
    """
    if v is None:
        return "—"
    try:
        x = float(v)
        if -10 <= x <= 10:
            x *= 100
        return f"{x:.1f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_pct_ratio(v: Any) -> str:
    """Formatea un ratio (0.85, 1.20, 2.16) como porcentaje (85%, 120%, 216%).
    Sin heurística — SIEMPRE multiplica por 100. Usar cuando el valor viene
    de una división en Python y sabes que es ratio puro."""
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _apollo_html(apollo: dict | None) -> str:
    """Renderiza el sub-bloque de prospección outbound (Apollo) con desglose
    por secuencia. Si apollo es None o no hay secuencias activas, vacío."""
    if not apollo or apollo.get("activas_count", 0) == 0:
        return ""
    delivered = apollo["delivered_total"]
    replied = apollo["replied_total"]
    tasa = apollo["tasa_respuesta_pct"]
    tasa_str = f"{tasa:.1f}%" if tasa is not None else "—"
    activas = apollo["activas_count"]
    secuencias = apollo.get("secuencias") or []

    # Lista de secuencias con sus contactos
    seq_rows = []
    for s in secuencias:
        nombre = s["nombre"]
        d = s["delivered"]
        r = s["replied"]
        seq_rows.append(
            f'<tr><td style="padding:6px 12px 6px 0;color:#374151;">• <b>{nombre}</b></td>'
            f'<td style="padding:6px 12px 6px 0;color:#065f46;font-weight:600;text-align:right;">'
            f'{d:,} contactos</td>'
            f'<td style="padding:6px 0;color:#6b7280;text-align:right;font-size:13px;">'
            f'{r} respuesta{"s" if r != 1 else ""}</td></tr>'
        )

    seq_table = ""
    if seq_rows:
        seq_table = (
            f'<table cellpadding="0" cellspacing="0" style="margin:6px 0 0 14px;'
            f'border-collapse:collapse;">'
            f'{"".join(seq_rows)}'
            f'</table>'
        )

    plural = "s" if activas > 1 else ""
    return f"""
<div style="background:#f5f3ff;border-left:4px solid #7c3aed;padding:12px 16px;margin:14px 0;border-radius:4px;">
  <p style="margin:0 0 4px 0;font-weight:600;color:#4c1d95;">📤 Prospección outbound (Apollo)</p>
  <p style="margin:0 0 8px 0;color:#5b21b6;font-size:13px;">
    Tu equipo está corriendo <b>{activas}</b> secuencia{plural} activa{plural} de correos a prospectos.
    En total contactaron a <b>{delivered:,} personas</b> y <b>{replied} respondieron</b> ({tasa_str} de tasa de respuesta).
  </p>
  {seq_table}
</div>"""


def _brecha_kpi(brecha: Any) -> str:
    """KPI de 'falta para meta' que se voltea cuando ya superaste la meta.

    - Si brecha > 0  → 'FALTA PARA META' (style default)
    - Si brecha == 0 → 'META JUSTA' (style ok, verde)
    - Si brecha < 0  → 'SUPERASTE LA META POR' (style ok, verde) con valor absoluto
    """
    if brecha is None:
        return _kpi("Falta para meta", "—")
    try:
        b = float(brecha)
    except (TypeError, ValueError):
        return _kpi("Falta para meta", "—")
    if b > 0:
        return _kpi("Falta para meta", fmt_money(b))
    if b == 0:
        return _kpi("Meta cumplida", "$0", "ok")
    # b < 0 → ya superaste
    return _kpi("✓ Superaste la meta por", fmt_money(abs(b)), "ok")


def _brecha_texto(brecha: Any) -> str:
    """Versión narrativa del estado de brecha, para el correo de cierre."""
    if brecha is None:
        return "Brecha para cumplir meta: <b>—</b>"
    try:
        b = float(brecha)
    except (TypeError, ValueError):
        return "Brecha para cumplir meta: <b>—</b>"
    if b > 0:
        return f"Brecha para cumplir meta: <b>{fmt_money(b)}</b>"
    if b == 0:
        return "Meta del mes <b>cumplida exactamente</b> 🎯"
    return f"<b style='color:#15803d;'>✓ Superaste la meta del mes por {fmt_money(abs(b))}</b>"


def _meta_dia_texto(meta_dia: Any, dias_rest: Any, brecha: Any) -> str:
    """Mensaje sobre cuánto vender por día. Si la meta ya está cumplida,
    cambia el mensaje a algo más motivacional."""
    try:
        b = float(brecha) if brecha is not None else None
    except (TypeError, ValueError):
        b = None
    if b is not None and b <= 0:
        return "Lo que vendas el resto del mes es <b>cosecha extra</b> 🎉"
    return (
        f"Hay que vender <b>{fmt_money(meta_dia)}/día</b> "
        f"en los {fmt_int(dias_rest)} días restantes."
    )


def fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "—"


def _safe(fn, default):
    """Llama `fn()` y devuelve default si falla. Mantiene el correo robusto
    cuando un endpoint de Contifico devuelve error parcial."""
    try:
        return fn()
    except Exception as e:
        print(f"  [WARN] {fn.__name__ if hasattr(fn,'__name__') else fn}: {e}",
              file=sys.stderr)
        return default


# ============ Adaptadores Contifico → diccionarios estilo legacy ============
# Las funciones q_* devolvían dicts con keys DAX-style (ej. "[VentasAyer]") y
# todo el render HTML lee esas keys. Para evitar tocar el HTML, los adaptadores
# de abajo mantienen la misma forma de salida pero leyendo datos de Contifico.


def q_kpis_cobranza() -> dict:
    """KPIs de cartera total (clientes con crédito). PctVencida ya viene como
    ratio decimal (0–1); Efectividad queda en None porque calcularla requiere
    historial de pagos que Contifico no expone vía esta API."""
    kpis = _safe(lambda: contifico_client.cartera_kpis(), {})
    return {
        "[CarteraTotal]": kpis.get("cartera_total"),
        "[CarteraVencida]": kpis.get("cartera_vencida"),
        "[CarteraNoVencida]": kpis.get("cartera_no_vencida"),
        "[PctVencida]": kpis.get("pct_vencida"),
        "[DiasAtraso]": kpis.get("dias_atraso_promedio"),
        "[Efectividad]": None,
    }


def q_antiguedad_completa() -> list[dict]:
    """Buckets de antigüedad de la cartera."""
    rows = _safe(lambda: contifico_client.cartera_antiguedad_buckets(), [])
    return [
        {"[Bucket]": r["bucket"], "[Saldo]": r["saldo"], "[Orden]": r["orden"]}
        for r in rows
    ]


def q_top_deudores_ciudad(ciudad: str, n: int = 7) -> list[dict]:
    """Top deudores vencidos para una ciudad (UIO o GYE).

    Usamos `meses_atras=12` para alinear con `cartera_kpis` y que la suma de
    los top deudores no quede por debajo del total `Vencida` mostrado arriba.
    """
    rows = _safe(
        lambda: contifico_client.cartera_vencida_por_ciudad(
            ciudad, n, meses_atras=12
        ),
        [],
    )
    return [
        {"[Cliente]": r["cliente"], "[Deuda]": r["saldo_vencido"]}
        for r in rows
    ]


def q_ventas_ayer_ciudad() -> list[dict]:
    """Ventas de ayer comercial divididas por ciudad (UIO/GYE)."""
    ayer_dt = previous_workday(date.today())
    by_city = _safe(
        lambda: contifico_client.ventas_por_ciudad(ayer_dt).get("por_ciudad", {}),
        {},
    )
    rows: list[dict] = []
    # Solo UIO/GYE — descartamos prefijos no reconocidos ("?")
    for codigo in ("UIO", "GYE"):
        entry = by_city.get(codigo) or {}
        rows.append({"[Ciudad]": codigo, "[Ventas]": entry.get("total", 0.0)})
    rows.sort(key=lambda r: r["[Ventas]"] or 0, reverse=True)
    return rows


def q_ventas_mes() -> dict:
    """MTD + ventas mismo mes año anterior (PY). El resto (meta, brecha,
    cumplimiento, meta diaria) lo recalcula `_recalcular_python` en días
    hábiles — así que sólo necesitamos los dos números crudos."""
    data = _safe(lambda: contifico_client.cumplimiento_mes(), {})
    return {
        "[MTD]": data.get("ventas_mtd"),
        "[VentasMesLY]": data.get("ventas_mismo_mes_anio_anterior"),
    }


def q_ventas_ayer() -> dict:
    """Ventas del día anterior comercial (salta domingos y feriados).
    La meta diaria base + cumplimiento ayer se calculan después en
    `_recalcular_python`."""
    ayer_dt = previous_workday(date.today())
    data = _safe(lambda: contifico_client.ventas_dia(ayer_dt), {})
    return {"[VentasAyer]": data.get("total")}


def q_ventas_dia() -> dict:
    """Ventas del día actual (para el reporte EOD)."""
    data = _safe(lambda: contifico_client.ventas_dia(date.today()), {})
    return {
        "[VentasDia]": data.get("total"),
        "[Ticket]": data.get("ticket_promedio"),
        "[Productos]": data.get("num_facturas"),
        "[Clientes]": data.get("clientes_unicos"),
    }


def q_top_vendedores_hoy(n: int = 5) -> list[dict]:
    hoy = date.today()
    rows = _safe(lambda: contifico_client.top_vendedores(hoy, hoy, n), [])
    return [
        {"[Vendedor]": r["vendedor"], "[VentasHoy]": r["total"]}
        for r in rows
    ]


# ============ HTML ============
CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; color: #2c2c2c; max-width: 720px;
       margin: 0; padding: 18px; }
h2 { color: #0e7c39; border-bottom: 2px solid #0e7c39; padding-bottom: 8px; margin-top: 0; }
h3 { color: #0e7c39; margin-top: 26px; margin-bottom: 8px; }
.kpi-grid { display: table; width: 100%; border-spacing: 8px 0; margin: 12px 0; }
.kpi { display: table-cell; padding: 12px 8px; border: 1px solid #d9e0d9;
       background: #f4faf6; text-align: center; vertical-align: middle; width: 33%; }
.kpi-label { display: block; font-size: 11px; color: #5e6b5e; text-transform: uppercase;
             letter-spacing: 0.5px; margin-bottom: 4px; }
.kpi-value { display: block; font-size: 20px; font-weight: 700; color: #2c2c2c; }
.kpi-value.muted { color: #5e6b5e; font-size: 17px; }
/* Semáforo: fondos saturados, letras blancas */
.kpi.ok   { background: #2e7d32; border-color: #1b5e20; }
.kpi.warn { background: #f57c00; border-color: #ef6c00; }
.kpi.bad  { background: #c62828; border-color: #b71c1c; }
.kpi.ok .kpi-label, .kpi.warn .kpi-label, .kpi.bad .kpi-label {
    color: rgba(255,255,255,0.92); }
.kpi.ok .kpi-value, .kpi.warn .kpi-value, .kpi.bad .kpi-value {
    color: #ffffff; }
table { border-collapse: collapse; width: 100%; margin-top: 6px; font-size: 13px; }
th { background: #0e7c39; color: white; text-align: left; padding: 8px 10px; font-weight: 600; }
td { border-bottom: 1px solid #ececec; padding: 8px 10px; }
.right { text-align: right; }
.warn-text { color: #c64a3b; font-weight: 600; }
.muted-text { color: #777; font-size: 12px; }
.footer { font-size: 11px; color: #888; margin-top: 30px; border-top: 1px solid #eee;
          padding-top: 10px; }
.footer a { color: #0e7c39; }
"""


def _kpi(label: str, value: str, cls: str = "") -> str:
    """Devuelve un <td> con estilos inline (resistente al stripping de Outlook).

    cls: '', 'ok' (verde), 'warn' (naranja), 'bad' (rojo), 'muted' (gris).
    """
    palette = {
        "ok":    ("#1b5e20", "#ffffff", "rgba(255,255,255,0.92)", "#0e3f12"),
        "warn":  ("#ef6c00", "#ffffff", "rgba(255,255,255,0.92)", "#a85000"),
        "bad":   ("#c62828", "#ffffff", "rgba(255,255,255,0.92)", "#8e1a1a"),
        "muted": ("#eef1ee", "#5e6b5e", "#5e6b5e", "#d4dad4"),
        "":      ("#f4faf6", "#0e7c39", "#5e6b5e", "#d9e0d9"),
    }
    bg, val_c, lbl_c, border = palette.get(cls, palette[""])
    return (
        f'<td bgcolor="{bg}" align="center" valign="middle" '
        f'style="background-color:{bg};padding:16px 10px;'
        f'border:2px solid {border};border-radius:6px;width:33.3%;">'
        f'<div style="font-size:11px;color:{lbl_c};font-family:Segoe UI,Arial,sans-serif;'
        f'text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;font-weight:600;">'
        f'{label}</div>'
        f'<div style="font-size:22px;color:{val_c};font-family:Segoe UI,Arial,sans-serif;'
        f'font-weight:700;line-height:1.2;">{value}</div>'
        f'</td>'
    )


def _kpi_row(*kpis: str) -> str:
    """Envuelve varios _kpi en una tabla (necesario para que Outlook los lea bien)."""
    cells = "".join(kpis)
    return (
        '<table role="presentation" cellpadding="0" cellspacing="6" border="0" '
        'width="100%" style="border-collapse:separate;border-spacing:6px;margin:10px 0;">'
        f'<tr>{cells}</tr></table>'
    )


def _recalcular_python(ventas: dict, ayer: dict) -> None:
    """Recalcula meta, brecha, cumplimientos y meta diaria usando DÍAS HÁBILES
    (excluye domingos y feriados Ecuador). Si hay PY_OVERRIDE para el mes,
    también lo aplica."""
    hoy = datetime.now().date()
    try:
        # PY: override si existe, si no el valor calculado por Contifico
        py_raw = float(ventas.get("[VentasMesLY]") or 0)
        py = float(PY_OVERRIDE.get(hoy.month, py_raw))

        # Días hábiles del mes y restantes
        wd_total = workdays_in_month(hoy.year, hoy.month)
        wd_rest = workdays_remaining(hoy)
        wd_passed = workdays_passed(hoy)
        if wd_total <= 0:
            return

        mtd = float(ventas.get("[MTD]") or 0)
        meta = py * 1.20
        meta_diaria_base = meta / wd_total
        meta_esp_hoy = meta_diaria_base * wd_passed
        brecha = meta - mtd
        cumpl_mes = mtd / meta if meta else None
        cumpl_hoy = mtd / meta_esp_hoy if meta_esp_hoy else None
        meta_dia = max(brecha, 0) / wd_rest if wd_rest else 0

        ventas["[VentasMesLY]"] = py
        ventas["[Meta]"] = meta
        ventas["[MetaEsperadaHoy]"] = meta_esp_hoy
        ventas["[Brecha]"] = brecha
        ventas["[CumplMes]"] = cumpl_mes
        ventas["[CumplHoy]"] = cumpl_hoy
        ventas["[MetaDia]"] = meta_dia
        ventas["[DiasRestantes]"] = wd_rest

        # Meta diaria base de ayer (misma que la del mes)
        ayer["[MetaDiariaBase]"] = meta_diaria_base
        ventas_ayer = float(ayer.get("[VentasAyer]") or 0)
        ayer["[CumplAyer]"] = (
            ventas_ayer / meta_diaria_base if meta_diaria_base else None
        )
    except (TypeError, ValueError, ZeroDivisionError):
        pass


SOURCE_NOMBRE = {
    "OFFLINE": "Offline / Eventos",
    "ORGANIC_SEARCH": "Búsqueda orgánica",
    "PAID_SEARCH": "Anuncios buscador",
    "PAID_SOCIAL": "Anuncios redes",
    "SOCIAL_MEDIA": "Redes orgánicas",
    "DIRECT_TRAFFIC": "Tráfico directo",
    "REFERRALS": "Referidos",
    "EMAIL_MARKETING": "Email marketing",
    "OTHER_CAMPAIGNS": "Otras campañas",
    "UNKNOWN": "Desconocido",
}


def _generate_leads_chart_png(serie: list[dict]) -> bytes | None:
    """Genera un PNG con gráfico de barras de leads últimos 7 días.
    Devuelve los bytes PNG, o None si matplotlib no está disponible o falla."""
    try:
        import io
        import matplotlib
        matplotlib.use("Agg")  # sin GUI
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator

        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        labels = []
        values = []
        colors = []
        for r in serie:
            d = r["fecha_obj"]
            # Etiqueta: día semana + número (ej. "Lun 13")
            labels.append(f"{dias[d.weekday()]} {d.day}")
            values.append(r["count"])
            # Marcar hoy y ayer con tonos diferentes
            colors.append("#0e7c39")

        fig, ax = plt.subplots(figsize=(7.0, 2.6), dpi=120)
        bars = ax.bar(labels, values, color=colors, edgecolor="#0a5a28", width=0.6)

        # Anotaciones encima de cada barra
        for bar, v in zip(bars, values):
            if v > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(values) * 0.02,
                    str(v),
                    ha="center",
                    va="bottom",
                    fontsize=10,
                    color="#0a5a28",
                    fontweight="bold",
                )

        ax.set_ylabel("Leads", fontsize=10, color="#444")
        ax.set_title("Leads nuevos — últimos 7 días", fontsize=12,
                     color="#0e7c39", fontweight="bold", loc="left")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_facecolor("#f7faf7")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#ccc")
        ax.spines["bottom"].set_color("#ccc")
        ax.tick_params(axis="both", colors="#666", labelsize=9)
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.set_ylim(top=max(values) * 1.25 if max(values, default=0) > 0 else 1)

        plt.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                    facecolor="white")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        print(f"  [WARN] no pude generar chart: {e}", file=sys.stderr)
        return None


def _ventas_exportacion_ayer() -> dict | None:
    """Trae ventas de exportación de ayer desde Contifico (no de PBI).

    Heurística: una factura es 'exportación' si `persona.es_extranjero=true`.
    Devuelve dict {total: $, count: N, clientes: [str]} o None si Contifico
    no responde / no hay token.
    """
    try:
        import contifico_client
    except ImportError:
        return None
    from datetime import date as _date
    fecha_ayer = _date.today() - timedelta(days=1)
    try:
        docs = contifico_client.get_documentos(fecha_ayer, fecha_ayer, tipo="FAC")
    except Exception as e:
        print(f"  [WARN] Contifico exportación falló: {e}", file=sys.stderr)
        return None

    total = 0.0
    facturas = []
    clientes_set = set()
    for d in docs:
        if d.get("anulado"):
            continue
        persona = d.get("persona") or {}
        if not persona.get("es_extranjero"):
            continue
        try:
            monto = float(d.get("total") or 0)
        except (TypeError, ValueError):
            monto = 0.0
        total += monto
        cliente = persona.get("razon_social") or "—"
        clientes_set.add(cliente)
        facturas.append({
            "documento": d.get("documento"),
            "cliente": cliente,
            "total": monto,
        })

    if not facturas:
        return {"total": 0.0, "count": 0, "clientes": []}
    return {
        "total": total,
        "count": len(facturas),
        "clientes": list(clientes_set)[:3],
        "facturas": facturas[:5],
    }


def _hubspot_data() -> dict | None:
    """Consulta los KPIs de HubSpot + serie de leads 7d. Devuelve None si falla.

    Incluye métricas extendidas (deals stuck, conversion rate, leads sin
    responder) para el resumen ejecutivo con Claude.
    """
    if not HUBSPOT_OK:
        return None
    try:
        leads = hubspot_client.leads_ayer()
        promedio = hubspot_client.leads_promedio_7d()
        deals = hubspot_client.deals_ganados_ayer()
        pipe = hubspot_client.pipeline_abierto()
        serie_7d = hubspot_client.leads_por_dia_ultimos_7d()
        # Nuevas métricas accionables
        stuck = hubspot_client.deals_stuck(dias_min=14)
        conv = hubspot_client.conversion_rate_30d()
        sin_responder = hubspot_client.leads_sin_responder(horas_min=24)
        return {
            "leads": leads,
            "promedio_7d": promedio,
            "deals": deals,
            "pipeline": pipe,
            "serie_7d": serie_7d,
            "stuck": stuck,
            "conversion": conv,
            "sin_responder": sin_responder,
        }
    except Exception as e:
        print(f"  [WARN] HubSpot falló: {e}", file=sys.stderr)
        return None


def _generate_marketing_summary(hs: dict) -> dict:
    """Usa Claude API para interpretar los datos de marketing y devolver:
    - `resumen`: 2-3 oraciones explicando cómo estuvo ayer
    - `acciones`: lista de 1-3 strings con acciones recomendadas para hoy

    Si Claude falla por cualquier motivo (sin internet, sin token, error de API),
    devuelve un fallback básico para que el correo siga saliendo.
    """
    try:
        from anthropic import Anthropic
    except ImportError:
        return _marketing_summary_fallback(hs)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _marketing_summary_fallback(hs)

    try:
        client = Anthropic()
        leads_total = hs["leads"]["total"]
        prom = hs.get("promedio_7d") or 0
        deltas_lead = ((leads_total - prom) / prom * 100) if prom > 0 else None

        prompt_data = {
            "leads_ayer": leads_total,
            "leads_promedio_7d": round(prom, 1),
            "delta_leads_pct": round(deltas_lead, 1) if deltas_lead is not None else None,
            "top_fuente": hs["leads"].get("top_source"),
            "deals_ganados_ayer": hs["deals"]["count"],
            "revenue_ganado_ayer": round(hs["deals"]["revenue"], 2),
            "pipeline_count": hs["pipeline"]["count"],
            "pipeline_valor": round(hs["pipeline"]["valor"], 2),
            "deals_stuck_count": hs["stuck"]["count"],
            "deals_stuck_valor": round(hs["stuck"]["valor"], 2),
            "deals_stuck_top": hs["stuck"]["top"][:3],
            "tasa_cierre_30d_pct": (
                round(hs["conversion"]["tasa_cierre"] * 100, 1)
                if hs["conversion"]["tasa_cierre"] is not None else None
            ),
            "ganados_30d": hs["conversion"]["ganados"],
            "perdidos_30d": hs["conversion"]["perdidos"],
            "leads_sin_responder_count": hs["sin_responder"]["count"],
        }

        system = """Eres un asistente que ayuda a Daniel Sánchez, gerente general de
Biodegradables Ecuador (empresa de empaques biodegradables en Quito y Guayaquil).

Tu tarea: en base a datos de marketing del día anterior, escribir:
1. Un resumen ejecutivo de 2-3 oraciones EN ESPAÑOL, claro, sin jerga, que le
   diga a Daniel cómo estuvo ayer y qué destacaría. Habla en segunda persona
   (vos / te).
2. Una lista de 1 a 3 acciones concretas recomendadas para HOY. Cada acción
   debe ser específica, no genérica. Si los datos están sanos, decílo y propone
   acciones para mantener el ritmo, no inventes problemas.

Devuelve EXACTAMENTE este JSON, sin texto antes ni después:
{
  "resumen": "...",
  "acciones": ["...", "..."]
}

Reglas:
- Si leads ayer son mucho menos que el promedio (>30% abajo): mencionalo
- Si hay deals stuck con valor alto: priorizalos con nombre y monto
- Si hay leads sin responder de >24h: alertálo
- Si la tasa de cierre es <15%: sugerí revisar el funnel
- Si los números están bien: felicitálo y propone consolidar
- No uses emojis en el resumen
- No menciones números que no estén en los datos
- Tono profesional pero cercano (es para el CEO)"""

        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": (
                    "Datos de marketing del día anterior:\n\n"
                    + json_module.dumps(prompt_data, ensure_ascii=False, indent=2)
                    + "\n\nGenerá el resumen ejecutivo y las acciones."
                ),
            }],
        )

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text
        # Extraer JSON
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json_module.loads(m.group(0))
            resumen = data.get("resumen", "").strip()
            acciones = data.get("acciones", [])
            if resumen and isinstance(acciones, list):
                return {"resumen": resumen, "acciones": acciones[:3]}
    except Exception as e:
        print(f"  [WARN] Claude marketing summary falló: {e}", file=sys.stderr)

    return _marketing_summary_fallback(hs)


def _marketing_summary_fallback(hs: dict) -> dict:
    """Resumen heurístico básico cuando Claude no está disponible."""
    leads_total = hs["leads"]["total"]
    prom = hs.get("promedio_7d") or 0
    if prom > 0:
        delta_pct = (leads_total - prom) / prom * 100
        if delta_pct >= 30:
            tendencia = f"un día fuerte de captación ({leads_total} leads, +{delta_pct:.0f}% sobre el promedio)"
        elif delta_pct >= -10:
            tendencia = f"un día normal de captación ({leads_total} leads, en línea con el promedio de {prom:.1f}/día)"
        else:
            tendencia = f"un día bajo de captación ({leads_total} leads, {delta_pct:.0f}% bajo el promedio)"
    else:
        tendencia = f"{leads_total} leads nuevos"

    deals = hs["deals"]
    if deals["count"] > 0:
        deals_msg = f" Cerraste {deals['count']} deal{'s' if deals['count']>1 else ''} por ${deals['revenue']:,.0f}."
    else:
        deals_msg = " No hubo deals cerrados ayer."
    resumen = f"Ayer fue {tendencia}.{deals_msg}"

    acciones = []
    if hs["stuck"]["count"] > 0:
        acciones.append(
            f"Revisar los {hs['stuck']['count']} deals abiertos sin movimiento >14 días "
            f"(${hs['stuck']['valor']:,.0f} en juego)."
        )
    if hs["sin_responder"]["count"] > 0:
        acciones.append(
            f"Responder a {hs['sin_responder']['count']} lead{'s' if hs['sin_responder']['count']>1 else ''} "
            "que llevan >24h sin contacto."
        )
    if not acciones:
        acciones.append("Mantener el ritmo de prospección.")
    return {"resumen": resumen, "acciones": acciones}


CIUDAD_NOMBRE = {"UIO": "Quito", "GYE": "Guayaquil"}


def _ciudad_nombre(codigo: str) -> str:
    return CIUDAD_NOMBRE.get((codigo or "").upper(), codigo or "—")


def html_morning() -> str:
    ayer = q_ventas_ayer()
    ayer_ciudad = q_ventas_ayer_ciudad()
    ventas = q_ventas_mes()
    kpis = q_kpis_cobranza()
    ant = q_antiguedad_completa()
    top_uio = q_top_deudores_ciudad("UIO", 10)
    top_gye = q_top_deudores_ciudad("GYE", 10)
    hs = _hubspot_data()

    # Recalcular usando días hábiles (excluye domingos + feriados Ecuador)
    # y aplicar PY_OVERRIDE si existe
    _recalcular_python(ventas, ayer)

    # ----- Cálculo de semáforos -----
    # CumplHoy y CumplAyer son ratios puros (calculados en _recalcular_python
    # como ventas/meta), NO les aplicamos _as_ratio porque distorsiona valores >2.
    cumpl_hoy_ratio = ventas.get("[CumplHoy]")
    cumpl_hoy_cls = color_cumpl(cumpl_hoy_ratio)

    cumpl_ayer_ratio = ayer.get("[CumplAyer]")
    cumpl_ayer_cls = color_ayer(cumpl_ayer_ratio)

    # PctVencida viene de PBI y puede venir como ratio o como porcentaje según el DAX,
    # por eso sí mantenemos la heurística aquí.
    mora_ratio = _as_ratio(kpis.get("[PctVencida]"))
    mora_cls = color_mora(mora_ratio)

    # Mapeo de etiquetas de bucket para consistencia con el resto del correo
    BUCKET_RENAME = {
        "Al Día": "Dentro del Plazo",
        "Al día": "Dentro del Plazo",
        "AL DÍA": "Dentro del Plazo",
    }
    ant_rows = "".join(
        f'<tr><td>{BUCKET_RENAME.get(r.get("[Bucket]",""), r.get("[Bucket]","—"))}</td>'
        f'<td class="right">{fmt_money(r.get("[Saldo]"))}</td></tr>'
        for r in ant
    )
    def _top_rows(rows: list[dict]) -> str:
        if not rows:
            return '<tr><td colspan="2" class="muted-text">Sin clientes con cartera vencida.</td></tr>'
        return "".join(
            f'<tr><td>{r.get("[Cliente]","—")}</td>'
            f'<td class="right" style="color:#c62828;font-weight:600">'
            f'{fmt_money(r.get("[Deuda]"))}</td></tr>'
            for r in rows
        )

    top_uio_rows = _top_rows(top_uio)
    top_gye_rows = _top_rows(top_gye)

    # KPIs de ventas ayer por ciudad
    ciudad_kpis = []
    for r in ayer_ciudad:
        ciudad = r.get("[Ciudad]")
        nombre = _ciudad_nombre(ciudad)
        valor = r.get("[Ventas]")
        if valor is None:
            continue
        ciudad_kpis.append(_kpi(f"Ayer {nombre}", fmt_money(valor), "muted"))

    # Exportación: solo si hubo ventas a clientes extranjeros ayer
    expo = _ventas_exportacion_ayer()
    if expo and expo["count"] > 0:
        clientes_txt = ", ".join(expo["clientes"][:2])
        if len(expo["clientes"]) > 2:
            clientes_txt += f" +{len(expo['clientes'])-2}"
        ciudad_kpis.append(_kpi(
            "Ayer Exportación",
            f"{fmt_money(expo['total'])}<br>"
            f"<span style='font-size:11px;font-weight:400;'>{expo['count']} factura{'s' if expo['count']>1 else ''} · {clientes_txt}</span>",
            "ok",
        ))

    ciudad_row_html = _kpi_row(*ciudad_kpis) if ciudad_kpis else ""

    # ----- Bloque HubSpot (Marketing) -----
    hubspot_section = ""
    if hs:
        leads_ayer_total = hs["leads"]["total"]
        promedio_7d = hs["promedio_7d"]
        ratio_leads = (leads_ayer_total / promedio_7d) if promedio_7d > 0 else None
        if ratio_leads is None:
            leads_cls = ""
            tendencia_leads = "—"
        elif ratio_leads >= 1.0:
            leads_cls = "ok"
            tendencia_leads = f"↑ +{(ratio_leads-1)*100:.0f}% vs promedio 7d"
        elif ratio_leads >= 0.7:
            leads_cls = "warn"
            tendencia_leads = f"↓ {(ratio_leads-1)*100:.0f}% vs promedio 7d"
        else:
            leads_cls = "bad"
            tendencia_leads = f"↓ {(ratio_leads-1)*100:.0f}% vs promedio 7d"

        top_src_raw = hs["leads"]["top_source"] or "UNKNOWN"
        top_src = SOURCE_NOMBRE.get(top_src_raw, top_src_raw.title())
        top_src_count = hs["leads"]["top_source_count"]

        deals_count = hs["deals"]["count"]
        deals_revenue = hs["deals"]["revenue"]
        deals_cls = "ok" if deals_count > 0 else "muted"

        pipe_count = hs["pipeline"]["count"]
        pipe_valor = hs["pipeline"]["valor"]

        conv = hs.get("conversion") or {}
        tasa_cierre = conv.get("tasa_cierre")
        tasa_cierre_str = f"{tasa_cierre*100:.0f}%" if tasa_cierre is not None else "—"
        ganados_30d = conv.get("ganados", 0)
        perdidos_30d = conv.get("perdidos", 0)
        if tasa_cierre is None:
            cierre_cls = ""
        elif tasa_cierre >= 0.20:
            cierre_cls = "ok"
        elif tasa_cierre >= 0.10:
            cierre_cls = "warn"
        else:
            cierre_cls = "bad"

        stuck = hs.get("stuck") or {"count": 0, "valor": 0, "top": []}
        sin_resp = hs.get("sin_responder") or {"count": 0, "leads": []}

        # Resumen ejecutivo + acciones con Claude
        summary = _generate_marketing_summary(hs)
        resumen_html = summary.get("resumen") or ""
        acciones = summary.get("acciones") or []

        # Sección "Necesita atención" — combina items concretos + acciones IA
        atencion_items: list[str] = []
        if stuck["count"] > 0:
            top_stuck = stuck["top"][0] if stuck["top"] else None
            if top_stuck:
                atencion_items.append(
                    f"<b>{stuck['count']} deals sin movimiento &gt;14 días</b> "
                    f"({fmt_money(stuck['valor'])} en juego). "
                    f"El más grande: <i>{top_stuck['nombre']}</i> "
                    f"({fmt_money(top_stuck['monto'])}, "
                    f"{top_stuck.get('dias_sin_movimiento','?')}d sin update)"
                )
            else:
                atencion_items.append(
                    f"<b>{stuck['count']} deals sin movimiento &gt;14 días</b> "
                    f"({fmt_money(stuck['valor'])} en juego)"
                )
        if sin_resp["count"] > 0:
            top_lead = sin_resp["leads"][0] if sin_resp["leads"] else None
            if top_lead:
                atencion_items.append(
                    f"<b>{sin_resp['count']} lead{'s' if sin_resp['count']>1 else ''} "
                    f"sin responder &gt;24h.</b> Ej: <i>{top_lead['nombre']}</i>"
                    + (f" ({top_lead['email']})" if top_lead.get('email') else "")
                )
            else:
                atencion_items.append(
                    f"<b>{sin_resp['count']} lead{'s' if sin_resp['count']>1 else ''} "
                    f"sin responder &gt;24h</b>"
                )
        # Sumar las acciones que sugiere Claude (que no estén ya cubiertas)
        for a in acciones:
            if a and a not in atencion_items:
                atencion_items.append(a)

        atencion_html = ""
        if atencion_items:
            li_items = "".join(f'<li style="margin:4px 0;">{it}</li>' for it in atencion_items[:5])
            atencion_html = f"""
<div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:12px 16px;border-radius:4px;margin:14px 0;">
  <p style="margin:0 0 6px 0;font-weight:600;color:#78350f;">🚨 Necesita atención hoy</p>
  <ul style="margin:0;padding-left:20px;color:#78350f;">
    {li_items}
  </ul>
</div>"""

        # Gráfico de tendencia 7d
        chart_html = ""
        serie = hs.get("serie_7d") or []
        if serie:
            chart_html = """
<div style="margin:18px 0 6px 0;">
  <img src="cid:chart_leads_7d" alt="Leads últimos 7 días"
       style="max-width:100%;height:auto;border:1px solid #d9e0d9;border-radius:6px;"/>
</div>"""

        # Resumen ejecutivo (cita destacada arriba de todo)
        resumen_html_block = ""
        if resumen_html:
            resumen_html_block = f"""
<div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:12px 16px;margin:12px 0;border-radius:4px;">
  <p style="margin:0;color:#14532d;line-height:1.45;">{resumen_html}</p>
</div>"""

        hubspot_section = f"""
<h3>📣 Marketing y Prospección</h3>
{resumen_html_block}

<p style="margin:14px 0 6px 0;font-weight:600;color:#374151;">📊 Captación de ayer</p>
<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin:6px 0 0 0;">
  <tr>
    <td style="padding:8px 12px;background:#f9fafb;border-left:3px solid #16a34a;">
      <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Leads nuevos</div>
      <div style="font-size:22px;font-weight:700;color:#111827;margin-top:2px;">{leads_ayer_total}</div>
      <div style="font-size:12px;color:#6b7280;">{tendencia_leads}</div>
      <div style="font-size:11px;color:#9ca3af;margin-top:4px;font-style:italic;">
        Lead = persona o empresa que mostró interés por primera vez (web, llamada, evento, Apollo).
      </div>
    </td>
  </tr>
  <tr><td style="height:6px;"></td></tr>
  <tr>
    <td style="padding:8px 12px;background:#f9fafb;border-left:3px solid #2563eb;">
      <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Deals ganados ayer</div>
      <div style="font-size:22px;font-weight:700;color:#111827;margin-top:2px;">{deals_count} · {fmt_money(deals_revenue)}</div>
      <div style="font-size:11px;color:#9ca3af;margin-top:4px;font-style:italic;">
        Deal ganado = lead que cerró compra y se vuelve cliente.
      </div>
    </td>
  </tr>
  <tr><td style="height:6px;"></td></tr>
  <tr>
    <td style="padding:8px 12px;background:#f9fafb;border-left:3px solid #f59e0b;">
      <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">En negociación ahora</div>
      <div style="font-size:22px;font-weight:700;color:#111827;margin-top:2px;">{pipe_count} deals · {fmt_money(pipe_valor)}</div>
      <div style="font-size:11px;color:#9ca3af;margin-top:4px;font-style:italic;">
        Leads que están en proceso de venta pero aún no cerraron.
      </div>
    </td>
  </tr>
</table>

{atencion_html}
{chart_html}
<p class="muted-text" style="margin-top:8px;font-size:11px;">
  Fuente: HubSpot CRM. Próximamente: costo por lead cuando conectemos Meta Ads y Google Ads.
</p>"""
    elif HUBSPOT_OK is False:
        hubspot_section = ""  # módulo no cargó, omitir silenciosamente

    dias_rest = fmt_int(ventas.get("[DiasRestantes]"))

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h2>Resumen Comercial — Apertura del día</h2>
<p>Buenos días Daniel,<br>
te comparto el estado de la operación al inicio del día.<br>
<span class="muted-text">{fecha_humana()}</span></p>

<h3>📅 Cómo nos fue ayer</h3>
{_kpi_row(
    _kpi("Vendimos ayer", fmt_money(ayer.get("[VentasAyer]")), cumpl_ayer_cls),
    _kpi("Meta diaria base", fmt_money(ayer.get("[MetaDiariaBase]")), "muted"),
    _kpi("Cumplimiento de ayer", fmt_pct_ratio(cumpl_ayer_ratio), cumpl_ayer_cls),
)}
{ciudad_row_html}
<p class="muted-text">
  Meta diaria base = (ventas mismo mes año anterior × 1.20) ÷ días del mes.
</p>

<!-- Sección "Lo que toca hoy" removida 2026-05-31 (request de Mateo) — los KPIs
     "Días hábiles restantes" y "Cumplimiento vs día" se mostraban acá pero
     son redundantes con la sección de Avance del mes que viene después. -->


<h3>📈 Avance del mes</h3>
{_kpi_row(
    _kpi("Vendido en el mes", fmt_money(ventas.get("[MTD]"))),
    _kpi("Meta del mes", fmt_money(ventas.get("[Meta]")), "muted"),
    _brecha_kpi(ventas.get("[Brecha]")),
)}
{_kpi_row(
    _kpi(
        "% cumplimiento al día",
        (fmt_pct_ratio(ventas.get("[CumplHoy]"))
         if ventas.get("[CumplHoy]") is not None else "—"),
        ("ok" if (ventas.get("[CumplHoy]") or 0) >= CUMPL_VERDE
         else ("warn" if (ventas.get("[CumplHoy]") or 0) >= CUMPL_AMARILLO
               else "bad")) if ventas.get("[CumplHoy]") is not None else "muted",
    ),
    _kpi(
        "Días laborales restantes",
        f"{workdays_remaining(date.today())} días",
        "muted",
    ),
)}
<p class="muted-text">
  % cumplimiento al día = vendido en el mes ÷ (meta diaria × días laborales transcurridos).
  100% = vamos en línea con la meta; menos de 100% = hay que vender más por día para alcanzarla.
  Meta mensual calculada como +20% sobre el mismo mes del año anterior
  ({fmt_money(ventas.get("[VentasMesLY]"))}). Días laborales = Lun-Sáb sin feriados Ecuador.
</p>
{hubspot_section}
<h3>📋 Cartera de clientes</h3>
{_kpi_row(
    _kpi("Total nos deben", fmt_money(kpis.get("[CarteraTotal]"))),
    _kpi("Dentro del Plazo", fmt_money(kpis.get("[CarteraNoVencida]")), "ok"),
    _kpi("Vencida", fmt_money(kpis.get("[CarteraVencida]")), mora_cls),
)}
{_kpi_row(
    _kpi("% Mora", fmt_pct(mora_ratio), mora_cls),
    _kpi("Días promedio atraso", fmt_int(kpis.get("[DiasAtraso]")), "muted"),
    _kpi("Efectividad cobranza", fmt_pct(kpis.get("[Efectividad]")), "muted"),
)}

<h4 style="margin-top:18px;margin-bottom:6px;color:#444">Cartera vencida por antigüedad</h4>
<table>
<tr><th>Bucket</th><th class="right">Saldo</th></tr>
{ant_rows}
</table>

<h3>⚠️ Top 10 deudores — Quito (UIO)</h3>
<table>
<tr><th>Cliente</th><th class="right">Deuda vencida</th></tr>
{top_uio_rows}
</table>

<h3>⚠️ Top 10 deudores — Guayaquil (GYE)</h3>
<table>
<tr><th>Cliente</th><th class="right">Deuda vencida</th></tr>
{top_gye_rows}
</table>

<div class="footer">
Datos en tiempo real desde Contifico · consulta: <b>{now_humano()}</b><br>
Reporte generado automáticamente · {datetime.now().strftime("%d/%m/%Y %H:%M")}<br>
<a href="{REPORT_URL}">Abrir dashboard completo en Power BI</a>
</div>
</body></html>"""


def html_eod() -> str:
    dia = q_ventas_dia()
    ventas = q_ventas_mes()
    top_vend = q_top_vendedores_hoy(5)
    cob = q_kpis_cobranza()

    top_rows = "".join(
        f'<tr><td>{r.get("[Vendedor]","—")}</td>'
        f'<td class="right">{fmt_money(r.get("[VentasHoy]"))}</td></tr>'
        for r in top_vend
    ) or '<tr><td colspan="2" class="muted-text">Sin ventas registradas hoy.</td></tr>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h2>Resumen Comercial — Cierre del día</h2>
<p>Hola Daniel,<br>
te comparto el cierre de operación del día.<br>
<span class="muted-text">{fecha_humana()}</span></p>

<h3>💰 Ventas de hoy</h3>
<div class="kpi-grid">
  {_kpi("Vendido hoy", fmt_money(dia.get("[VentasDia]")))}
  {_kpi("Ticket promedio", fmt_money(dia.get("[Ticket]")), "muted")}
  {_kpi("Clientes únicos", fmt_int(dia.get("[Clientes]")), "muted")}
</div>
<p>Productos vendidos: <b>{fmt_int(dia.get("[Productos]"))}</b></p>

<h3>🏆 Top 5 vendedores</h3>
<table>
<tr><th>Vendedor</th><th class="right">Ventas del día</th></tr>
{top_rows}
</table>

<h3>📈 Acumulado del mes</h3>
<div class="kpi-grid">
  {_kpi("MTD", fmt_money(ventas.get("[MTD]")))}
  {_kpi("Meta (+20% vs año anterior)", fmt_money(ventas.get("[Meta]")), "muted")}
  {_kpi("Cumplimiento vs día", fmt_pct_ratio(ventas.get("[CumplHoy]")))}
</div>
<p>{_brecha_texto(ventas.get("[Brecha]"))}
   &nbsp;·&nbsp; {_meta_dia_texto(ventas.get("[MetaDia]"), ventas.get("[DiasRestantes]"), ventas.get("[Brecha]"))}</p>

<h3>📋 Cartera al cierre</h3>
<p>Cartera total: <b>{fmt_money(cob.get("[CarteraTotal]"))}</b>
   &nbsp;·&nbsp; Vencida: <span class="warn-text">{fmt_money(cob.get("[CarteraVencida]"))}</span>
   ({fmt_pct(cob.get("[PctVencida]"))})</p>

<div class="footer">
Datos en tiempo real desde Contifico · consulta: <b>{now_humano()}</b><br>
Reporte generado automáticamente · {datetime.now().strftime("%d/%m/%Y %H:%M")}<br>
<a href="{REPORT_URL}">Abrir dashboard completo en Power BI</a>
</div>
</body></html>"""


# ============ CLI ============
USAGE = "Uso: python daily_report.py [morning|test-morning|dry-morning]"


def main() -> int:
    if len(sys.argv) != 2:
        print(USAGE)
        return 1

    mode = sys.argv[1]
    today = datetime.now().strftime("%d/%m/%Y")

    # Forzar UTF-8 en stdout para que funcionen acentos/símbolos en consola Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if mode == "dry-morning":
        print(html_morning())
        return 0

    if mode in ("morning", "test-morning"):
        # Phase R (2026-06-06): skip domingo. No abrimos el domingo, así que
        # no hay nada de ayer (sábado) que ya no se haya reportado.
        # IMPORTANTE: lunes el reporte saca ventas del SÁBADO (no del domingo).
        # La lógica de "ayer comercial" la maneja previous_workday() y se inyecta
        # en las queries DAX.
        from datetime import date as _date
        hoy_obj = _date.today()
        if hoy_obj.weekday() == 6 and mode == "morning":  # domingo en modo prod
            print(f"[SKIP] Hoy es domingo ({today}). No se envía reporte.")
            return 0

        html = html_morning()
        subject = f"Resumen comercial - {today}"
        to = MIO if mode == "test-morning" else JEFE
        cc = None if mode == "test-morning" else MIO

        # Gráfico inline: regeneramos la serie y el PNG aquí para attachment.
        # (Sí, lo consulta dos veces; aceptable porque es 1 query barata.)
        inline_images = []
        if HUBSPOT_OK:
            try:
                serie = hubspot_client.leads_por_dia_ultimos_7d()
                png = _generate_leads_chart_png(serie)
                if png:
                    inline_images.append({
                        "name": "leads_7d.png",
                        "content_bytes": png,
                        "content_id": "chart_leads_7d",
                        "content_type": "image/png",
                    })
            except Exception as e:
                print(f"  [WARN] no pude adjuntar chart: {e}", file=sys.stderr)

        send_email(to, subject, html, cc=cc,
                   inline_images=inline_images or None)
        cc_txt = f" (cc: {cc})" if cc else ""
        chart_txt = " [con chart]" if inline_images else " [sin chart]"
        print(f"[OK] Enviado a {to}{cc_txt}{chart_txt}")
        return 0

    print(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
