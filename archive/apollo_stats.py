"""Stats de prospección outbound desde Apollo para el reporte diario.

Lee todas las secuencias activas y agrega métricas comparables:
- Correos enviados (delivered)
- Respuestas (replied)
- Tasa de respuesta
- Secuencia con mejor performance

Notas:
- Las métricas son CUMULATIVAS (totales históricos de cada secuencia, no
  "ayer" específico). Apollo no expone un delta diario sin trackear estado
  uno mismo. Si más adelante querés "respuestas de ayer", agregamos un
  snapshot diario en Azure Tables.
- Requiere APOLLO_API_KEY tipo Master (ya configurada en Key Vault).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("apollo_stats")


def outbound_stats() -> dict | None:
    """Devuelve métricas agregadas de todas las secuencias activas.

    Estructura:
    {
        "activas_count": 3,
        "delivered_total": 1240,
        "replied_total": 67,
        "tasa_respuesta_pct": 5.4,
        "top_secuencia": {
            "nombre": "Sector Hotelero",
            "delivered": 380,
            "replied": 28,
            "tasa_pct": 7.4,
        },
        "secuencias": [
            {"nombre": "...", "delivered": ..., "replied": ..., "tasa_pct": ...},
        ],
    }

    Devuelve None si Apollo falla (sin internet, sin key válida, etc.).
    """
    try:
        import apollo_rest
    except ImportError:
        logger.warning("apollo_rest no disponible")
        return None
    try:
        all_seqs = apollo_rest.list_sequences()
    except Exception as e:
        logger.warning("list_sequences falló: %s", e)
        return None

    activas = [s for s in all_seqs if s.get("active")]
    if not activas:
        return {
            "activas_count": 0,
            "delivered_total": 0,
            "replied_total": 0,
            "tasa_respuesta_pct": None,
            "top_secuencia": None,
            "secuencias": [],
        }

    secuencias = []
    delivered_total = 0
    replied_total = 0
    for s in activas:
        delivered = int(s.get("unique_delivered") or 0)
        replied = int(s.get("unique_replied") or 0)
        tasa = (replied / delivered * 100) if delivered > 0 else None
        secuencias.append({
            "nombre": s.get("name") or "(sin nombre)",
            "delivered": delivered,
            "replied": replied,
            "tasa_pct": round(tasa, 1) if tasa is not None else None,
        })
        delivered_total += delivered
        replied_total += replied

    tasa_global = (
        replied_total / delivered_total * 100 if delivered_total > 0 else None
    )

    # Top por tasa de respuesta (con piso de 20 envíos para que sea
    # estadísticamente relevante; si ninguna llega, usar la más grande)
    candidatas = [s for s in secuencias if s["delivered"] >= 20 and s["tasa_pct"] is not None]
    if not candidatas:
        candidatas = [s for s in secuencias if s["delivered"] > 0]
    top = max(candidatas, key=lambda x: x["tasa_pct"] or 0) if candidatas else None

    # Orden por delivered desc para mostrar
    secuencias.sort(key=lambda x: x["delivered"], reverse=True)

    return {
        "activas_count": len(activas),
        "delivered_total": delivered_total,
        "replied_total": replied_total,
        "tasa_respuesta_pct": round(tasa_global, 1) if tasa_global is not None else None,
        "top_secuencia": top,
        "secuencias": secuencias[:5],
    }
