"""Helper para consultar Power BI cloud desde la CLI.

Lo uso yo (Claude) cuando me preguntes en el chat. Tú puedes invocarlo
también directamente si quieres.

Modos:
    python pbi_ask.py --schema              # tablas, columnas, medidas
    python pbi_ask.py --refresh-status      # ultimo refresh del dataset
    python pbi_ask.py --measures            # solo lista medidas
    python pbi_ask.py --dax "EVALUATE ..."  # ejecuta DAX y devuelve resultado
    python pbi_ask.py --dax "..." --json    # salida JSON en vez de tabla
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pbi_cloud import dax_rows, execute_dax, get_last_refresh

DATASET_ID = "5b04e54f-36e7-4ef0-a041-e4826f124223"
SCHEMA_PATH = Path(__file__).parent / "pbi_schema.json"
LOCAL_TZ = timezone(timedelta(hours=-5))


def _try_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _fmt_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v == int(v):
            return f"{int(v):,}"
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def run_dax(dax: str, as_json: bool = False) -> int:
    try:
        result = execute_dax(DATASET_ID, dax)
    except Exception as e:
        print(f"ERROR ejecutando DAX: {e}", file=sys.stderr)
        return 1

    rows = dax_rows(result)
    if as_json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0

    if not rows:
        print("(sin resultados)")
        return 0

    # Tabla en texto plano
    keys = list(rows[0].keys())
    headers = [k.strip("[]") for k in keys]
    # Calcular ancho de cada columna
    widths = [len(h) for h in headers]
    table = []
    for r in rows:
        row = [_fmt_value(r.get(k)) for k in keys]
        table.append(row)
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)

    sep = "  "
    print(sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print(sep.join("-" * widths[i] for i in range(len(headers))))
    for row in table:
        print(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))

    print(f"\n({len(rows)} filas)")
    return 0


def show_schema(only_measures: bool = False) -> int:
    if not SCHEMA_PATH.exists():
        print(
            f"No existe {SCHEMA_PATH}. Ejecuta primero: python pbi_discover.py",
            file=sys.stderr,
        )
        return 1
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    if only_measures:
        print("=== MEDIDAS ===")
        for tbl, ms in schema.get("measures_by_table", {}).items():
            print(f"\n[{tbl}]")
            for m in ms:
                print(f"  - {m}")
        return 0

    print(f"Dataset: {schema.get('dataset_id')}\n")

    print("=== TABLAS ===")
    for t in schema.get("tables", []):
        print(f"  - {t}")

    print("\n=== MEDIDAS POR TABLA ===")
    for tbl, ms in schema.get("measures_by_table", {}).items():
        print(f"\n  [{tbl}]")
        for m in ms:
            print(f"    - {m}")

    print("\n=== COLUMNAS POR TABLA ===")
    for tbl, cols in schema.get("columns_by_table", {}).items():
        print(f"\n  [{tbl}]")
        for c in cols:
            print(f"    - {c}")

    return 0


def show_refresh() -> int:
    iso = get_last_refresh(DATASET_ID)
    if not iso:
        print("Sin información de refresh.")
        return 1
    try:
        utc = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        local = utc.astimezone(LOCAL_TZ)
        print(f"Último refresh: {local.strftime('%d/%m/%Y %H:%M')} (hora Ecuador)")
        print(f"UTC original: {iso}")
        age = datetime.now(timezone.utc) - utc
        mins = int(age.total_seconds() / 60)
        if mins < 60:
            print(f"Antigüedad: {mins} min")
        else:
            print(f"Antigüedad: {mins // 60}h {mins % 60}min")
    except Exception:
        print(f"Último refresh: {iso}")
    return 0


def main() -> int:
    _try_utf8()
    p = argparse.ArgumentParser(
        description="Consulta Power BI cloud (Contifico)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--dax",
        help="Query DAX a ejecutar (usa '-' para leer de stdin, o '@archivo.dax')",
    )
    p.add_argument("--json", action="store_true", help="Salida JSON (solo con --dax)")
    p.add_argument("--schema", action="store_true", help="Mostrar esquema completo")
    p.add_argument("--measures", action="store_true", help="Listar solo medidas")
    p.add_argument("--refresh-status", action="store_true", help="Hora del último refresh")
    args = p.parse_args()

    if args.dax:
        dax_query = args.dax
        if dax_query == "-":
            dax_query = sys.stdin.read()
        elif dax_query.startswith("@"):
            dax_query = Path(dax_query[1:]).read_text(encoding="utf-8")
        # Limpiar BOM y caracteres invisibles que PowerShell mete a veces
        dax_query = dax_query.lstrip("﻿\u200b\xa0 \t\r\n").rstrip()
        return run_dax(dax_query, args.json)
    if args.schema:
        return show_schema(only_measures=False)
    if args.measures:
        return show_schema(only_measures=True)
    if args.refresh_status:
        return show_refresh()

    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
