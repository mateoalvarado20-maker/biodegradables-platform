"""Recodificación masiva de productos en Contifico.

Lee el plan revisado en `Plan_recodificacion_Contifico.xlsx` y aplica los
cambios de `codigo` (y inactivaciones) vía `PATCH /producto/{id}/`.

Esquema de unificación:
  - Se quita el prefijo de proveedor (4 letras) -> queda el código base de 13.
  - Las XXX se reemplazan por SER (servicios) / EQU (equipos) / INS (insumos
    de imprenta).
  - Colisiones (mismo base, productos distintos): el de mayor stock conserva el
    código, los demás reciben el siguiente consecutivo libre.
  - Duplicados (mismo base, mismo producto): se inactivan los sobrantes.

SIEMPRE corre con --dry-run primero. Antes de --execute guarda un rollback en
`contifico_rollback.json` con (id, codigo, estado) originales para revertir.

Uso:
    python contifico_recode.py --dry-run
    python contifico_recode.py --execute
    python contifico_recode.py --rollback   # revierte usando contifico_rollback.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "https://api.contifico.com/sistema/api/v1"
PLAN_XLSX = Path(
    r"C:\Users\Mateo\OneDrive - Biodegradables Ecuador\Plan_recodificacion_Contifico.xlsx"
)
ROLLBACK = Path(r"C:\Users\Mateo\contifico_rollback.json")
PRODUCTS_BACKUP = Path(r"C:\Users\Mateo\contifico_productos.json")

INACTIVATE_MARK = "(INACTIVAR"
VALID_CODE = re.compile(r"^[A-Z]{3,}\d{3}$")


def _token() -> str:
    t = os.environ.get("CONTIFICO_API_TOKEN", "").strip()
    if not t:
        t = _user_env("CONTIFICO_API_TOKEN")
    if not t:
        raise RuntimeError("Falta CONTIFICO_API_TOKEN")
    return t


def _user_env(name: str) -> str:
    # fallback: leer del scope User en Windows
    import subprocess

    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             f"[Environment]::GetEnvironmentVariable('{name}','User')"],
            text=True,
        ).strip()
        return out
    except Exception:
        return ""


def load_plan() -> list[dict]:
    """Devuelve [{id, actual, propuesto, problema, inactivar}] solo de filas con cambio."""
    df = pd.read_excel(PLAN_XLSX)
    # Acceso por posición para evitar problemas de encoding con acentos:
    # 0=ID, 1=Código ACTUAL, 2=Estado, 3=Stock, 4=Producto, 5=BASE, 6=PROPUESTO, 7=Problema
    changes = []
    for _, r in df.iterrows():
        pid = str(r.iloc[0]).strip()
        actual = str(r.iloc[1]).strip()
        propuesto = str(r.iloc[6]).strip()
        problema = str(r.iloc[7]).strip()
        if not pid or pid == "nan":
            continue
        if propuesto.startswith(INACTIVATE_MARK):
            changes.append({"id": pid, "actual": actual, "propuesto": None,
                            "problema": problema, "inactivar": True})
        elif VALID_CODE.match(propuesto) and propuesto != actual:
            changes.append({"id": pid, "actual": actual, "propuesto": propuesto,
                            "problema": problema, "inactivar": False})
    return changes


def save_rollback(token: str, changes: list[dict]) -> None:
    """Guarda codigo/estado originales de cada id afectado (desde el backup local)."""
    backup = {p["id"]: p for p in json.load(open(PRODUCTS_BACKUP, encoding="utf-8"))}
    snap = []
    for ch in changes:
        p = backup.get(ch["id"], {})
        snap.append({"id": ch["id"], "codigo": p.get("codigo"), "estado": p.get("estado")})
    json.dump(snap, open(ROLLBACK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Rollback guardado: {ROLLBACK} ({len(snap)} registros)")


def patch_product(client: httpx.Client, token: str, pid: str, payload: dict) -> tuple[bool, str]:
    r = client.patch(f"{API_BASE}/producto/{pid}/", json=payload,
                     headers={"Authorization": token, "Content-Type": "application/json"})
    if r.status_code < 400:
        return True, "OK"
    return False, f"{r.status_code}: {r.text[:200]}"


def run(execute: bool) -> None:
    token = _token()
    changes = load_plan()
    print(f"Cambios a aplicar: {len(changes)} "
          f"(recodificar/renombrar: {sum(1 for c in changes if not c['inactivar'])}, "
          f"inactivar: {sum(1 for c in changes if c['inactivar'])})")
    # validar que no queden códigos propuestos duplicados entre sí
    props = [c["propuesto"] for c in changes if c["propuesto"]]
    dup = {x for x in props if props.count(x) > 1}
    if dup:
        print(f"  ⚠ ATENCIÓN: códigos propuestos duplicados entre sí: {sorted(dup)}")

    if not execute:
        for c in changes[:60]:
            act = "INACTIVAR" if c["inactivar"] else f"{c['actual']} -> {c['propuesto']}"
            print(f"  [{c['id']}] {act}   ({c['problema']})")
        if len(changes) > 60:
            print(f"  ... y {len(changes) - 60} más")
        print("\n(DRY-RUN: nada se escribió. Corre con --execute para aplicar.)")
        return

    save_rollback(token, changes)
    ok = err = 0
    with httpx.Client(timeout=60) as client:
        for i, c in enumerate(changes, 1):
            payload = {"estado": "I"} if c["inactivar"] else {"codigo": c["propuesto"]}
            success, msg = patch_product(client, token, c["id"], payload)
            if success:
                ok += 1
            else:
                err += 1
                print(f"  ERROR [{c['id']}] {c['actual']}: {msg}")
            if i % 25 == 0:
                print(f"  ...{i}/{len(changes)}")
            time.sleep(0.15)  # cortesía con la API
    print(f"\nHecho. OK={ok}  Errores={err}")
    if err:
        print("Revisa errores arriba. Puedes revertir con: python contifico_recode.py --rollback")


def rollback() -> None:
    token = _token()
    snap = json.load(open(ROLLBACK, encoding="utf-8"))
    print(f"Revirtiendo {len(snap)} productos...")
    ok = err = 0
    with httpx.Client(timeout=60) as client:
        for s in snap:
            payload = {"codigo": s["codigo"], "estado": s["estado"]}
            success, msg = patch_product(client, token, s["id"], payload)
            if success:
                ok += 1
            else:
                err += 1
                print(f"  ERROR [{s['id']}]: {msg}")
            time.sleep(0.15)
    print(f"Rollback hecho. OK={ok} Errores={err}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    g.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.rollback:
        rollback()
    else:
        run(execute=a.execute)
