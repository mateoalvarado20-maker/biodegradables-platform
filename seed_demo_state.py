r"""seed_demo_state — siembra el estado del EQUIPO para el entorno DEMO (Fase 3).

Puebla activity_state (actividades de la semana + cierres de caja + horarios) y
dispatch_state (estados de despacho) con datos ficticios de Andex, para que el
check-in card, el resumen consolidado del equipo y la columna "Estado" del
reporte de logística se vean POBLADOS en la demo.

Seguridad: SOLO corre con DEMO_MODE=1 y un tenant que no sea el real. Escribe en
STATE_DIR (poné uno dedicado al demo, p.ej. STATE_DIR=%TEMP%\andex-demo, para no
tocar el estado de producción). Idempotente: re-ejecutar no duplica.

Uso:
    set DEMO_MODE=1 & set TENANT_CONFIG_SOURCE=yaml & set TENANT_SLUG=andex
    set STATE_DIR=%USERPROFILE%\.andex-demo
    python seed_demo_state.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

_EC_TZ = timezone(timedelta(hours=-5))


def _today() -> date:
    override = os.environ.get("DEMO_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return datetime.now(_EC_TZ).date()


# Actividades sembradas por colaborador de Andex. (id, nombre, tipo, meta, marcas)
# marcas: para diaria = lista de (offset_dias, valor, notas); para semanal = avance.
_SEED = {
    "amora@andexdemo.com": [
        ("prospeccion-correos", "Prospección: correos enviados", "diaria", 50,
         [(1, 52, ""), (0, 38, "media mañana en reunión")]),
        ("codigos-promo", "Cargar códigos promocionales", "diaria", None,
         [(1, 1, ""), (0, 1, "")]),
        ("wordpress-catalogo", "Actualizar catálogo en WordPress", "semanal", None, 60),
    ],
    "cvega@andexdemo.com": [
        ("visitas-clientes", "Visitas a clientes clave", "diaria", 4,
         [(1, 4, ""), (0, 2, "lluvia en la tarde")]),
        ("seguimiento-pedidos", "Seguimiento de pedidos grandes", "semanal", None, 75),
    ],
    "info@andexdemo.com": [
        ("cobranzas-gye", "Cobranzas Guayaquil", "diaria", None,
         [(1, 3, ""), (0, 0, "clientes no contestaron")]),
    ],
    "quito@andexdemo.com": [
        ("cobranzas-uio", "Cobranzas Quito", "diaria", None,
         [(1, 2, ""), (0, 2, "")]),
    ],
    "mtipan@andexdemo.com": [
        ("ruta-entregas", "Ruta de entregas del día", "diaria", None,
         [(1, 9, ""), (0, 7, "")]),
    ],
}

# Cierre de caja (denominaciones) para las dos sucursales, fecha = ayer.
_CIERRES = {
    "info@andexdemo.com": ("Guayaquil",
                           {"b20": 6, "b10": 4, "b5": 5, "b1": 8, "m025": 6, "m010": 5}),
    "quito@andexdemo.com": ("Quito",
                            {"b20": 3, "b10": 3, "b5": 4, "b1": 6, "m050": 3, "m025": 4}),
}


def _guard() -> None:
    if os.environ.get("DEMO_MODE") != "1":
        print("[seed_demo_state] ABORTA: DEMO_MODE != 1. No siembro estado real.",
              file=sys.stderr)
        sys.exit(2)
    if os.environ.get("TENANT_SLUG", "").strip().lower() == "biodegradables":
        print("[seed_demo_state] ABORTA: TENANT_SLUG=biodegradables (cliente real).",
              file=sys.stderr)
        sys.exit(2)


def seed_activities(today: date) -> None:
    import activity_state as st
    wk = st.week_key(today)
    ayer = today - timedelta(days=1)
    for email, acts in _SEED.items():
        st.init_week(email, wk)
        st.set_day_schedule(email, today.isoformat(), estandar=True)
        for aid, nombre, tipo, meta, marcas in acts:
            try:
                st.add_adhoc(aid, nombre, user_email=email, tipo=tipo, meta=meta, wk=wk)
            except ValueError:
                pass  # ya existe (idempotente)
            if tipo == "diaria":
                for offset, valor, notas in marcas:
                    f = (today - timedelta(days=offset)).isoformat()
                    st.mark_daily(aid, valor, user_email=email, fecha=f, notas=notas, wk=wk)
            else:
                st.set_weekly_progress(aid, float(marcas), user_email=email, wk=wk)
    # Cierres de caja (ayer)
    for email, (sucursal, denoms) in _CIERRES.items():
        st.set_cierre_caja(email, ayer.isoformat(), denoms, sucursal=sucursal,
                           notas="Cierre del día (demo)")


def seed_dispatch(today: date) -> int:
    """Marca algunos despachos GYE de ayer (OK/NO/PARCIAL) para la columna Estado."""
    import contifico_client
    import dispatch_state
    ayer = today - timedelta(days=1)
    envios = contifico_client.envios_dia_gye(ayer, dias_atras=1)
    estados = ["OK", "OK", "OK", "PARCIAL", "NO"]
    n = 0
    for env, status in zip(envios, estados):
        razon = {"NO": "transporte sin cupo", "PARCIAL": "entrega parcial, falta 1 bulto"}.get(status, "")
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
    print(f"[seed_demo_state] OK — semana {__import__('activity_state').week_key(today)}, "
          f"{len(_SEED)} colaboradores sembrados, {n} despachos marcados.")
    print(f"  STATE_DIR = {os.environ.get('STATE_DIR') or '(default ~/.claude-agent)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
