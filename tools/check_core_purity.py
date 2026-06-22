"""Check de pureza del núcleo: el código compartido NUNCA nombra a un cliente (Acción 2).

Escanea `core/` y `modules/` buscando literales atados a un cliente concreto
(nombre de empresa, ERP/CRM específicos, país/ciudad). Si aparece alguno, es señal
de que algo que debería vivir en `tenants/<slug>/` se filtró al núcleo.

Arranca en modo WARNING (sale 0 siempre) para no bloquear el CI mientras se hace
la migración. Cuando `core/` esté limpio, se pasa a modo gate con --strict.

Uso:
    python tools/check_core_purity.py            # warning (no falla)
    python tools/check_core_purity.py --strict   # falla (exit 1) si hay coincidencias
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["core", "modules"]
FORBIDDEN = [
    "biodegradables", "contifico", "hubspot", "apollo",
    "ecuador", "guayaquil", "quito",
]
PATTERN = re.compile("|".join(FORBIDDEN), re.IGNORECASE)


def scan() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if PATTERN.search(line):
                    rel = path.relative_to(ROOT).as_posix()
                    hits.append((rel, i, line.strip()))
    return hits


def main(argv: list[str]) -> int:
    strict = "--strict" in argv[1:]
    hits = scan()
    if not hits:
        print("[core-purity] OK: core/ y modules/ no nombran a ningún cliente.")
        return 0
    print(f"[core-purity] {len(hits)} literal(es) de cliente en el núcleo:")
    for rel, line_no, text in hits:
        print(f"  {rel}:{line_no}: {text}")
    if strict:
        print("[core-purity] FALLA (--strict): mové estos literales a tenants/<slug>/.")
        return 1
    print("[core-purity] WARNING: aún no bloquea. Meta: 0 para activar --strict en CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
