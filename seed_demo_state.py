r"""seed_demo_state — siembra el estado del EQUIPO para el entorno DEMO (Fase 3+).

Puebla activity_state y dispatch_state con un día COMPLETO y realista de Andex,
para que el check-in, el resumen consolidado del equipo y la logística se vean
"como los llenan los colaboradores": actividades marcadas, cobranzas contactadas
/pendientes con notas, cierre de caja por sucursal, chocolates, y la ruta del
chofer (entregas + caja chica). Todo para HOY, que es lo que resume el correo de
las 6:30 PM.

Las cobranzas se derivan de la cartera vencida sintética (mismos deudores que el
reporte comercial) → coherencia entre artefactos.

Seguridad: SOLO corre con DEMO_MODE=1 y tenant != real. Escribe en STATE_DIR
(usá uno dedicado al demo). Idempotente.

Uso:
    set DEMO_MODE=1 & set TENANT_CONFIG_SOURCE=yaml & set TENANT_SLUG=andex
    set STATE_DIR=%USERPROFILE%\.andex-demo
    python seed_demo_state.py
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

_EC_TZ = timezone(timedelta(hours=-5))


def _today() -> date:
    override = os.environ.get("DEMO_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return datetime.now(_EC_TZ).date()


# Actividades de oficina. (email, [(id, nombre, tipo, meta, marcas)])
# marcas: diaria=[(offset_dias, valor, notas)]; semanal=(avance, notas)
_OFICINA = {
    "amora@andexdemo.com": [
        ("prospeccion-correos", "Prospección: correos enviados", "diaria", 50,
         [(1, 52, ""), (0, 44, "media mañana en reunión de pipeline")]),
        ("codigos-promo", "Cargar códigos promocionales del mes", "diaria", None,
         [(1, 1, ""), (0, 1, "")]),
        ("wordpress-catalogo", "Actualizar catálogo en WordPress", "semanal", None,
         (60, "subí 18 de 30 SKUs nuevos; faltan fotos de empaque")),
    ],
    "cvega@andexdemo.com": [
        ("visitas-clientes", "Visitas a clientes clave", "diaria", 4,
         [(1, 4, ""), (0, 3, "una visita se reprogramó por lluvia")]),
        ("seguimiento-pedidos", "Seguimiento de pedidos grandes", "semanal", None,
         (75, "Hotel Costa Azul confirmó pedido mensual; falta cerrar 2 distribuidores")),
    ],
}

# Asistentes de sucursal: (email, codigo_sucursal, nombre_ciudad, fondo, denoms_cierre)
_ASISTENTES = [
    ("info@andexdemo.com", "GYE", "Guayaquil",
     {"b20": 10, "b10": 6, "b5": 6, "b1": 18, "m025": 8, "m010": 12, "m005": 6}),
    ("quito@andexdemo.com", "UIO", "Quito",
     {"b20": 4, "b10": 5, "b5": 4, "b1": 12, "m050": 4, "m025": 6, "m010": 8}),
]
_NOTAS_OK = ["se comprometió a pagar el viernes", "abonó 50% hoy",
             "transferencia en proceso", "factura reenviada, paga mañana"]
_NOTAS_NO = ["no contestó el teléfono", "pidió reprogramar la llamada",
             "responsable de pagos fuera de la ciudad"]


def _guard() -> None:
    if os.environ.get("DEMO_MODE") != "1":
        print("[seed_demo_state] ABORTA: DEMO_MODE != 1. No siembro estado real.",
              file=sys.stderr)
        sys.exit(2)
    if os.environ.get("TENANT_SLUG", "").strip().lower() == "biodegradables":
        print("[seed_demo_state] ABORTA: TENANT_SLUG=biodegradables (cliente real).",
              file=sys.stderr)
        sys.exit(2)


def _seed_oficina(st, today: date) -> None:
    wk = st.week_key(today)
    for email, acts in _OFICINA.items():
        st.init_week(email, wk)
        st.set_day_schedule(email, today.isoformat(), estandar=True)
        for aid, nombre, tipo, meta, marcas in acts:
            try:
                st.add_adhoc(aid, nombre, user_email=email, tipo=tipo, meta=meta, wk=wk)
            except ValueError:
                pass
            if tipo == "diaria":
                for offset, valor, notas in marcas:
                    f = (today - timedelta(days=offset)).isoformat()
                    st.mark_daily(aid, valor, user_email=email, fecha=f, notas=notas, wk=wk)
            else:
                avance, notas = marcas
                st.set_weekly_progress(aid, float(avance), user_email=email, notas=notas, wk=wk)


def _seed_asistentes(st, today: date) -> None:
    import contifico_client as cc
    wk = st.week_key(today)
    t = today.isoformat()
    for email, ccode, ciudad, denoms in _ASISTENTES:
        st.init_week(email, wk)
        st.set_day_schedule(email, t, estandar=True)
        # Cobranzas = top deudores vencidos de la ciudad (coherente con el comercial)
        try:
            deudores = cc.cartera_vencida_por_ciudad(ccode, 4, fecha_referencia=today)
        except Exception:
            deudores = []
        for i, d in enumerate(deudores):
            slug = "cobranza-" + re.sub(r"[^a-z0-9]+", "-", d["cliente"].lower()).strip("-")
            nombre = (f'📞 Cobranza: {d["cliente"]} — '
                      f'${d["saldo_vencido"]:,.0f} ({d["dias_atraso_max"]}d atraso)')
            try:
                st.add_adhoc(slug, nombre, user_email=email, tipo="diaria", wk=wk)
            except ValueError:
                pass
            if i < 3:  # 3 contactadas con nota, el resto pendiente
                st.mark_daily(slug, 1, user_email=email, fecha=t,
                              notas=_NOTAS_OK[i % len(_NOTAS_OK)], wk=wk)
            else:
                st.mark_daily(slug, 0, user_email=email, fecha=t,
                              notas=_NOTAS_NO[i % len(_NOTAS_NO)], wk=wk)
        # Chocolates de reviews
        st.set_chocolates_stock_inicial(email, 20, wk=wk)
        st.add_chocolates_entrega(email, t, 3, wk=wk)
        # Cierre de caja de HOY (lo que el correo del equipo muestra)
        st.set_cierre_caja(email, t, denoms, sucursal=ciudad, notas="Cierre del día (demo)")


def _seed_chofer(st, today: date) -> None:
    """Ruta + entregas + caja chica del chofer (bloque José en el consolidado)."""
    email = next((e for e, p in __import__("core_config").PEOPLE.items()
                  if p.get("role") == "chofer"), None)
    if not email:
        return
    t = today.isoformat()
    st.set_day_schedule(email, t, estandar=True)
    st.set_caja_chica_inicial(email, 50.0)
    st.start_ruta(email, t)
    # (cliente, dirección, monto, entregado, observación, pago_envio)
    destinos = [
        ("Minimarket El Ahorro", "Cdla. Alborada Mz 5 Villa 3, Guayaquil", 145.0, True, "", 0.0),
        ("Restaurante La Sazón", "Av. 9 de Octubre 1234, Guayaquil", 320.0, True,
         "cliente pidió factura física la próxima", 0.0),
        ("Hotel Costa Azul", "Vía a Samborondón km 2.5, Guayas", 880.0, True, "", 3.5),
        ("Despensa Doña Marta", "Durán, km 4.5 vía Durán-Tambo", 210.0, False, "", 0.0),
        ("Comercial Su Despensa", "Av. Francisco de Orellana, Guayaquil", 175.0, True,
         "dejado con el guardia, firma adjunta", 0.0),
    ]
    for cliente, direccion, monto, entregado, obs, pago in destinos:
        r = st.add_destino_adhoc(email, cliente, direccion, monto=monto, fecha=t)
        fid = r["factura_id"]
        st.marcar_entrega(
            email, fid, entregado,
            razon=None if entregado else "local cerrado, reprogramar mañana",
            observacion=obs or None, pago_envio=(pago or None),
            cliente_label=cliente, fecha=t,
        )
    st.add_caja_chica_movimiento(email, "gasto", 1.50, "Peaje vía Samborondón")
    st.end_ruta(email, fecha=t)


def seed_activities(today: date) -> None:
    import activity_state as st
    _seed_oficina(st, today)
    _seed_asistentes(st, today)
    _seed_chofer(st, today)


def seed_dispatch(today: date) -> int:
    """Marca algunos despachos GYE (OK/PARCIAL/NO) para la columna Estado."""
    import contifico_client
    import dispatch_state
    ayer = today - timedelta(days=1)
    envios = contifico_client.envios_dia_gye(ayer, dias_atras=1)
    estados = ["OK", "OK", "OK", "PARCIAL", "NO"]
    n = 0
    for env, status in zip(envios, estados):
        razon = {"NO": "transporte sin cupo",
                 "PARCIAL": "entrega parcial, falta 1 bulto"}.get(status, "")
        dispatch_state.mark(env["documento"], status, razon=razon, marcado_por="demo")
        n += 1
    return n


def main() -> int:
    _guard()
    today = _today()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    seed_activities(today)
    n = seed_dispatch(today)
    import activity_state as st
    print(f"[seed_demo_state] OK — semana {st.week_key(today)}, "
          f"oficina+sucursales+chofer sembrados, {n} despachos marcados.")
    print(f"  STATE_DIR = {os.environ.get('STATE_DIR') or '(default ~/.claude-agent)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
