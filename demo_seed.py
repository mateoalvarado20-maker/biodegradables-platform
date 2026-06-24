"""demo_seed — generador de datos sintéticos COHERENTES para el entorno DEMO.

Una sola fuente de verdad: un conjunto de facturas ficticias generado de forma
DETERMINISTA (semilla fija) y relativo a "hoy". De ahí se derivan TODOS los KPIs
(ventas, cumplimiento, top vendedores/clientes, cartera, logística), así que
cuadran por construcción: las facturas generan la cartera, los indicadores se
calculan de las mismas facturas, etc.

Solo se usa cuando DEMO_MODE=1 (lo enganchan demo_contifico / demo_hubspot).
Nada acá es real: nombres de clientes, vendedores y productos son inventados.

Determinismo: la estructura es estable entre corridas del mismo día (semilla
fija + fechas relativas a hoy). Override de "hoy" con DEMO_TODAY=YYYY-MM-DD para
tests reproducibles.
"""
from __future__ import annotations

import os
import random
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

_EC_TZ = timezone(timedelta(hours=-5))
DEMO_SEED = 20260101          # semilla fija → dataset estable
HISTORY_MONTHS = 14           # meses de histórico (cubre YoY para el forecast)

# ---- Catálogo fijo (ficticio) -------------------------------------------------
VENDEDORES = [
    {"nombre": "Diana Cevallos", "ciudad": "UIO", "cuota": 32000.0},
    {"nombre": "Jorge Andrade", "ciudad": "UIO", "cuota": 28000.0},
    {"nombre": "Verónica Suárez", "ciudad": "GYE", "cuota": 38000.0},
    {"nombre": "Luis Maldonado", "ciudad": "GYE", "cuota": 30000.0},
]

# (codigo, nombre, categoria, precio_unitario)
PRODUCTOS = [
    ("AND-DESC-001", "Vaso plástico 7oz x100", "Descartables", 2.40),
    ("AND-DESC-002", "Plato hondo biodegradable x50", "Descartables", 4.80),
    ("AND-DESC-003", "Cuchara plástica x100", "Descartables", 1.90),
    ("AND-DESC-004", "Contenedor 2 div. x50", "Descartables", 9.50),
    ("AND-DESC-005", "Vaso foam 12oz x25", "Descartables", 3.20),
    ("AND-LIMP-001", "Desinfectante galón", "Limpieza", 6.75),
    ("AND-LIMP-002", "Jabón líquido manos galón", "Limpieza", 8.30),
    ("AND-LIMP-003", "Funda de basura 30x36 x10", "Limpieza", 2.10),
    ("AND-LIMP-004", "Cloro 1L x12", "Limpieza", 14.40),
    ("AND-LIMP-005", "Desengrasante 1L", "Limpieza", 4.95),
    ("AND-CUID-001", "Papel higiénico institucional x12", "Cuidado personal", 11.90),
    ("AND-CUID-002", "Toalla de mano Z x200", "Cuidado personal", 9.80),
    ("AND-CUID-003", "Jabón espuma recarga 1L", "Cuidado personal", 7.20),
    ("AND-CUID-004", "Papel toalla industrial rollo", "Cuidado personal", 13.50),
    ("AND-EMP-001", "Film stretch 18'' rollo", "Empaque", 16.00),
    ("AND-EMP-002", "Cinta embalaje 48mm x6", "Empaque", 8.90),
    ("AND-EMP-003", "Caja cartón 40x30x30 x25", "Empaque", 22.50),
    ("AND-EMP-004", "Bolsa kraft #20 x100", "Empaque", 5.60),
]

# Nombres ficticios para clientes (combinatoria → ~90 cuentas únicas)
_TIPO_CLIENTE = [
    "Minimarket", "Comercial", "Restaurante", "Hotel", "Despensa", "Distribuidora",
    "Cafetería", "Bazar", "Tienda", "Autoservicio", "Panadería", "Marisquería",
]
_NOMBRE_CLIENTE = [
    "El Ahorro", "La Esquina", "Su Despensa", "Don Pepe", "La Económica", "El Rosado",
    "Mi Caserita", "La Ganga", "Buen Precio", "La Favorita Express", "El Trébol",
    "Costa Azul", "La Sazón", "El Fogón", "Doña Marta", "San José", "La Bahía",
    "El Vecino", "Akí", "La Central", "Patacón", "El Manaba", "La Perla", "Tía Mary",
]
# Direcciones: ciudad propia + otras provincias (para que logística muestre variedad)
_DIRS_UIO = [
    "Av. Amazonas N34-120 y Av. Naciones Unidas, Quito",
    "Av. 6 de Diciembre y Eloy Alfaro, Quito",
    "Calle Venezuela 1042 y Mejía, Centro, Quito",
    "Av. Mariscal Sucre y Av. Morán Valverde, Quito",
    "Sangolquí, Valle de los Chillos, Pichincha",
]
_DIRS_GYE = [
    "Av. 9 de Octubre 1234 y Malecón, Guayaquil",
    "Cdla. Alborada Mz 5 Villa 3, Guayaquil",
    "Av. Francisco de Orellana, Kennedy Norte, Guayaquil",
    "Durán, km 4.5 vía Durán-Tambo, Guayas",
    "Vía a Samborondón km 2.5, Guayas",
]
_DIRS_OTRAS = [
    "Av. 4 de Noviembre y Malecón, Manta, Manabí",
    "Calle Bolívar y Sucre, Cuenca, Azuay",
    "Av. Cevallos 05-12, Ambato, Tungurahua",
    "Av. Olmedo y 10 de Agosto, Machala, El Oro",
    "Cdla. Universitaria, Portoviejo, Manabí",
    "Av. Amazonas, Riobamba, Chimborazo",
]


def _today() -> date:
    override = os.environ.get("DEMO_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return datetime.now(_EC_TZ).date()


def _documento(ciudad: str, n: int) -> str:
    """Prefijo de establecimiento: GYE=001-001, UIO=001-002 (igual que el real)."""
    prefijo = "001-001" if ciudad == "GYE" else "001-002"
    return f"{prefijo}-{n:09d}"


@lru_cache(maxsize=4)
def _build(today_iso: str) -> dict[str, Any]:
    """Genera el dataset completo para 'hoy'. Cacheado por día."""
    today = date.fromisoformat(today_iso)
    rng = random.Random(DEMO_SEED)

    # --- Clientes (~90) ---
    clientes: list[dict] = []
    combos = [(t, n) for t in _TIPO_CLIENTE for n in _NOMBRE_CLIENTE]
    rng.shuffle(combos)
    for i, (tipo, nom) in enumerate(combos[:90]):
        ciudad = "UIO" if i % 2 == 0 else "GYE"
        vend = rng.choice([v for v in VENDEDORES if v["ciudad"] == ciudad])
        # 60% crédito (15/30/45 días), 40% contado
        plazo = rng.choice([0, 0, 15, 30, 30, 45])
        intra = rng.random() < 0.7  # 70% entrega intra-ciudad
        if ciudad == "UIO":
            direccion = rng.choice(_DIRS_UIO if intra else _DIRS_OTRAS)
        else:
            direccion = rng.choice(_DIRS_GYE if intra else _DIRS_OTRAS)
        clientes.append({
            "id": f"C{i:04d}",
            "razon_social": f"{tipo} {nom}",
            "ciudad": ciudad,
            "vendedor": vend["nombre"],
            "direccion": direccion,
            "telefono": f"09{rng.randint(70000000, 99999999)}",
            "plazo_dias": plazo,
            "intra": intra,  # entrega dentro de la ciudad vs otra provincia
            # propensión a pagar tarde (genera cartera vencida en ~12%)
            "moroso": rng.random() < 0.12,
        })

    # --- Facturas (documentos) sobre el histórico hasta AYER ---
    start = (today.replace(day=1) - timedelta(days=31 * HISTORY_MONTHS)).replace(day=1)
    invoices: list[dict] = []
    correlativo = {"UIO": 1000, "GYE": 1000}
    d = start
    while d < today:  # hasta ayer inclusive (no genera "hoy")
        if d.weekday() != 6:  # sin domingos
            # más facturas a fin de mes (pico de pedidos)
            base = 16 if d.day < 25 else 24
            n_fac = rng.randint(base - 6, base + 6)
            for _ in range(n_fac):
                cli = rng.choice(clientes)
                ciudad = cli["ciudad"]
                correlativo[ciudad] += 1
                # 2-5 líneas
                n_lineas = rng.randint(2, 5)
                detalles = []
                subtotal = 0.0
                for _ in range(n_lineas):
                    cod, nom, _cat, precio = rng.choice(PRODUCTOS)
                    cant = float(rng.randint(1, 24))
                    detalles.append({
                        "producto_codigo": cod,
                        "producto_nombre": nom,
                        "cantidad": cant,
                        "precio": precio,
                    })
                    subtotal += cant * precio
                # Flete: inter-provincia SIEMPRE lleva transporte externo;
                # intra-ciudad ~35% lleva entrega propia (B.E.). El nombre usa
                # "TRANSP." con punto (no matchea "transparente" — ver
                # contifico_client._tiene_transporte_item).
                if not cli["intra"]:
                    flete = round(rng.uniform(8, 25), 2)
                    detalles.append({
                        "producto_codigo": "TRANSP-EXT",
                        "producto_nombre": "TRANSP. EXT. 12%",
                        "cantidad": 1.0, "precio": flete,
                    })
                    subtotal += flete
                elif rng.random() < 0.35:
                    flete = round(rng.uniform(3, 6), 2)
                    detalles.append({
                        "producto_codigo": "TRANSP-BE",
                        "producto_nombre": "TRANSP. B.E.",
                        "cantidad": 1.0, "precio": flete,
                    })
                    subtotal += flete
                subtotal = round(subtotal, 2)
                iva = round(subtotal * 0.15, 2)
                total = round(subtotal + iva, 2)
                anulado = rng.random() < 0.01  # ~1% anuladas
                saldo = _saldo_para(d, today, cli, total, rng)
                invoices.append({
                    "id": f"F{ciudad}{correlativo[ciudad]}",
                    "documento": _documento(ciudad, correlativo[ciudad]),
                    "fecha_emision": d.strftime("%d/%m/%Y"),
                    "_fecha": d,
                    "_ciudad": ciudad,
                    "subtotal": subtotal,
                    "total": total,
                    "saldo": 0.0 if anulado else saldo,
                    "anulado": anulado,
                    "plazo_dias": cli["plazo_dias"],
                    "persona": {
                        "id": cli["id"],
                        "razon_social": cli["razon_social"],
                        "direccion": cli["direccion"],
                        "telefonos": cli["telefono"],
                    },
                    "vendedor": {"razon_social": cli["vendedor"]},
                    "detalles": detalles,
                })
        d += timedelta(days=1)

    return {
        "today": today,
        "clientes": clientes,
        "vendedores": VENDEDORES,
        "productos": PRODUCTOS,
        "invoices": invoices,
    }


def _saldo_para(emision: date, today: date, cli: dict, total: float, rng: random.Random) -> float:
    """Modela el saldo pendiente de una factura:
    - Contado (plazo 0) → siempre 0 (pagada al instante).
    - Crédito → pendiente hasta emision + plazo (+ atraso). Morosos pagan mucho
      más tarde → generan cartera VENCIDA. El resto, una vez pasado el plazo, se
      considera cobrado (saldo 0)."""
    plazo = cli["plazo_dias"]
    if plazo == 0:
        return 0.0
    atraso = rng.randint(0, 8) if not cli["moroso"] else rng.randint(20, 75)
    fecha_pago = emision + timedelta(days=plazo + atraso)
    return total if today <= fecha_pago else 0.0


def dataset() -> dict[str, Any]:
    """Dataset demo para hoy (cacheado)."""
    return _build(_today().isoformat())


def vigentes(invoices: list[dict]) -> list[dict]:
    """Facturas que cuentan para ventas (no anuladas)."""
    return [f for f in invoices if not f["anulado"]]
