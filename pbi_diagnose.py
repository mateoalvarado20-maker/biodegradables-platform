"""Diagnóstico: qué dataset usa el reporte publicado y cuándo refrescó.

Compara los 2 datasets Contifico (original y copia) — muestra cuál está
vinculado al reporte que publicaste, su último refresh, y un sanity-check
del valor [Cartera Total] en cada uno.
"""
from __future__ import annotations

import sys

from pbi_cloud import _pbi_request, dax_rows, execute_dax, list_datasets

REPORT_ID = "de5387d4-8203-4a93-8eaf-04212041fece"


def _try(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        return f"ERROR: {e}"


def main() -> int:
    # 1) Cuál dataset usa el reporte publicado
    print("=" * 60)
    print("REPORTE PUBLICADO")
    print("=" * 60)
    rep = _try(_pbi_request, "GET", f"/reports/{REPORT_ID}")
    if isinstance(rep, dict):
        print(f"  Nombre: {rep.get('name')}")
        print(f"  Dataset ID vinculado: {rep.get('datasetId')}")
        linked_dsid = rep.get("datasetId")
    else:
        print(f"  {rep}")
        linked_dsid = None

    # 2) Para cada dataset Contifico: último refresh + Cartera Total
    print("\n" + "=" * 60)
    print("DATASETS CONTIFICO — refresh y muestra de datos")
    print("=" * 60)
    for ds in list_datasets():
        name = ds.get("name", "")
        if "contifico" not in name.lower():
            continue
        dsid = ds["id"]
        marker = " ⭐ (este usa tu reporte publicado)" if dsid == linked_dsid else ""
        print(f"\n  [{name}]{marker}")
        print(f"    ID: {dsid}")
        print(f"    isRefreshable: {ds.get('isRefreshable')}")
        print(f"    Configurado por: {ds.get('configuredBy', '?')}")

        # Último refresh
        refr = _try(_pbi_request, "GET", f"/datasets/{dsid}/refreshes?$top=3")
        if isinstance(refr, dict) and refr.get("value"):
            print("    Refreshes recientes:")
            for r in refr["value"]:
                rt = r.get("refreshType", "?")
                st = r.get("status", "?")
                end = r.get("endTime") or r.get("startTime") or "?"
                print(f"      - {end} [{st}] ({rt})")
        else:
            print(f"    Refreshes: {refr if isinstance(refr, str) else '(ninguno)'}")

        # Schedule de refresh
        sched = _try(_pbi_request, "GET", f"/datasets/{dsid}/refreshSchedule")
        if isinstance(sched, dict):
            enabled = sched.get("enabled")
            times = sched.get("times", [])
            days = sched.get("days", [])
            print(f"    Refresh programado: enabled={enabled}, días={days}, horas={times}")
        else:
            print(f"    Refresh programado: {sched}")

        # Sanity check de Cartera Total
        try:
            res = execute_dax(
                dsid,
                'EVALUATE ROW("CarteraTotal", [Cartera Total], "Vencida", [Cartera Vencida])',
            )
            rows = dax_rows(res)
            if rows:
                ct = rows[0].get("[CarteraTotal]")
                cv = rows[0].get("[Vencida]")
                ct_s = f"${float(ct):,.2f}" if ct is not None else "—"
                cv_s = f"${float(cv):,.2f}" if cv is not None else "—"
                print(f"    [Cartera Total] = {ct_s}")
                print(f"    [Cartera Vencida] = {cv_s}")
        except Exception as e:
            print(f"    Query falló: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
