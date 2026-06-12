"""CLI para tracking de actividades semanales (Fase 1, antes del bot de Teams).

Uso:

    # Marcar valor de una actividad diaria
    python activity_tracker.py done apollo-correos --valor 72
    python activity_tracker.py done tiktok-video --valor 1 \
        --evidencia "https://tiktok.com/@biodegradablesecuador/video/..."

    # Marcar % de avance de una actividad semanal/proyecto
    python activity_tracker.py progress codigos-contifico --avance 25 \
        --notas "5 de 20 códigos modificados, quedan los proveedores grandes"

    # Agregar actividad ad-hoc a la semana actual
    python activity_tracker.py add reunion-daniel "Reunión 1on1 con Daniel" --tipo unica
    python activity_tracker.py add curso-ml "Curso MercadoLibre" \
        --tipo semanal --meta 100 --unidad %

    # Ver estado de la semana
    python activity_tracker.py status
    python activity_tracker.py week --wk 2026-W21   # ver una semana pasada

    # Borrar una actividad de la semana actual (no del template)
    python activity_tracker.py remove reunion-daniel

Cuando el bot de Teams esté listo (Fase 2), estos comandos se mapean a slash
commands en Teams (/done, /progress, /add, /status) y leen/escriben el mismo
state JSON — cero migración.
"""
from __future__ import annotations

import argparse
import sys

import activity_state

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _cmd_done(args: argparse.Namespace) -> int:
    rec = activity_state.mark_daily(
        args.activity_id,
        args.valor,
        fecha=args.fecha,
        evidencia=args.evidencia or "",
        notas=args.notas or "",
    )
    msg = f"[OK] {args.activity_id}: valor={rec['valor']}"
    if rec.get("evidencia"):
        msg += f" | evidencia={rec['evidencia']}"
    if rec.get("notas"):
        msg += f" | notas={rec['notas']}"
    print(msg)
    return 0


def _cmd_progress(args: argparse.Namespace) -> int:
    rec = activity_state.set_weekly_progress(
        args.activity_id, args.avance, notas=args.notas or ""
    )
    msg = f"[OK] {args.activity_id}: avance={rec['avance']:.0f}%"
    if rec.get("notas"):
        msg += f" | notas={rec['notas']}"
    print(msg)
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    rec = activity_state.add_adhoc(
        args.activity_id,
        args.nombre,
        tipo=args.tipo,
        meta=args.meta,
        unidad=args.unidad or "",
    )
    print(
        f"[OK] agregada actividad ad-hoc '{args.activity_id}' "
        f"({rec['tipo']}) — {rec['nombre']}"
    )
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    ok = activity_state.remove_activity(args.activity_id)
    if ok:
        print(f"[OK] {args.activity_id} borrada de la semana en curso")
    else:
        print(f"(no existía '{args.activity_id}' en la semana actual)")
    return 0


def _print_week(wk: str | None) -> int:
    wk = wk or activity_state.week_key()
    data = activity_state.get_week(wk)
    monday, friday = activity_state.week_range(wk)
    print(f"=== Semana {wk}  ({monday.isoformat()} → {friday.isoformat()}) ===\n")

    diarias = [(aid, a) for aid, a in data["activities"].items() if a["tipo"] == "diaria"]
    semanales = [(aid, a) for aid, a in data["activities"].items() if a["tipo"] != "diaria"]

    if diarias:
        print("Actividades diarias:")
        print(f"  {'ID':<22} {'Nombre':<42} {'Total':>8} {'Meta sem.':>10} {'Cumpl.':>8}")
        print("  " + "-" * 92)
        for aid, a in diarias:
            total = activity_state.daily_total(a)
            meta = a.get("meta") or 0
            meta_sem = meta * 5
            cumpl = activity_state.daily_compliance(a)
            cumpl_txt = f"{cumpl * 100:.0f}%" if cumpl is not None else "—"
            meta_txt = f"{meta_sem:.0f}" if meta_sem else "—"
            tag = " (ad-hoc)" if a.get("adhoc") else ""
            nombre = (a["nombre"] + tag)[:42]
            print(
                f"  {aid:<22} {nombre:<42} "
                f"{total:>8.0f} {meta_txt:>10} {cumpl_txt:>8}"
            )
        print()

    if semanales:
        print("Actividades semanales / proyectos:")
        print(f"  {'ID':<22} {'Nombre':<48} {'Avance':>8}")
        print("  " + "-" * 80)
        for aid, a in semanales:
            avance = a.get("avance") or 0
            tag = " (ad-hoc)" if a.get("adhoc") else ""
            nombre = (a["nombre"] + tag)[:48]
            print(f"  {aid:<22} {nombre:<48} {avance:>7.0f}%")
            if a.get("notas"):
                print(f"  {'':<22} └ {a['notas']}")
        print()

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    return _print_week(getattr(args, "wk", None))


def main() -> int:
    p = argparse.ArgumentParser(description="Tracker de actividades semanales.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_done = sub.add_parser("done", help="Marcar valor de una actividad diaria.")
    p_done.add_argument("activity_id")
    p_done.add_argument("--valor", "-v", type=float, required=True)
    p_done.add_argument("--fecha", help="ISO YYYY-MM-DD (default: hoy).")
    p_done.add_argument("--evidencia", default="", help="Link/referencia (ej. URL TikTok).")
    p_done.add_argument("--notas", default="")
    p_done.set_defaults(func=_cmd_done)

    p_prog = sub.add_parser("progress", help="Setear avance %% de una actividad semanal.")
    p_prog.add_argument("activity_id")
    p_prog.add_argument("--avance", "-a", type=float, required=True)
    p_prog.add_argument("--notas", default="")
    p_prog.set_defaults(func=_cmd_progress)

    p_add = sub.add_parser("add", help="Agregar actividad ad-hoc a la semana actual.")
    p_add.add_argument("activity_id", help="Slug único (ej. 'reunion-daniel').")
    p_add.add_argument("nombre", help="Nombre legible para el reporte.")
    p_add.add_argument("--tipo", choices=list(activity_state.VALID_TIPOS), default="unica")
    p_add.add_argument("--meta", type=float, default=None)
    p_add.add_argument("--unidad", default="")
    p_add.set_defaults(func=_cmd_add)

    p_rem = sub.add_parser("remove", help="Borrar actividad de la semana actual.")
    p_rem.add_argument("activity_id")
    p_rem.set_defaults(func=_cmd_remove)

    for name, help_text in (
        ("status", "Ver actividades de la semana actual."),
        ("week", "Alias de status — acepta --wk para semanas pasadas."),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--wk", help="Semana ISO AAAA-Www (default: actual).")
        sp.set_defaults(func=_cmd_status)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
