"""Orquestador de secuencias Apollo.

Mantiene exactamente UNA secuencia activa a la vez, rotando según orden de
prioridad definido en apollo_orchestrator.json. Cuando la secuencia activa
termina su tanda de envíos (unique_scheduled == 0), se pausa y se activa
la siguiente en la cola.

Diseño:
- Solo opera en horario hábil (lun-vie, sin feriados Ecuador, 9-18 EC local).
- Fuera de horario hábil → pausa todas las activas.
- Si hay >1 activa al mismo tiempo (estado inconsistente) → pausa todas menos
  la primera de la cola que tenga contactos pendientes.
- Si la secuencia "activa esperada" tiene contactos pendientes pero está
  pausada → la activa.
- Si la activa terminó su tanda → la pausa y activa la siguiente que tenga
  contactos pendientes.

Requiere APOLLO_API_KEY con permiso "Master API Key" (Apollo Settings →
Integrations → API). Sin master, /search /approve /abort devuelven 403.

Uso:
    python apollo_orchestrator.py --status        # ver estado actual
    python apollo_orchestrator.py --dry-run       # mostrar qué haría
    python apollo_orchestrator.py                 # ejecutar tick (lo que Task Scheduler corre)
    python apollo_orchestrator.py --pause-all     # pausar todas (uso manual)
    python apollo_orchestrator.py --force-rotate  # forzar pasar a la siguiente
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import apollo_rest
from ec_holidays import EC_HOLIDAYS

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador UTC-5

CONFIG_PATH = Path(__file__).parent / "apollo_orchestrator.json"
# En Azure Functions (Linux), filesystem es efimero. Usar /tmp ahi.
# Localmente, mantener en ~/.claude-agent para que sobreviva reinicios.
if os.environ.get("AzureWebJobsStorage"):
    STATE_PATH = Path("/tmp/apollo_orchestrator_state.json")
else:
    STATE_PATH = Path.home() / ".claude-agent" / "apollo_orchestrator_state.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"last_tick": None, "last_action": None, "history": []}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    history = state.get("history", [])
    state["history"] = history[-50:]  # mantener últimas 50 entradas
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def is_working_hours(cfg: dict, now: datetime | None = None) -> tuple[bool, str]:
    """Devuelve (es_hábil, razón)."""
    now = now or now_local()
    sched = cfg["schedule"]

    if sched.get("weekdays_only", True) and now.weekday() >= 5:
        return False, f"fin de semana ({now.strftime('%A')})"

    if sched.get("respect_ecuador_holidays", True):
        today = now.date()
        if today in EC_HOLIDAYS.get(today.year, []):
            return False, "feriado Ecuador"

    h = now.hour
    if h < sched["start_hour_local"] or h >= sched["end_hour_local"]:
        return False, f"fuera de horario ({h}:00, ventana {sched['start_hour_local']}-{sched['end_hour_local']})"

    return True, "horario hábil"


def scheduled_count(seq: dict) -> int:
    """Apollo devuelve 'loading' a veces para unique_scheduled. Trata eso como 0."""
    v = seq.get("unique_scheduled")
    if isinstance(v, int):
        return v
    return 0


def has_pending_contacts(seq: dict) -> bool:
    return scheduled_count(seq) > 0


def fetch_sequences_by_id(cfg: dict) -> dict[str, dict]:
    """Trae todas las secuencias de la cuenta y las devuelve dict[id, seq]."""
    all_seqs = apollo_rest.list_sequences()
    return {s["id"]: s for s in all_seqs}


def desired_active(cfg: dict, seqs_by_id: dict[str, dict]) -> dict | None:
    """De la cola, devuelve la primera secuencia que tenga contactos pendientes.
    None si ninguna tiene."""
    for entry in cfg["queue"]:
        seq = seqs_by_id.get(entry["id"])
        if seq is None:
            continue
        if has_pending_contacts(seq):
            return seq
    return None


def currently_active(seqs_by_id: dict[str, dict]) -> list[dict]:
    return [s for s in seqs_by_id.values() if s.get("active")]


def plan_actions(cfg: dict, seqs_by_id: dict[str, dict], working: bool) -> list[tuple[str, dict, str]]:
    """Devuelve lista de (acción, sequence, razón).
    Acción ∈ {'pause', 'activate'}.
    """
    actions: list[tuple[str, dict, str]] = []
    actives = currently_active(seqs_by_id)

    if not working:
        for s in actives:
            actions.append(("pause", s, "fuera de horario hábil"))
        return actions

    target = desired_active(cfg, seqs_by_id)

    if target is None:
        for s in actives:
            actions.append(("pause", s, "no hay secuencias con contactos pendientes en la cola"))
        return actions

    for s in actives:
        if s["id"] != target["id"]:
            reason = "ya no es la siguiente en la cola"
            if not has_pending_contacts(s):
                reason = "terminó su tanda (scheduled=0)"
            actions.append(("pause", s, reason))

    if not target.get("active"):
        actions.append(("activate", target, "siguiente en la cola con contactos pendientes"))

    return actions


def apply_action(action: str, seq: dict, dry_run: bool) -> dict:
    name = seq.get("name", seq["id"])
    if dry_run:
        return {"dry_run": True, "action": action, "id": seq["id"], "name": name}
    try:
        if action == "pause":
            apollo_rest.deactivate_sequence(seq["id"])
        elif action == "activate":
            apollo_rest.activate_sequence(seq["id"])
        return {"ok": True, "action": action, "id": seq["id"], "name": name}
    except Exception as e:
        return {"ok": False, "action": action, "id": seq["id"], "name": name, "error": str(e)}


def cmd_status() -> None:
    cfg = load_config()
    seqs_by_id = fetch_sequences_by_id(cfg)
    working, reason = is_working_hours(cfg)

    print(f"=== Apollo Orchestrator status @ {now_local():%Y-%m-%d %H:%M %Z} ===")
    print(f"Horario: {'HABIL' if working else 'FUERA'} ({reason})")
    print()

    actives = currently_active(seqs_by_id)
    print(f"Activas ahora ({len(actives)}):")
    for s in actives:
        print(f"  ✓ {s['name']:<45} scheduled={scheduled_count(s):<4} delivered={s.get('unique_delivered')}")
    if not actives:
        print("  (ninguna)")
    print()

    target = desired_active(cfg, seqs_by_id)
    print("Cola configurada (queue):")
    for i, entry in enumerate(cfg["queue"], 1):
        seq = seqs_by_id.get(entry["id"])
        if seq is None:
            print(f"  {i:2}. [NO ENCONTRADA] {entry['name']}  id={entry['id']}")
            continue
        marker = "  "
        if target and seq["id"] == target["id"]:
            marker = "→ "
        active_mark = "ACTIVA" if seq.get("active") else "pausada"
        print(f"  {marker}{i:2}. {seq['name']:<45} scheduled={scheduled_count(seq):<4} [{active_mark}]")
    print()

    if target:
        print(f"Secuencia esperada activa: {target['name']}")
    else:
        print("Secuencia esperada activa: (ninguna — no hay contactos pendientes)")

    actions = plan_actions(cfg, seqs_by_id, working)
    if actions:
        print("\nAcciones que se ejecutarían en este momento:")
        for action, s, reason in actions:
            print(f"  {action.upper():<8} {s['name']:<45} ({reason})")
    else:
        print("\nNo hay acciones pendientes. Sistema en estado deseado.")


def cmd_tick(dry_run: bool, verbose: bool = False) -> int:
    cfg = load_config()
    seqs_by_id = fetch_sequences_by_id(cfg)
    working, reason = is_working_hours(cfg)
    actions = plan_actions(cfg, seqs_by_id, working)

    results = []
    for action, seq, why in actions:
        r = apply_action(action, seq, dry_run)
        r["reason"] = why
        results.append(r)
        prefix = "[DRY]" if dry_run else "[LIVE]"
        ok = "OK" if r.get("ok") or r.get("dry_run") else "ERROR"
        print(f"{prefix} {ok}  {action.upper():<8} {seq['name']:<45} ({why})")
        if not r.get("ok") and not r.get("dry_run"):
            print(f"        → {r.get('error', '')}")

    if not actions and verbose:
        print(f"[{('DRY' if dry_run else 'LIVE')}] sin acciones — sistema en estado deseado ({reason})")

    if not dry_run:
        state = load_state()
        state["last_tick"] = now_local().isoformat()
        state["last_action"] = results
        state["history"].append({
            "ts": now_local().isoformat(),
            "working": working,
            "reason": reason,
            "actions": results,
        })
        save_state(state)

    failed = [r for r in results if r.get("ok") is False]
    return 1 if failed else 0


def cmd_pause_all(dry_run: bool) -> int:
    cfg = load_config()
    seqs_by_id = fetch_sequences_by_id(cfg)
    actives = currently_active(seqs_by_id)
    if not actives:
        print("No hay secuencias activas.")
        return 0
    for s in actives:
        r = apply_action("pause", s, dry_run)
        prefix = "[DRY]" if dry_run else "[LIVE]"
        ok = "OK" if r.get("ok") or r.get("dry_run") else "ERROR"
        print(f"{prefix} {ok}  PAUSE  {s['name']}")
        if not r.get("ok") and not r.get("dry_run"):
            print(f"        → {r.get('error', '')}")
    return 0


def cmd_force_rotate(dry_run: bool) -> int:
    """Pausa la activa actual y activa la siguiente en la cola
    (independientemente de scheduled_count)."""
    cfg = load_config()
    seqs_by_id = fetch_sequences_by_id(cfg)
    actives = currently_active(seqs_by_id)

    queue_ids = [e["id"] for e in cfg["queue"]]
    current_idx = -1
    for s in actives:
        if s["id"] in queue_ids:
            current_idx = queue_ids.index(s["id"])
            break

    if current_idx == -1:
        next_idx = 0
    else:
        next_idx = (current_idx + 1) % len(queue_ids)

    next_seq = seqs_by_id.get(queue_ids[next_idx])
    if next_seq is None:
        print(f"ERROR: secuencia {queue_ids[next_idx]} no encontrada en la cuenta")
        return 1

    for s in actives:
        if s["id"] != next_seq["id"]:
            r = apply_action("pause", s, dry_run)
            print(f"{'[DRY]' if dry_run else '[LIVE]'} PAUSE  {s['name']}")
            if not r.get("ok") and not r.get("dry_run"):
                print(f"        → {r.get('error', '')}")

    if not next_seq.get("active"):
        r = apply_action("activate", next_seq, dry_run)
        print(f"{'[DRY]' if dry_run else '[LIVE]'} ACTIVATE  {next_seq['name']}")
        if not r.get("ok") and not r.get("dry_run"):
            print(f"        → {r.get('error', '')}")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Orquestador de secuencias Apollo")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="Mostrar estado actual sin hacer cambios")
    g.add_argument("--pause-all", action="store_true", help="Pausar todas las secuencias activas")
    g.add_argument("--force-rotate", action="store_true", help="Forzar paso a la siguiente secuencia")
    p.add_argument("--dry-run", action="store_true", help="No hacer cambios, solo mostrar acciones")
    p.add_argument("--verbose", "-v", action="store_true", help="Mostrar más detalle")
    args = p.parse_args()

    try:
        if args.status:
            cmd_status()
            return 0
        if args.pause_all:
            return cmd_pause_all(args.dry_run)
        if args.force_rotate:
            return cmd_force_rotate(args.dry_run)
        return cmd_tick(args.dry_run, args.verbose)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if "403" in str(e):
            print("\nHint: La API key necesita ser Master API Key.", file=sys.stderr)
            print("Genera una en Apollo Settings → Integrations → API → New API Key", file=sys.stderr)
            print("y marca el toggle 'Master API Key'.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
