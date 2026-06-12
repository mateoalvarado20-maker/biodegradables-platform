"""One-off Fase 1: inserta @_locked sobre las funciones mutadoras de activity_state.py."""
import re
from pathlib import Path

MUTATORS = [
    "init_week", "mark_daily", "set_weekly_progress", "add_adhoc",
    "remove_activity", "set_priority", "set_day_schedule", "set_cierre_caja",
    "set_apertura_caja", "set_cierre_caja_confirmacion", "add_recordatorio_cierre",
    "set_chocolates_stock_inicial", "add_chocolates_entrega", "add_chocolates_recarga",
    "marcar_alerta_chocolates_enviada", "set_tiktok_seguidores_semana",
    "set_envios_snapshot", "start_ruta", "end_ruta", "marcar_entrega",
    "add_destino_adhoc", "carry_over_envios_no_entregados",
    "set_caja_chica_inicial", "add_caja_chica_movimiento", "reset_day", "wipe_user",
]

path = Path(__file__).parent.parent / "activity_state.py"
src = path.read_text(encoding="utf-8")
applied, missing = [], []
for name in MUTATORS:
    pattern = re.compile(rf"^def {name}\(", re.MULTILINE)
    matches = pattern.findall(src)
    if len(matches) != 1:
        missing.append((name, len(matches)))
        continue
    if re.search(rf"^@_locked\ndef {name}\(", src, re.MULTILINE):
        continue  # ya aplicado
    src = pattern.sub(f"@_locked\ndef {name}(", src, count=1)
    applied.append(name)

path.write_text(src, encoding="utf-8")
print(f"Aplicados: {len(applied)}")
for n in applied:
    print(f"  + {n}")
if missing:
    print(f"PROBLEMAS: {missing}")
    raise SystemExit(1)
