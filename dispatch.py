"""CLI para marcar despachos manualmente (Fase 1, antes del bot de Teams).

Uso:

    # Marcar como despachado
    python dispatch.py mark 001-002-000008181 OK

    # Marcar como NO despachado con razón
    python dispatch.py mark 001-001-000012566 NO --razon "Cliente no responde"

    # Marcar parcial
    python dispatch.py mark 001-002-000008180 PARCIAL --razon "Falta 1 caja"

    # Listar todo el estado
    python dispatch.py status

    # Ver solo pendientes (no OK)
    python dispatch.py list-pending

    # Borrar una marca (vuelve a estado "sin marcar")
    python dispatch.py clear 001-002-000008181

Identidad opcional con --por para distinguir quién marca cuando lo usan varias
personas desde el mismo equipo:

    python dispatch.py mark 001-002-000008181 OK --por "jefe_uio"

Cuando el bot de Teams esté listo (Fase 2), las marcas vendrán automáticamente
con `marcado_por="teams:correo@dominio"`.
"""
from __future__ import annotations

import argparse
import sys

import dispatch_state

# Forzar UTF-8 en stdout para evitar crash con caracteres no-cp1252 en Windows.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _cmd_mark(args: argparse.Namespace) -> int:
    status = args.status.upper()
    rec = dispatch_state.mark(
        args.factura,
        status,  # type: ignore[arg-type]
        razon=args.razon or "",
        marcado_por=args.por,
    )
    line = f"[OK] {args.factura}: {rec['status']}"
    if rec["razon"]:
        line += f"  ({rec['razon']})"
    line += f"  | marcado por {rec['marcado_por']} a las {rec['marcado_en']}"
    print(line)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    state = dispatch_state.load()
    if not state:
        print("(state file vacío — sin marcas)")
        return 0
    print(f"{'Factura':<22} {'Estado':<10} {'Por':<14} {'Cuándo':<28} Razón")
    print("-" * 90)
    for factura in sorted(state.keys()):
        rec = state[factura]
        print(
            f"{factura:<22} {rec['status']:<10} {rec['marcado_por']:<14} "
            f"{rec['marcado_en']:<28} {rec.get('razon', '')}"
        )
    return 0


def _cmd_list_pending(args: argparse.Namespace) -> int:
    state = dispatch_state.load()
    pendientes = [(f, r) for f, r in state.items() if r.get("status") != "OK"]
    if not pendientes:
        print("(no hay pendientes — todo marcado como OK o no hay marcas)")
        return 0
    print(f"Pendientes (no marcadas OK): {len(pendientes)}")
    for f, r in sorted(pendientes):
        print(f"  - {f}  [{r['status']}]  {r.get('razon', '')}")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    ok = dispatch_state.clear(args.factura)
    if ok:
        print(f"[OK] {args.factura} borrado del state")
    else:
        print(f"(no existia {args.factura} en el state)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Marcar despachos manualmente.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_mark = sub.add_parser("mark", help="Marcar una factura con estado de despacho.")
    p_mark.add_argument("factura", help="Número de factura (ej. 001-002-000008181).")
    p_mark.add_argument("status", choices=["OK", "NO", "PARCIAL", "ok", "no", "parcial"])
    p_mark.add_argument("--razon", default="", help="Razón si no se despachó / fue parcial.")
    p_mark.add_argument("--por", default="cli", help="Identificador de quién marca (ej. jefe_uio).")
    p_mark.set_defaults(func=_cmd_mark)

    p_status = sub.add_parser("status", help="Listar el estado de despacho de todas las facturas.")
    p_status.set_defaults(func=_cmd_status)

    p_pend = sub.add_parser("list-pending", help="Listar facturas no marcadas OK.")
    p_pend.set_defaults(func=_cmd_list_pending)

    p_clear = sub.add_parser("clear", help="Borrar la marca de una factura.")
    p_clear.add_argument("factura")
    p_clear.set_defaults(func=_cmd_clear)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
