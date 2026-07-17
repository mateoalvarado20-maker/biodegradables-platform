"""cobranzas_sync — sincronización de la cartera de cobranzas (Phase F → 2026-07-06).

Extraído de teams_bot.py (gate F4: el entrypoint sigue bajando). Sincroniza el
state de actividades de cada asistente con la cartera REAL de Contifico:
agrega clientes nuevos, actualiza montos y QUITA a los que ya pagaron.

Capas: teams_bot → cobranzas_sync → (contifico_client, activity_state,
core_config). Este módulo NO importa nada de teams_bot.
"""
from __future__ import annotations

import asyncio
import logging
import re as _re
import unicodedata as _unicodedata

import activity_state
import contifico_client
import core_config

logger = logging.getLogger("teams_bot")

# Mapeo ciudad → colaborador responsable de cobranza en esa plaza
COBRANZA_COLABORADORES = {
    "UIO": core_config.asistente_email_for_sucursal("UIO"),
    "GYE": core_config.asistente_email_for_sucursal("GYE"),
}


def _slugify(text: str, maxlen: int = 40) -> str:
    """'CLIENTE Ñ SA' → 'cliente-n-sa'. Para activity_id en kebab-case."""
    s = _unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = _re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen] or "cliente"


async def auto_assign_cobranzas() -> None:
    """SINCRONIZA la cartera de cada asistente con Contifico (2026-07-06).

    Antes solo AGREGABA (idempotente por semana): un cliente que pagaba en el
    día seguía apareciendo en el card de la tarde — Gabriela B. y Gladys veían
    clientes ya pagados. Ahora cada corrida deja el state igual a la cartera
    REAL del momento:
      - agrega los clientes nuevos (vencidos con crédito → `cobranza-<slug>`;
        sin crédito con saldo → `cobranza-sc-<slug>`),
      - actualiza el nombre (monto/atraso) de los que siguen debiendo,
      - QUITA los que ya no aparecen en el pull (pagaron / ya no vencidos).
    Corre a las 7:30 y de nuevo justo antes del check-in card de sucursales
    (_job_checkin_sucursales) para que el card salga con data del momento.

    Protección: si el pull de Contifico falla para un colaborador, NO se le
    quita nada (no sabemos la verdad) — solo se loguea el error.
    """
    asignadas = 0
    actualizadas = 0
    removidas = 0
    errores = 0
    # target_user -> {aid: nombre} según la cartera REAL de este momento
    deseadas: dict[str, dict[str, str]] = {}
    # target_user -> False si algún pull suyo falló (bloquea las remociones)
    pulls_ok: dict[str, bool] = {}

    for ciudad, target_user in COBRANZA_COLABORADORES.items():
        deseadas.setdefault(target_user, {})
        pulls_ok.setdefault(target_user, True)
        # 1) Cartera VENCIDA — clientes CON crédito que se pasaron del plazo.
        try:
            # to_thread (fix 2026-06-23): la consulta a Contifico es síncrona y
            # tarda ~2 min; llamarla directo bloqueaba el event loop más allá del
            # --timeout 120 de gunicorn → el worker se reiniciaba y NUNCA se
            # asignaban las cobranzas (count=0 en producción).
            top = await asyncio.to_thread(
                contifico_client.cartera_vencida_por_ciudad, ciudad, n=5
            )
        except Exception as e:
            logger.exception("Cobranza pull falló para %s: %s", ciudad, e)
            errores += 1
            pulls_ok[target_user] = False
            top = []

        for c in top:
            aid = f"cobranza-{_slugify(c['cliente'])}"
            deseadas[target_user][aid] = (
                f"📞 Cobranza: {c['cliente']} — "
                f"${c['saldo_vencido']:,.0f} "
                f"({c['dias_atraso_max']}d atraso)"
            )

        # 2) SIN crédito — no están en el Excel pero tienen saldo (facturado
        #    sin registrar pago). Mismo prefijo `cobranza-` → hereda el UI.
        try:
            sin_cred = await asyncio.to_thread(
                contifico_client.clientes_sin_credito_con_saldo, ciudad
            )
        except Exception as e:
            logger.exception("Cobranza sin-crédito pull falló para %s: %s", ciudad, e)
            errores += 1
            pulls_ok[target_user] = False
            sin_cred = []

        for c in sin_cred:
            aid = f"cobranza-sc-{_slugify(c['cliente'])}"
            deseadas[target_user][aid] = (
                f"⚠️ Sin crédito: {c['cliente']} — "
                f"${c['saldo_pendiente']:,.0f} "
                f"(facturado sin registrar pago)"
            )

    for target_user, aids in deseadas.items():
        # Agregar nuevos / refrescar montos de los existentes. tipo="diaria"
        # (NO "unica"): el check-in renderiza el UI de cobranza solo para
        # diarias y el submit usa mark_daily (fix 2026-06-19). aid ESTABLE por
        # cliente (2026-06-25) → add_adhoc con ValueError = ya existe.
        for aid, nombre in aids.items():
            try:
                activity_state.add_adhoc(
                    aid, nombre,
                    user_email=target_user,
                    tipo="diaria",
                    meta=1,
                    unidad="cliente contactado",
                )
                asignadas += 1
            except ValueError:
                try:
                    if activity_state.set_activity_nombre(
                        aid, nombre, user_email=target_user
                    ):
                        actualizadas += 1
                except Exception as e:
                    logger.exception(
                        "Error actualizando cobranza %s de %s: %s",
                        aid, target_user, e,
                    )
                    errores += 1
            except Exception as e:
                logger.exception(
                    "Error asignando cobranza %s a %s: %s",
                    aid, target_user, e,
                )
                errores += 1

        # Quitar las que YA NO están en la cartera (pagaron) — solo si TODOS
        # los pulls de este colaborador fueron exitosos.
        if not pulls_ok[target_user]:
            logger.warning(
                "Cobranza sync %s: pull incompleto — no se quita nada",
                target_user,
            )
            continue
        try:
            wk_acts = activity_state.get_week(target_user)["activities"]
            pagadas = [
                aid for aid in list(wk_acts)
                if aid.startswith("cobranza-") and aid not in aids
            ]
            for aid in pagadas:
                if activity_state.remove_activity(aid, user_email=target_user):
                    removidas += 1
                    logger.info(
                        "Cobranza sync: %s de %s removida (ya pagó / no vencida)",
                        aid, target_user,
                    )
        except Exception as e:
            logger.exception("Error removiendo pagadas de %s: %s", target_user, e)
            errores += 1

    logger.info(
        "auto_assign_cobranzas: %d asignadas, %d actualizadas, %d removidas, %d errores",
        asignadas, actualizadas, removidas, errores,
    )
    # F0 (2026-07-02): un fallo TOTAL (ningún pull exitoso) debe subir a
    # _reliable_job para retry + alerta — modo de fallo del incidente
    # 2026-06-23. Un día legítimamente sin vencidos (0 errores) NO es error.
    if errores and not any(pulls_ok.values()):
        raise RuntimeError(
            f"auto_assign_cobranzas: fallo total ({errores} errores, ningún pull OK)"
        )
