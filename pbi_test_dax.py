"""Prueba varias consultas DAX contra el dataset cloud para ver cuál funciona.

Esto nos dice si executeQueries soporta INFO.* o si tenemos que ir por otro
camino (Power BI Desktop local) para descubrir el esquema.
"""
from __future__ import annotations

import json
import sys

from pbi_cloud import dax_rows, execute_dax

DATASET_ID = "5b04e54f-36e7-4ef0-a041-e4826f124223"  # Contifico Dashboard

TESTS = [
    ("Smoke test: ROW", "EVALUATE ROW(\"answer\", 42)"),
    ("INFO.TABLES plain", "EVALUATE INFO.TABLES()"),
    ("INFO.VIEW.TABLES", "EVALUATE INFO.VIEW.TABLES()"),
    ("INFO.MEASURES plain", "EVALUATE INFO.MEASURES()"),
    ("INFO.VIEW.MEASURES", "EVALUATE INFO.VIEW.MEASURES()"),
    ("INFO.COLUMNS plain", "EVALUATE INFO.COLUMNS()"),
    ("INFO.RELATIONSHIPS", "EVALUATE INFO.RELATIONSHIPS()"),
    ("INFO.MODEL", "EVALUATE INFO.MODEL()"),
]


def run() -> int:
    print(f"Dataset: {DATASET_ID}\n")
    works: list[str] = []
    for name, dax in TESTS:
        print(f"== {name} ==")
        print(f"   DAX: {dax}")
        try:
            res = execute_dax(DATASET_ID, dax)
            rows = dax_rows(res)
            print(f"   OK ({len(rows)} filas)")
            if rows:
                # Mostrar primeras 3 filas para inspección
                for r in rows[:3]:
                    print(f"     {r}")
                if len(rows) > 3:
                    print(f"     ... ({len(rows) - 3} más)")
            works.append(name)
        except RuntimeError as e:
            err = str(e)
            # Mostrar solo lo esencial del error
            if "DatasetExecuteQueriesError" in err:
                print("   FAIL (DAX no aceptado por el servicio)")
            else:
                print(f"   FAIL: {err[:200]}")
        print()

    print("\n=== Resumen ===")
    print(f"Funcionan: {', '.join(works) if works else '(ninguna)'}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
