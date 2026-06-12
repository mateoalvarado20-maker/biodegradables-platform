"""Reporte diario de logística — envíos del día anterior.

Trae los documentos (facturas) facturados ayer desde Contifico API, filtra los
que tienen línea de transporte (códigos TRANSP.*), los agrupa por provincia y
genera un correo HTML para Gabriela Sánchez (gerente comercial).

Modos:
    python daily_logistics_report.py morning      # envía a gsanchez (cc Mateo)
    python daily_logistics_report.py test         # envía solo a Mateo
    python daily_logistics_report.py dry          # imprime HTML, no envía

El origen (Quito/Guayaquil) se deduce del prefijo del número de documento:
    001-001-... → Guayaquil (GYE)
    001-002-... → Quito (UIO)

La provincia destino se parsea del string `persona.direccion` por keywords.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import dispatch_state  # noqa: F401  (usado vía _estado_badge en cada Envio)
from contifico_client import get_documentos
from pbi_cloud import send_email

# Ventana del reporte respecto a HOY (ambos inclusive). Por ejemplo:
#   DIAS_DESDE=2, DIAS_HASTA=1 → reporta desde anteayer hasta ayer.
# Las facturas se muestran agrupadas por día dentro del correo.
DIAS_DESDE = 2  # fecha más antigua a incluir
DIAS_HASTA = 1  # fecha más reciente a incluir

# ===== Configuración =====
GABRIELA = "gsanchez@biodegradablesecuador.com"
MIO = "malvarado@biodegradablesecuador.com"

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador (UTC-5)

# Map prefijo documento → ciudad origen (sucursal)
ORIGEN_POR_PREFIJO: dict[str, str] = {
    "001-001": "Guayaquil",
    "001-002": "Quito",
}


def _parse_contifico_date(s: Any) -> date | None:
    """Convierte 'dd/mm/yyyy' (formato Contifico) a date. None si falla."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None

# Productos de transporte (códigos vistos en inventario)
# Las líneas se filtran por nombre que empieza con "TRANSP"
TRANSP_PREFIX = "TRANSP"

# Tipo de transporte: B.E. = propio (intra-ciudad), EXT. = externo (provincia)
def _tipo_transporte(nombre: str) -> str:
    n = nombre.upper()
    if "B.E" in n:
        return "B.E."
    if "EXT" in n:
        return "EXT."
    return "?"


# Nomenclatura de calles de Quito: E10-129, N20-45, S5-8, Oe6-12, etc.
# Es un patrón único de Quito (DMQ) que no existe en otras ciudades.
QUITO_STREET_RE = re.compile(r"\b(?:oe|on|os|n|s|e|w)\d+[-\s]?\d+\b", re.IGNORECASE)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


# ===== Provincias Ecuador (keyword → provincia) =====
# Orden importa: keywords más específicos primero. Lowercase, SIN acentos
# (porque normalizamos la dirección antes de buscar).
PROVINCIA_KEYWORDS: list[tuple[str, str, str]] = [
    # (keyword sin acentos lowercase, provincia, ciudad)
    # Phrases largas/compuestas primero
    ("santo domingo", "Santo Domingo", "Santo Domingo"),
    ("santa elena", "Santa Elena", "Santa Elena"),
    ("la libertad", "Santa Elena", "La Libertad"),
    ("la troncal", "Canar", "La Troncal"),
    ("la mana", "Cotopaxi", "La Mana"),
    ("lago agrio", "Sucumbios", "Lago Agrio"),
    ("nueva loja", "Sucumbios", "Nueva Loja"),
    ("francisco de orellana", "Orellana", "Coca"),
    ("via auca", "Orellana", "Coca"),
    ("san cristobal", "Galapagos", "San Cristobal"),
    ("puerto ayora", "Galapagos", "Puerto Ayora"),
    ("banos de agua santa", "Tungurahua", "Banos"),
    # Ciudades principales
    ("guayaquil", "Guayas", "Guayaquil"),
    ("gye", "Guayas", "Guayaquil"),
    ("duran", "Guayas", "Duran"),
    ("daule", "Guayas", "Daule"),
    ("samborondon", "Guayas", "Samborondon"),
    ("milagro", "Guayas", "Milagro"),
    ("salinas", "Santa Elena", "Salinas"),
    ("quito", "Pichincha", "Quito"),
    ("uio", "Pichincha", "Quito"),
    ("cumbaya", "Pichincha", "Cumbaya"),
    ("tumbaco", "Pichincha", "Tumbaco"),
    ("calderon", "Pichincha", "Calderon"),
    ("cayambe", "Pichincha", "Cayambe"),
    ("sangolqui", "Pichincha", "Sangolqui"),
    ("ruminahui", "Pichincha", "Sangolqui"),
    ("conocoto", "Pichincha", "Conocoto"),
    ("cuenca", "Azuay", "Cuenca"),
    ("gualaceo", "Azuay", "Gualaceo"),
    ("ambato", "Tungurahua", "Ambato"),
    ("banos", "Tungurahua", "Banos"),
    ("ibarra", "Imbabura", "Ibarra"),
    ("otavalo", "Imbabura", "Otavalo"),
    ("cotacachi", "Imbabura", "Cotacachi"),
    ("portoviejo", "Manabi", "Portoviejo"),
    ("manta", "Manabi", "Manta"),
    ("chone", "Manabi", "Chone"),
    ("bahia", "Manabi", "Bahia"),
    ("jipijapa", "Manabi", "Jipijapa"),
    ("machala", "El Oro", "Machala"),
    ("pasaje", "El Oro", "Pasaje"),
    ("santa rosa", "El Oro", "Santa Rosa"),
    ("huaquillas", "El Oro", "Huaquillas"),
    ("pinas", "El Oro", "Pinas"),
    ("catamayo", "Loja", "Catamayo"),
    ("riobamba", "Chimborazo", "Riobamba"),
    ("guano", "Chimborazo", "Guano"),
    ("latacunga", "Cotopaxi", "Latacunga"),
    ("salcedo", "Cotopaxi", "Salcedo"),
    ("azogues", "Canar", "Azogues"),
    ("tulcan", "Carchi", "Tulcan"),
    ("babahoyo", "Los Rios", "Babahoyo"),
    ("quevedo", "Los Rios", "Quevedo"),
    ("ventanas", "Los Rios", "Ventanas"),
    ("vinces", "Los Rios", "Vinces"),
    ("esmeraldas", "Esmeraldas", "Esmeraldas"),
    ("atacames", "Esmeraldas", "Atacames"),
    ("quininde", "Esmeraldas", "Quininde"),
    ("tena", "Napo", "Tena"),
    ("archidona", "Napo", "Archidona"),
    ("puyo", "Pastaza", "Puyo"),
    ("coca", "Orellana", "Coca"),
    ("guaranda", "Bolivar", "Guaranda"),
    ("morona", "Morona Santiago", ""),
    ("zamora", "Zamora Chinchipe", "Zamora"),
    ("baltra", "Galapagos", "Baltra"),
    ("galapagos", "Galapagos", ""),
    # Nombres de provincia explícitos (ciudad queda vacía)
    ("pichincha", "Pichincha", ""),
    ("guayas", "Guayas", ""),
    ("manabi", "Manabi", ""),
    ("azuay", "Azuay", ""),
    ("tungurahua", "Tungurahua", ""),
    ("imbabura", "Imbabura", ""),
    ("chimborazo", "Chimborazo", ""),
    ("cotopaxi", "Cotopaxi", ""),
    ("el oro", "El Oro", ""),
    ("los rios", "Los Rios", ""),
    ("carchi", "Carchi", ""),
    ("napo", "Napo", ""),
    ("pastaza", "Pastaza", ""),
    ("loja", "Loja", "Loja"),
    ("orellana", "Orellana", ""),
    ("sucumbios", "Sucumbios", ""),
    ("canar", "Canar", ""),
    ("bolivar", "Bolivar", ""),
    # Calles conocidas de Quito (señal indirecta cuando no hay nombre de ciudad)
    ("diego de almagro", "Pichincha", "Quito"),
    ("naciones unidas", "Pichincha", "Quito"),
    ("republica del salvador", "Pichincha", "Quito"),
    ("amazonas", "Pichincha", "Quito"),
    ("shyris", "Pichincha", "Quito"),
    ("12 de octubre", "Pichincha", "Quito"),
    ("6 de diciembre", "Pichincha", "Quito"),
    ("eloy alfaro", "Pichincha", "Quito"),
    ("ponce carrasco", "Pichincha", "Quito"),
    ("irlanda", "Pichincha", "Quito"),
]


def _provincia_y_ciudad(direccion: str, *, origen: str | None = None) -> tuple[str, str]:
    """Devuelve (provincia, ciudad) parseadas del string de dirección.

    Si nada matchea pero hay un `origen` ("Quito"/"Guayaquil"), cae a la
    provincia/ciudad de ese origen (asume envío local). Si no, devuelve
    ("Sin identificar", "").
    """
    if direccion:
        d = _strip_accents(direccion).lower()
        for kw, prov, ciudad in PROVINCIA_KEYWORDS:
            if kw in d:
                return prov, ciudad
        # Nomenclatura de calle Quito (E10-129, N20-45, S5-8, Oe6-12)
        if QUITO_STREET_RE.search(d):
            return "Pichincha", "Quito"
    if origen == "Quito":
        return "Pichincha", "Quito"
    if origen == "Guayaquil":
        return "Guayas", "Guayaquil"
    return "Sin identificar", ""


def _origen_desde_documento(doc_numero: str) -> str:
    if not doc_numero or len(doc_numero) < 7:
        return "?"
    prefix = doc_numero[:7]
    return ORIGEN_POR_PREFIJO.get(prefix, "?")


# ===== Procesamiento =====
class Envio:
    __slots__ = (
        "documento", "fecha_emision", "hora_emision", "cliente", "direccion",
        "total_factura", "transporte_cobrado", "tipo_transporte", "origen",
        "provincia", "ciudad",
    )

    def __init__(self, doc: dict[str, Any]) -> None:
        self.documento: str = doc.get("documento", "")
        self.fecha_emision: date | None = _parse_contifico_date(doc.get("fecha_emision"))
        self.hora_emision: str = (doc.get("hora_emision") or "").strip()
        persona = doc.get("persona") or {}
        self.cliente: str = persona.get("razon_social") or "—"
        self.direccion: str = (persona.get("direccion") or "").strip()
        self.total_factura: float = float(doc.get("total") or 0)
        # Sumar líneas TRANSP
        transp_total = 0.0
        transp_tipo = "?"
        for det in (doc.get("detalles") or []):
            nombre = (det.get("producto_nombre") or "").upper()
            if nombre.startswith(TRANSP_PREFIX):
                cantidad = float(det.get("cantidad") or 0)
                precio = float(det.get("precio") or 0)
                transp_total += cantidad * precio
                if transp_tipo == "?":
                    transp_tipo = _tipo_transporte(nombre)
        self.transporte_cobrado = transp_total
        self.tipo_transporte = transp_tipo
        self.origen = _origen_desde_documento(self.documento)
        # B.E. (propio) → destino es la misma ciudad del origen.
        # EXT/desconocido → parsear de la dirección con fallback al origen.
        if transp_tipo == "B.E." and self.origen in ("Quito", "Guayaquil"):
            self.provincia, self.ciudad = (
                ("Pichincha", "Quito") if self.origen == "Quito"
                else ("Guayas", "Guayaquil")
            )
        else:
            self.provincia, self.ciudad = _provincia_y_ciudad(
                self.direccion, origen=self.origen
            )

    @property
    def es_intra_ciudad(self) -> bool:
        """True si el envío se queda dentro de la provincia del origen."""
        return (
            (self.origen == "Quito" and self.provincia == "Pichincha")
            or (self.origen == "Guayaquil" and self.provincia == "Guayas")
        )

    @property
    def estado_record(self) -> dict[str, Any] | None:
        """Registro del dispatch_state si existe, o None."""
        return dispatch_state.get(self.documento)

    @property
    def estado_status(self) -> str | None:
        """Status actual: OK, NO, PARCIAL o None si no se ha marcado."""
        rec = self.estado_record
        return rec.get("status") if rec else None

    @property
    def dias_atras(self) -> int:
        """Días entre fecha_emision y hoy (EC). 0 = de hoy, 1 = ayer, etc."""
        if self.fecha_emision is None:
            return 0
        hoy = datetime.now(LOCAL_TZ).date()
        return (hoy - self.fecha_emision).days


def _tiene_transporte(doc: dict[str, Any]) -> bool:
    if doc.get("anulado"):
        return False
    for det in (doc.get("detalles") or []):
        nombre = (det.get("producto_nombre") or "").upper()
        if nombre.startswith(TRANSP_PREFIX):
            return True
    return False


# ===== Renderizado HTML =====
def _money(v: float) -> str:
    return f"${v:,.2f}"


def _row_style(idx: int) -> str:
    bg = "#ffffff" if idx % 2 == 0 else "#f6f8fa"
    return (
        f'style="background:{bg};padding:8px 10px;'
        f'border-bottom:1px solid #e5e7eb;"'
    )


def _row_style_pendiente(envio: Envio, idx: int) -> str:
    """Background según el status: NO=rojo claro, PARCIAL=naranja, sin marca=rojo más tenue."""
    status = envio.estado_status
    if status == "NO":
        bg = "#fecaca" if idx % 2 == 0 else "#fca5a5"  # rojo
    elif status == "PARCIAL":
        bg = "#fed7aa" if idx % 2 == 0 else "#fdba74"  # naranja
    else:
        bg = "#fee2e2" if idx % 2 == 0 else "#fecaca"  # rojo claro
    return f'style="background:{bg};padding:8px 10px;border-bottom:1px solid #e5e7eb;"'


def _estado_badge(envio: Envio) -> str:
    """Devuelve HTML del badge de estado para una celda."""
    rec = envio.estado_record
    if not rec:
        return '<span style="color:#9ca3af;">—</span>'
    status = rec["status"]
    razon = rec.get("razon", "")
    if status == "OK":
        return '<span style="color:#065f46;font-weight:600;">✓ Despachado</span>'
    if status == "NO":
        tag = '<span style="color:#991b1b;font-weight:600;">✗ No despachado</span>'
        return f'{tag}<br/><span style="color:#7f1d1d;font-size:11px;">{razon}</span>' if razon else tag
    if status == "PARCIAL":
        tag = '<span style="color:#9a3412;font-weight:600;">◐ Parcial</span>'
        return f'{tag}<br/><span style="color:#7c2d12;font-size:11px;">{razon}</span>' if razon else tag
    return f'<span>{status}</span>'


def _render_origen_block(
    parts: list[str], origen_titulo: str,
    intra_lst: list[Envio], ext_lst: list[Envio],
) -> None:
    """Renderiza el bloque 'Envíos desde X' (sub-tablas intra + a provincias).
    Append directo sobre `parts`."""
    n_total = len(intra_lst) + len(ext_lst)
    parts.append(f"""
    <h4 style="margin:14px 0 4px 18px;color:#065f46;font-weight:600;">
        Envíos desde {origen_titulo} ({n_total})
    </h4>
    """)
    if n_total == 0:
        parts.append('<p style="color:#6b7280;margin-left:18px;">— Sin envíos desde este origen —</p>')
        return

    # ---- Sub-tabla: Dentro de la ciudad ----
    parts.append(f"""
    <h5 style="margin:10px 0 4px 18px;color:#065f46;font-weight:600;">Dentro de {origen_titulo} ({len(intra_lst)})</h5>
    """)
    if not intra_lst:
        parts.append('<p style="color:#6b7280;margin-left:18px;">— Sin envíos intra-ciudad —</p>')
    else:
        parts.append("""
        <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:calc(100% - 18px);margin-left:18px;">
          <tr style="background:#065f46;color:#fff;">
            <td style="padding:8px 10px;font-weight:600;">Factura</td>
            <td style="padding:8px 10px;font-weight:600;">Hora</td>
            <td style="padding:8px 10px;font-weight:600;">Cliente</td>
            <td style="padding:8px 10px;font-weight:600;">Dirección</td>
            <td style="padding:8px 10px;font-weight:600;">Estado</td>
            <td style="padding:8px 10px;font-weight:600;text-align:right;">Total</td>
            <td style="padding:8px 10px;font-weight:600;text-align:right;">Transporte</td>
          </tr>
        """)
        for i, e in enumerate(intra_lst):
            parts.append(f"""
            <tr {_row_style(i)}>
              <td style="padding:8px 10px;white-space:nowrap;">{e.documento}</td>
              <td style="padding:8px 10px;white-space:nowrap;color:#6b7280;">{e.hora_emision or '—'}</td>
              <td style="padding:8px 10px;">{e.cliente}</td>
              <td style="padding:8px 10px;">{e.direccion or '—'}</td>
              <td style="padding:8px 10px;">{_estado_badge(e)}</td>
              <td style="padding:8px 10px;text-align:right;">{_money(e.total_factura)}</td>
              <td style="padding:8px 10px;text-align:right;">{_money(e.transporte_cobrado)}</td>
            </tr>
            """)
        st_total = sum(e.total_factura for e in intra_lst)
        st_transp = sum(e.transporte_cobrado for e in intra_lst)
        parts.append(f"""
        <tr style="background:#d1fae5;font-weight:600;">
          <td colspan="5" style="padding:8px 10px;text-align:right;">Subtotal dentro de {origen_titulo}:</td>
          <td style="padding:8px 10px;text-align:right;">{_money(st_total)}</td>
          <td style="padding:8px 10px;text-align:right;">{_money(st_transp)}</td>
        </tr>
        </table>
        """)

    # ---- Sub-tabla: A otras provincias ----
    parts.append(f"""
    <h5 style="margin:10px 0 4px 18px;color:#065f46;font-weight:600;">A otras provincias desde {origen_titulo} ({len(ext_lst)})</h5>
    """)
    if not ext_lst:
        parts.append('<p style="color:#6b7280;margin-left:18px;">— Sin envíos a provincia —</p>')
    else:
        parts.append("""
        <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:calc(100% - 18px);margin-left:18px;">
          <tr style="background:#065f46;color:#fff;">
            <td style="padding:8px 10px;font-weight:600;">Factura</td>
            <td style="padding:8px 10px;font-weight:600;">Hora</td>
            <td style="padding:8px 10px;font-weight:600;">Cliente</td>
            <td style="padding:8px 10px;font-weight:600;">Ciudad</td>
            <td style="padding:8px 10px;font-weight:600;">Provincia</td>
            <td style="padding:8px 10px;font-weight:600;">Estado</td>
            <td style="padding:8px 10px;font-weight:600;text-align:right;">Total</td>
            <td style="padding:8px 10px;font-weight:600;text-align:right;">Transporte</td>
          </tr>
        """)
        for i, e in enumerate(ext_lst):
            parts.append(f"""
            <tr {_row_style(i)}>
              <td style="padding:8px 10px;white-space:nowrap;">{e.documento}</td>
              <td style="padding:8px 10px;white-space:nowrap;color:#6b7280;">{e.hora_emision or '—'}</td>
              <td style="padding:8px 10px;">{e.cliente}</td>
              <td style="padding:8px 10px;">{e.ciudad or '—'}</td>
              <td style="padding:8px 10px;">{e.provincia}</td>
              <td style="padding:8px 10px;">{_estado_badge(e)}</td>
              <td style="padding:8px 10px;text-align:right;">{_money(e.total_factura)}</td>
              <td style="padding:8px 10px;text-align:right;">{_money(e.transporte_cobrado)}</td>
            </tr>
            """)
        st_total = sum(e.total_factura for e in ext_lst)
        st_transp = sum(e.transporte_cobrado for e in ext_lst)
        parts.append(f"""
        <tr style="background:#d1fae5;font-weight:600;">
          <td colspan="6" style="padding:8px 10px;text-align:right;">Subtotal a provincia desde {origen_titulo}:</td>
          <td style="padding:8px 10px;text-align:right;">{_money(st_total)}</td>
          <td style="padding:8px 10px;text-align:right;">{_money(st_transp)}</td>
        </tr>
        </table>
        """)


def _render_html(
    envios: list[Envio], fecha_desde: date, fecha_hasta: date
) -> str:
    if fecha_desde == fecha_hasta:
        rango_largo = fecha_desde.strftime('%d/%m/%Y')
        rango_corto = fecha_desde.strftime('%d/%m')
    else:
        rango_largo = f"{fecha_desde.strftime('%d/%m/%Y')} al {fecha_hasta.strftime('%d/%m/%Y')}"
        rango_corto = f"{fecha_desde.strftime('%d/%m')} al {fecha_hasta.strftime('%d/%m')}"

    if not envios:
        return f"""
        <html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2937;">
        <h2 style="color:#065f46;">Reporte de logística — {rango_largo}</h2>
        <p>No se registraron facturas con envío para este rango.</p>
        </body></html>
        """

    total_facturas = len(envios)
    total_ventas = sum(e.total_factura for e in envios)
    total_transporte = sum(e.transporte_cobrado for e in envios)

    # Desglose por origen y por tipo
    desde_q = [e for e in envios if e.origen == "Quito"]
    desde_g = [e for e in envios if e.origen == "Guayaquil"]
    transp_ext = sum(e.transporte_cobrado for e in envios if e.tipo_transporte == "EXT.")
    transp_be = sum(e.transporte_cobrado for e in envios if e.tipo_transporte == "B.E.")
    transp_q = sum(e.transporte_cobrado for e in desde_q)
    transp_g = sum(e.transporte_cobrado for e in desde_g)

    # Externos vs intra-ciudad
    ext_desde_q = [e for e in desde_q if not e.es_intra_ciudad]
    ext_desde_g = [e for e in desde_g if not e.es_intra_ciudad]
    intra_q = [e for e in desde_q if e.es_intra_ciudad]
    intra_g = [e for e in desde_g if e.es_intra_ciudad]

    # Agrupar por provincia (para tabla resumen) con desglose origen
    por_prov: dict[str, list[Envio]] = defaultdict(list)
    for e in envios:
        por_prov[e.provincia].append(e)
    def _prov_sort(k: str) -> tuple[int, str]:
        prio = {"Pichincha": 0, "Guayas": 1}
        return (prio.get(k, 2), k)
    provs_orden = sorted(por_prov.keys(), key=_prov_sort)

    parts: list[str] = []
    parts.append(f"""
    <html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2937;max-width:1000px;">
    <h2 style="color:#065f46;margin-bottom:4px;">Reporte de logística — {rango_largo}</h2>
    <p style="color:#6b7280;margin-top:0;">Facturas del {rango_largo} con línea de transporte (TRANSP.*).</p>

    <h3 style="border-bottom:2px solid #065f46;padding-bottom:4px;">Resumen</h3>
    <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <tr><td style="font-weight:600;">Facturas con envío:</td>
          <td>{total_facturas} &nbsp;
            <span style="color:#6b7280;">({len(desde_q)} desde Quito · {len(desde_g)} desde Guayaquil)</span></td></tr>
      <tr><td style="font-weight:600;">Total ventas (facturas con envío):</td>
          <td>{_money(total_ventas)}</td></tr>
      <tr><td style="font-weight:600;">Total transporte cobrado:</td>
          <td>{_money(total_transporte)}</td></tr>
      <tr><td style="padding-left:18px;color:#6b7280;">· por tipo</td>
          <td style="color:#6b7280;">Externo (EXT): {_money(transp_ext)} &nbsp;·&nbsp; Propio (B.E.): {_money(transp_be)}</td></tr>
      <tr><td style="padding-left:18px;color:#6b7280;">· por origen</td>
          <td style="color:#6b7280;">Quito: {_money(transp_q)} &nbsp;·&nbsp; Guayaquil: {_money(transp_g)}</td></tr>
    </table>
    """)

    # Agrupar envíos por día (fecha_emision) — orden cronológico (más viejos arriba)
    envios_por_dia: dict[date, list[Envio]] = defaultdict(list)
    for e in envios:
        if e.fecha_emision:
            envios_por_dia[e.fecha_emision].append(e)
    dias_orden = sorted(envios_por_dia.keys())

    # Para cada día, una sección con el detalle por origen (GYE → Quito)
    dia_nombres_es = {
        0: "Lunes", 1: "Martes", 2: "Miércoles", 3: "Jueves",
        4: "Viernes", 5: "Sábado", 6: "Domingo",
    }
    for d in dias_orden:
        envios_dia = envios_por_dia[d]
        intra_g_d = [e for e in envios_dia if e.origen == "Guayaquil" and e.es_intra_ciudad]
        intra_q_d = [e for e in envios_dia if e.origen == "Quito" and e.es_intra_ciudad]
        ext_g_d = [e for e in envios_dia if e.origen == "Guayaquil" and not e.es_intra_ciudad]
        ext_q_d = [e for e in envios_dia if e.origen == "Quito" and not e.es_intra_ciudad]
        dia_label = f"{dia_nombres_es.get(d.weekday(), '')} {d.strftime('%d/%m/%Y')}"
        total_ventas_d = sum(e.total_factura for e in envios_dia)
        total_transp_d = sum(e.transporte_cobrado for e in envios_dia)
        parts.append(f"""
        <h3 style="border-bottom:2px solid #065f46;padding-bottom:4px;margin-top:28px;">
            Día {dia_label} ({len(envios_dia)} envíos)
        </h3>
        <p style="color:#6b7280;margin:4px 0 8px 0;font-size:13px;">
            Ventas {_money(total_ventas_d)} · Transporte cobrado {_money(total_transp_d)}
        </p>
        """)
        _render_origen_block(parts, "Guayaquil", intra_g_d, ext_g_d)
        _render_origen_block(parts, "Quito", intra_q_d, ext_q_d)

    # 3) Tabla unificada por provincia (al final, vista resumen)
    parts.append("""
    <h3 style="border-bottom:2px solid #065f46;padding-bottom:4px;margin-top:24px;">Envíos por provincia (resumen)</h3>
    <table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:850px;">
      <tr style="background:#065f46;color:#fff;">
        <td style="padding:8px 10px;font-weight:600;">Provincia</td>
        <td style="padding:8px 10px;font-weight:600;text-align:right;"># Envíos</td>
        <td style="padding:8px 10px;font-weight:600;text-align:right;">Desde Quito</td>
        <td style="padding:8px 10px;font-weight:600;text-align:right;">Desde Guayaquil</td>
        <td style="padding:8px 10px;font-weight:600;text-align:right;">Total ventas</td>
        <td style="padding:8px 10px;font-weight:600;text-align:right;">Transporte cobrado</td>
      </tr>
    """)
    for i, prov in enumerate(provs_orden):
        lst = por_prov[prov]
        n = len(lst)
        n_q = sum(1 for e in lst if e.origen == "Quito")
        n_g = sum(1 for e in lst if e.origen == "Guayaquil")
        tv = sum(e.total_factura for e in lst)
        tt = sum(e.transporte_cobrado for e in lst)
        parts.append(f"""
        <tr {_row_style(i)}>
          <td style="padding:8px 10px;">{prov}</td>
          <td style="padding:8px 10px;text-align:right;">{n}</td>
          <td style="padding:8px 10px;text-align:right;">{n_q}</td>
          <td style="padding:8px 10px;text-align:right;">{n_g}</td>
          <td style="padding:8px 10px;text-align:right;">{_money(tv)}</td>
          <td style="padding:8px 10px;text-align:right;">{_money(tt)}</td>
        </tr>
        """)
    parts.append("</table>")

    parts.append(f"""
    <p style="margin-top:32px;color:#6b7280;font-size:12px;">
      Fuente: Contifico API · Generado {datetime.now(LOCAL_TZ).strftime('%d/%m/%Y %H:%M')} ECT
    </p>
    </body></html>
    """)
    return "".join(parts)


def build_envios(
    fecha_inicial: date, fecha_final: date | None = None
) -> list[Envio]:
    """Devuelve envíos (facturas con transporte) en el rango. Si solo se pasa
    una fecha, busca exactamente ese día."""
    if fecha_final is None:
        fecha_final = fecha_inicial
    docs = get_documentos(fecha_inicial, fecha_final, tipo="FAC")
    return [Envio(d) for d in docs if _tiene_transporte(d)]


def filtrar_pendientes(envios: list[Envio]) -> list[Envio]:
    """De una lista de envíos, devuelve los que NO están marcados como OK.

    Incluye: marcados como NO/PARCIAL + nunca marcados.
    """
    return [e for e in envios if e.estado_status != "OK"]


def main() -> int:
    modo = sys.argv[1] if len(sys.argv) >= 2 else "morning"
    hoy = date.today()
    fecha_desde = hoy - timedelta(days=DIAS_DESDE)
    fecha_hasta = hoy - timedelta(days=DIAS_HASTA)

    envios = build_envios(fecha_desde, fecha_hasta)
    html = _render_html(envios, fecha_desde, fecha_hasta)
    if fecha_desde == fecha_hasta:
        rango = fecha_desde.strftime('%d/%m')
    else:
        rango = f"{fecha_desde.strftime('%d/%m')}–{fecha_hasta.strftime('%d/%m')}"
    subject = f"Logística {rango} — {len(envios)} envíos"

    if modo == "dry":
        print(html)
        print(f"\n--- {len(envios)} envíos en {fecha_desde}..{fecha_hasta} ---", file=sys.stderr)
        return 0
    if modo == "test":
        send_email(MIO, f"[TEST] {subject}", html)
        print(f"Enviado a {MIO}: {len(envios)} envíos")
        return 0
    # morning (default) — solo a Gabriela, sin CC
    send_email(GABRIELA, subject, html)
    print(f"Enviado a {GABRIELA}: {len(envios)} envíos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
