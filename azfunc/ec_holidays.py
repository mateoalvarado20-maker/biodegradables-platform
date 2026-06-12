"""Calendario de feriados Ecuador. Extraído de daily_report.py para que sea
reutilizable sin importar el módulo del reporte comercial."""
from __future__ import annotations

from datetime import date

EC_HOLIDAYS: dict[int, list[date]] = {
    2025: [
        date(2025, 1, 1),    # Año Nuevo
        date(2025, 3, 3),    # Carnaval (lunes)
        date(2025, 3, 4),    # Carnaval (martes)
        date(2025, 4, 18),   # Viernes Santo
        date(2025, 5, 1),    # Día del Trabajo
        date(2025, 5, 23),   # Batalla de Pichincha (trasladado del 24)
        date(2025, 8, 10),   # Primer Grito de Independencia
        date(2025, 10, 10),  # Independencia de Guayaquil (trasladado del 9)
        date(2025, 11, 3),   # Día de los Difuntos / Independencia Cuenca
        date(2025, 12, 25),  # Navidad
    ],
    2026: [
        date(2026, 1, 1),    # Año Nuevo
        date(2026, 2, 16),   # Carnaval (lunes)
        date(2026, 2, 17),   # Carnaval (martes)
        date(2026, 4, 3),    # Viernes Santo
        date(2026, 5, 1),    # Día del Trabajo
        date(2026, 5, 25),   # Batalla de Pichincha (trasladado, lunes)
        date(2026, 8, 10),   # Primer Grito de Independencia
        date(2026, 10, 9),   # Independencia de Guayaquil
        date(2026, 11, 2),   # Día de los Difuntos
        date(2026, 11, 3),   # Independencia de Cuenca
        date(2026, 12, 25),  # Navidad
    ],
}


def is_holiday(d: date) -> bool:
    return d in EC_HOLIDAYS.get(d.year, [])
