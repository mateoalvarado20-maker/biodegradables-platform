"""CLI: valida el paquete de configuración de un tenant (Acción 4).

Uso:
    python ops/validate_tenant.py biodegradables
    python ops/validate_tenant.py --all

Sale 0 si el/los tenant(s) validan; 1 si alguno falla, con un mensaje claro de
qué campo está mal. Es la "documentación ejecutable" del onboarding: un cliente
nuevo se da de alta llenando el YAML hasta que esto pase en verde.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from core.config.integrations import load_tenant_integrations  # noqa: E402
from core.config.loader import TENANTS_DIR, load_tenant_config  # noqa: E402


def _validate_one(slug: str) -> bool:
    try:
        cfg = load_tenant_config(slug)
    except FileNotFoundError as e:
        print(f"[FAIL] {slug}: {e}")
        return False
    except ValidationError as e:
        print(f"[FAIL] {slug}: config inválida")
        print(e)
        return False
    except Exception as e:  # YAML roto, fecha mal escrita, etc.
        print(f"[FAIL] {slug}: {type(e).__name__}: {e}")
        return False
    # F5.3: integrations.yaml es opcional, pero si existe debe validar.
    try:
        integ = load_tenant_integrations(slug)
    except ValidationError as e:
        print(f"[FAIL] {slug}: integrations.yaml inválido")
        print(e)
        return False
    except Exception as e:
        print(f"[FAIL] {slug}: integrations.yaml — {type(e).__name__}: {e}")
        return False
    extra = f", {len(integ.all_secrets())} secrets declarados" if integ else ""
    print(f"[OK]   {slug}: {cfg.display_name} ({cfg.locale}, {cfg.timezone}{extra})")
    return True


def _all_slugs() -> list[str]:
    return sorted(
        p.name
        for p in TENANTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("uso: python ops/validate_tenant.py <slug> [<slug> ...] | --all")
        return 0 if not args else 2
    slugs = _all_slugs() if args[0] == "--all" else args
    ok = all(_validate_one(s) for s in slugs)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
