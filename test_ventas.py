"""Identifica qué medida/expresión da el MTD correcto (≈ $33,640 como el dashboard).

Prueba varias formas de calcular ventas del mes para ver cuál corresponde
con el número que ves en Power BI Service.
"""
from __future__ import annotations

import sys

from pbi_cloud import dax_rows, execute_dax

DATASET_ID = "5b04e54f-36e7-4ef0-a041-e4826f124223"


def main() -> int:
    dax = """
DEFINE
    VAR _Today = TODAY()
    VAR _Year  = YEAR(_Today)
    VAR _Month = MONTH(_Today)
    VAR _CtxMTD =
        FILTER(
            ALL('Calendario'),
            'Calendario'[Anio] = _Year &&
            'Calendario'[Mes]  = _Month &&
            'Calendario'[Date] <= _Today
        )
    VAR _CtxPYMonth =
        FILTER(
            ALL('Calendario'),
            'Calendario'[Anio] = _Year - 1 &&
            'Calendario'[Mes]  = _Month
        )
EVALUATE
ROW(
    "Hoy", _Today,
    "M_VentasReales_MTD",   CALCULATE([Ventas Reales],   _CtxMTD),
    "M_VentasTotales_MTD",  CALCULATE([Ventas Totales],  _CtxMTD),
    "M_VentasMTD_naked",    [Ventas MTD],
    "M_VentasMTD_ctx",      CALCULATE([Ventas MTD], 'Calendario'[Anio] = _Year, 'Calendario'[Mes] = _Month),
    "M_VentasDia_naked",    [Ventas Día],
    "SUM_Total_MTD",        CALCULATE(SUM('Ventas'[Total]), _CtxMTD),
    "SUM_Subtotal_MTD",     CALCULATE(SUM('Ventas'[Subtotal]), _CtxMTD),
    "Distinct_Docs_MTD",    CALCULATE(DISTINCTCOUNT('Ventas'[Data.documento]), _CtxMTD),
    "M_VentasReales_PY",    CALCULATE([Ventas Reales],   _CtxPYMonth),
    "M_VentasTotales_PY",   CALCULATE([Ventas Totales],  _CtxPYMonth),
    "MaxFechaVentas",       CALCULATE(MAX('Ventas'[Fecha])),
    "M_MetaMensual",        [Meta Mensual],
    "M_Cumplimiento",       [Cumplimiento %]
)
"""
    rows = dax_rows(execute_dax(DATASET_ID, dax))
    if not rows:
        print("Sin filas — algo falló.")
        return 1
    r = rows[0]
    print("Resultados (referencia esperada MTD: $33,640):\n")
    for k, v in r.items():
        # quitar corchetes del key para legibilidad
        label = k.strip("[]")
        if isinstance(v, (int, float)):
            print(f"  {label:30} = {v:>15,.2f}")
        else:
            print(f"  {label:30} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
