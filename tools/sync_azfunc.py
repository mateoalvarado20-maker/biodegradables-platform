"""sync_azfunc — genera las copias de deploy de azfunc/ desde la raíz.

Fase 4 del refactor (2026-06-12). La auditoría encontró que 8 de 13 módulos
duplicados raíz↔azfunc habían divergido (bugs corregidos en una copia
seguían vivos en la que corría en producción — D2). Desde esta fase:

    LA RAÍZ ES LA ÚNICA FUENTE. azfunc/ se GENERA con este script.
    Editar a mano un archivo SHARED dentro de azfunc/ está prohibido —
    el CI (--check) y el próximo sync lo van a pisar.

Uso:
    python tools/sync_azfunc.py            # sincroniza raíz → azfunc/
    python tools/sync_azfunc.py --check    # solo verifica (exit 1 si drift)

Archivos azfunc-ESPECÍFICOS (autorados dentro de azfunc/, NO se tocan):
    function_app.py, host.json, requirements.txt, bot_handler.py,
    chat_agent.py, pbi_query.py, ec_holidays.py, outlook_client.py (versión
    app-only — la raíz usa MSAL delegated), manifest.json, *.png
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AZFUNC = ROOT / "azfunc"

# Módulos compartidos: la copia de azfunc/ se genera desde la raíz.
SHARED = [
    "apollo_rest.py",
    "company_context.md",
    "condiciones_credito.json",
    "contifico_client.py",
    "core_config.py",
    "credito_excel.py",
    "daily_logistics_report.py",
    "daily_report.py",
    "dispatch_state.py",
    "graph_mail.py",
    "hubspot_client.py",
    "pbi_cloud.py",
    "reply_agent.py",
    "reply_state.py",
    "safe_json.py",
]


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def main() -> int:
    # Consolas Windows con cp1252 no soportan caracteres como flechas
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="no copia: falla con exit 1 si hay drift (para CI)",
    )
    args = parser.parse_args()

    drift: list[str] = []
    missing: list[str] = []
    for name in SHARED:
        src = ROOT / name
        dst = AZFUNC / name
        if not src.exists():
            missing.append(name)
            continue
        if not dst.exists() or _md5(src) != _md5(dst):
            drift.append(name)
            if not args.check:
                shutil.copy2(src, dst)

    if missing:
        print(f"ERROR: faltan en la raíz: {missing}", file=sys.stderr)
        return 1

    if args.check:
        if drift:
            print("DRIFT detectado (azfunc/ desactualizado o editado a mano):")
            for n in drift:
                print(f"  - {n}")
            print("Corré: python tools/sync_azfunc.py")
            return 1
        print(f"OK: {len(SHARED)} módulos compartidos sin drift.")
        return 0

    if drift:
        print(f"Sincronizados {len(drift)} archivos raíz → azfunc/:")
        for n in drift:
            print(f"  → {n}")
    else:
        print("azfunc/ ya estaba al día.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
