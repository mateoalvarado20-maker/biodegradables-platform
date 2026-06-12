"""Descubrimiento completo del esquema del modelo Contifico cloud.

Usa INFO.VIEW.* (lo que funciona en tu nivel) para listar tablas, columnas
y medidas. La salida se guarda en pbi_schema.json para reutilizar.

Uso:
    python pbi_discover.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pbi_cloud import dax_rows, execute_dax

DATASET_ID = "5b04e54f-36e7-4ef0-a041-e4826f124223"  # Contifico Dashboard


def _evaluate(dax: str) -> list[dict]:
    try:
        return dax_rows(execute_dax(DATASET_ID, dax))
    except RuntimeError as e:
        print(f"  [ERROR] {dax[:60]}...: {str(e)[:150]}")
        return []


def main() -> int:
    print(f"Dataset: {DATASET_ID}\n")

    # --- Tablas visibles ---
    print("=" * 50)
    print("TABLAS")
    print("=" * 50)
    tables = _evaluate(
        "EVALUATE FILTER(INFO.VIEW.TABLES(), [IsHidden] = FALSE)"
    )
    table_names: list[str] = []
    for t in tables:
        name = t.get("[Name]")
        table_names.append(name)
        print(f"  - {name}")

    # --- Medidas visibles, agrupadas por tabla ---
    print("\n" + "=" * 50)
    print("MEDIDAS (visibles)")
    print("=" * 50)
    measures = _evaluate(
        "EVALUATE FILTER(INFO.VIEW.MEASURES(), [IsHidden] = FALSE)"
    )
    by_table: dict[str, list[dict]] = {}
    for m in measures:
        tbl = m.get("[Table]", "?")
        by_table.setdefault(tbl, []).append(m)

    for tbl in sorted(by_table.keys()):
        print(f"\n  [{tbl}]")
        for m in by_table[tbl]:
            name = m.get("[Name]")
            dtype = m.get("[DataType]", "")
            fmt = m.get("[FormatString]") or ""
            print(f"    - {name}  ({dtype}{', ' + fmt if fmt else ''})")

    # --- Columnas por tabla (intenta INFO.VIEW.COLUMNS) ---
    print("\n" + "=" * 50)
    print("COLUMNAS (por tabla)")
    print("=" * 50)
    cols = _evaluate(
        "EVALUATE FILTER(INFO.VIEW.COLUMNS(), [IsHidden] = FALSE)"
    )
    if cols:
        cols_by_table: dict[str, list[dict]] = {}
        for c in cols:
            tbl = c.get("[Table]", "?")
            cols_by_table.setdefault(tbl, []).append(c)
        for tbl in sorted(cols_by_table.keys()):
            if tbl not in table_names:
                continue  # saltar columnas de tablas ocultas
            print(f"\n  [{tbl}]")
            for c in cols_by_table[tbl]:
                name = c.get("[Name]")
                dtype = c.get("[DataType]", "")
                print(f"    - {name}  ({dtype})")
    else:
        print("  (INFO.VIEW.COLUMNS no devolvió datos)")

    # Guardar JSON para reutilizar
    schema = {
        "dataset_id": DATASET_ID,
        "tables": [t.get("[Name]") for t in tables],
        "measures_by_table": {
            tbl: [m.get("[Name]") for m in ms]
            for tbl, ms in by_table.items()
        },
        "columns_by_table": {
            tbl: [c.get("[Name]") for c in cs]
            for tbl, cs in (cols_by_table.items() if cols else [])
            if tbl in table_names
        } if cols else {},
    }
    out = Path("pbi_schema.json")
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nEsquema guardado en: {out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
