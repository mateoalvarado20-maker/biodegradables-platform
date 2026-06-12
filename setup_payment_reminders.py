"""Crea recordatorios de pagos recurrentes en el calendario de Daniel Sánchez.

Pagos anuales:
  - 12 mayo  → Patente Municipal e Impuesto 1.5% (reminder 10 días antes)
  - 30 sept  → Superintendencia de Bancos (reminder 10 días antes)
  -  1 oct   → Renovación licencia CONTIFICO (reminder 15 días antes — hay que negociar)

Pagos mensuales:
  - día 18 de cada mes → Suscripción Claude (reminder 3 días antes)
  - día 15 de cada mes → Microsoft 365 (reminder 3 días antes)

El script es idempotente: si ya existe un evento con el mismo subject, lo
salta. Para reemplazar uno (ej. cambio de reminder), borrarlo manualmente
primero o usar `--replace "subject substring"`.

Pre-requisitos:
  - App registration con scope Calendars.ReadWrite.Shared (admin consent).
  - Daniel comparte su calendario contigo con permiso "Editor" en Outlook.

Uso:
    python setup_payment_reminders.py                       # crea faltantes en Daniel
    python setup_payment_reminders.py --dry-run             # solo imprime plan
    python setup_payment_reminders.py --self                # apunta a TU calendario
    python setup_payment_reminders.py --replace "CONTIFICO" # borra y recrea match
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from calendar_client import (
    create_monthly_all_day_event,
    create_yearly_all_day_event,
    delete_event,
    find_events_by_subject,
)

DANIEL_EMAIL = "dsanchez@biodegradablesecuador.com"
TODAY = date.today()

# Cada pago se define una vez. El script calcula la próxima fecha de inicio
# como la siguiente ocurrencia desde hoy.
PAYMENTS: list[dict] = [
    # ANUALES
    {
        "subject": "Pago Patente Municipal e Impuesto 1.5%",
        "frequency": "yearly",
        "month": 5, "day": 12,
        "reminder_days": 10,
        "body_html": (
            "<p><strong>Pago anual:</strong> Patente Municipal e Impuesto 1.5% sobre activos totales.</p>"
            "<p><strong>Fecha límite:</strong> 12 de mayo.</p>"
            "<p>Recordatorio configurado 10 días antes.</p>"
        ),
    },
    {
        "subject": "Pago Superintendencia de Bancos",
        "frequency": "yearly",
        "month": 9, "day": 30,
        "reminder_days": 10,
        "body_html": (
            "<p><strong>Pago anual:</strong> Superintendencia de Bancos.</p>"
            "<p><strong>Fecha límite:</strong> 30 de septiembre.</p>"
            "<p>Recordatorio configurado 10 días antes.</p>"
        ),
    },
    {
        "subject": "Renovación licencia CONTIFICO",
        "frequency": "yearly",
        "month": 10, "day": 1,
        "reminder_days": 15,
        "body_html": (
            "<p><strong>Pago anual:</strong> Renovación licencia ERP CONTIFICO.</p>"
            "<p><strong>Fecha límite:</strong> 1 de octubre.</p>"
            "<p>Recordatorio configurado 15 días antes — hay que negociar las licencias antes del vencimiento.</p>"
        ),
    },
    # MENSUALES
    {
        "subject": "Suscripción Claude",
        "frequency": "monthly",
        "day": 18,
        "reminder_days": 3,
        "body_html": (
            "<p><strong>Suscripción mensual:</strong> Claude (Anthropic).</p>"
            "<p><strong>Cargo:</strong> día 18 de cada mes.</p>"
            "<p>Recordatorio 3 días antes.</p>"
        ),
    },
    {
        "subject": "Suscripción Microsoft 365",
        "frequency": "monthly",
        "day": 15,
        "reminder_days": 3,
        "body_html": (
            "<p><strong>Suscripción mensual:</strong> Microsoft 365.</p>"
            "<p><strong>Cargo:</strong> día 15 de cada mes.</p>"
            "<p>Recordatorio 3 días antes.</p>"
        ),
    },
]


def next_yearly_date(month: int, day: int) -> date:
    """Próxima ocurrencia del día/mes, este año o el siguiente si ya pasó."""
    try:
        candidate = date(TODAY.year, month, day)
    except ValueError:
        # Día inválido para el mes (raro, ej. 29 feb). Próximo año.
        candidate = date(TODAY.year + 1, month, day)
    if candidate < TODAY:
        candidate = date(TODAY.year + 1, month, day)
    return candidate


def next_monthly_date(day: int) -> date:
    """Próxima ocurrencia del día del mes, este mes o el siguiente si ya pasó."""
    y, m = TODAY.year, TODAY.month
    try:
        candidate = date(y, m, day)
    except ValueError:
        candidate = None
    if candidate is None or candidate < TODAY:
        # Avanzar al siguiente mes
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
        try:
            candidate = date(y, m, day)
        except ValueError:
            # Día > días del mes (ej. 31 en febrero). Outlook ajusta con dayOfMonth.
            # Para la fecha de inicio usamos el último día del mes.
            from calendar import monthrange
            candidate = date(y, m, monthrange(y, m)[1])
    return candidate


def compute_start_date(p: dict) -> str:
    if p["frequency"] == "yearly":
        d = next_yearly_date(p["month"], p["day"])
    else:
        d = next_monthly_date(p["day"])
    return d.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo imprime, no crea.")
    parser.add_argument("--self", action="store_true", help="Crea en TU calendario en vez del de Daniel.")
    parser.add_argument(
        "--replace",
        metavar="SUBSTRING",
        help="Borra eventos cuyo subject contenga SUBSTRING antes de recrear.",
    )
    args = parser.parse_args()

    target_email: str | None = None if args.self else DANIEL_EMAIL
    target_label = "TU calendario" if args.self else f"calendario de {DANIEL_EMAIL}"
    print(f"Destino: {target_label}\n")

    # 1. Manejar --replace
    replaced_subjects = set()
    if args.replace:
        matches = find_events_by_subject(target_email, args.replace)
        print(f"--replace '{args.replace}': {len(matches)} evento(s) coincidente(s)")
        for m in matches:
            subj = (m.get("subject") or "").strip()
            print(f"  - {subj} (id {m['id'][:30]}...)")
            replaced_subjects.add(subj)
            if not args.dry_run:
                delete_event(user_email=target_email, event_id=m["id"])
                print("    borrado.")
        print()

    # 2. Listar todos los eventos para saber qué ya existe (lectura, OK en dry-run).
    #    Los subjects marcados para reemplazo se sacan del set para que se recreen.
    from calendar_client import list_calendar_events
    existing_subjects = {
        (ev.get("subject") or "").strip()
        for ev in list_calendar_events(target_email, top=100)
    } - replaced_subjects

    # 3. Procesar cada pago
    creados = 0
    saltados = 0
    for p in PAYMENTS:
        start = compute_start_date(p)
        tag = "[ANUAL] " if p["frequency"] == "yearly" else "[MENSUAL]"
        if p["subject"] in existing_subjects:
            print(f"  SKIP {tag} {p['subject']}  (ya existe)")
            saltados += 1
            continue
        print(f"  CREATE {tag} {p['subject']}  start={start}  reminder={p['reminder_days']}d")
        if args.dry_run:
            continue
        minutes = p["reminder_days"] * 24 * 60
        try:
            if p["frequency"] == "yearly":
                ev = create_yearly_all_day_event(
                    user_email=target_email,
                    subject=p["subject"],
                    body_html=p["body_html"],
                    start_date_iso=start,
                    reminder_minutes_before=minutes,
                    categories=["Pagos"],
                )
            else:
                ev = create_monthly_all_day_event(
                    user_email=target_email,
                    subject=p["subject"],
                    body_html=p["body_html"],
                    start_date_iso=start,
                    reminder_minutes_before=minutes,
                    categories=["Pagos"],
                )
            print(f"         OK id={ev.get('id', '')[:30]}...")
            creados += 1
        except Exception as e:
            print(f"         ERR {e}", file=sys.stderr)
            return 1

    print(f"\nResumen: {creados} creado(s), {saltados} salteado(s) por ya existir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
