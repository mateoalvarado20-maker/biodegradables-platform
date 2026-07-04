"""bot_cards — builders de Adaptive Cards del Activities Bot (F4.4a VER-IA).

Funciones PURAS datos→card (dict JSON de Adaptive Card envuelto en Activity).
Extraídas de teams_bot.py el 2026-07-04 (auditoría H2: god-file). Sin I/O de
red: leen state (activity_state) y config (core_config/tenant_roles) y
devuelven el Activity listo para enviar.

Regla de capas: bot_cards NO importa teams_bot (la dependencia es
teams_bot → bot_cards, nunca al revés).
"""
from __future__ import annotations

import logging
from typing import Any
from datetime import date, datetime, timedelta, timezone

from botbuilder.schema import Activity, ActivityTypes, Attachment

import activity_state
import core_config
from tenant_roles import (  # noqa: F401
    CIERRE_CAJA_USERS,
    INFO_EMAIL,
    JOSE_EMAIL,
    JOSE_SUMMARY_TO,
    QUITO_EMAIL,
    ROUTE_USERS,
    SUCURSAL_POR_USER,
    SUPERVISORS_ONLY,
    VALIDADOR_CIERRE_POR_CIUDAD,
    WORKLOAD_SUPERVISORS,
)

logger = logging.getLogger("bot_cards")

DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

CAJA_CHICA_ALERTA_ROJO = 30.0  # Phase V: alerta cuando ≤ $30

def _horario_card_items(fecha_date) -> list[dict[str, Any]]:
    """Items del Adaptive Card para la asistencia/horario del día. Compartido
    por el check-in normal y el card de ruta de José (2026-06-19)."""
    return [
        {
            "type": "TextBlock",
            "text": "⏰ Horario de hoy",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": f"¿Trabajaste el horario estándar ({activity_state.horario_estandar_label(fecha_date)})?",
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "Input.ChoiceSet",
            "id": "horario_estandar",
            "style": "expanded",
            "value": "si",
            "choices": [
                {"title": "✅ Sí, horario estándar", "value": "si"},
                {"title": "⏱️ No, falté o salí antes", "value": "no"},
            ],
        },
        {
            "type": "TextBlock",
            "text": "📝 Si fue NO, completá lo siguiente:",
            "weight": "Bolder",
            "color": "Warning",
            "spacing": "Medium",
            "isSubtle": False,
        },
        {
            "type": "TextBlock",
            "text": "¿Notificaste con anticipación que ibas a faltar?",
            "wrap": True,
            "spacing": "Small",
            "isSubtle": True,
        },
        {
            "type": "Input.ChoiceSet",
            "id": "horario_notifico",
            "style": "expanded",
            "value": "no_aplica",
            "choices": [
                {"title": "📧 Sí, notifiqué por correo (medio formal)", "value": "si_correo"},
                {"title": "❌ No, no notifiqué", "value": "no_notifico"},
                {"title": "— (No aplica, trabajé normal)", "value": "no_aplica"},
            ],
        },
        {
            "type": "Input.Number",
            "id": "horario_horas_permiso",
            "label": "Horas de permiso / ausencia",
            "placeholder": "0",
            "min": 0,
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_franja",
            "label": "¿De qué hora a qué hora?",
            "placeholder": "Ej. 9:30 – 11:00",
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_motivo",
            "label": "Motivo de la ausencia / permiso",
            "placeholder": "Ej. reunión médica, emergencia familiar...",
            "isMultiline": True,
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_porque_no_notifico",
            "label": "Si NO notificaste antes: ¿por qué?",
            "placeholder": "Ej. emergencia inesperada, sin batería...",
            "isMultiline": True,
            "spacing": "Small",
        },
    ]

def _refresh_envios_jose(fecha: date | None = None) -> dict[str, Any]:
    """Phase V (2026-06-10): trae envíos de Contifico de AYER + HOY y los
    mergea al snapshot de HOY. También arrastra los pendientes (no entregados)
    de ayer al snapshot de hoy para que José los siga viendo.

    Idempotente: si una factura ya estaba en el snapshot, NO la borra
    ni sobrescribe (preserva entregas marcadas)."""
    from contifico_client import envios_dia_gye

    fecha = fecha or activity_state._today()
    fecha_str = fecha.isoformat()

    # 1) Arrastrar pendientes de AYER al snapshot de HOY
    try:
        activity_state.carry_over_envios_no_entregados(
            JOSE_EMAIL, fecha_hoy=fecha_str
        )
    except Exception as e:
        logger.exception("carry_over envios José falló: %s", e)

    # 2) Pull de Contifico ayer + hoy (dias_atras=1)
    try:
        envios = envios_dia_gye(fecha, dias_atras=1)
    except Exception as e:
        logger.exception("refresh envios José falló: %s", e)
        return {"ok": False, "error": str(e), "total": 0, "nuevos": 0}
    envios_dict = {e["factura_id"]: e for e in envios}
    res = activity_state.set_envios_snapshot(
        JOSE_EMAIL, envios_dict, fecha=fecha_str
    )
    # 3) Reconciliar: quitar falsos positivos viejos (compras en oficina sin
    # transporte) que quedaron por el merge histórico + carry-forward. La base
    # "fresca" usa una ventana de 7 días para NO podar envíos reales recientes
    # aún no entregados — solo se quitan facturas que el filtro actual ya no
    # considera envío (y que no son ad-hoc ni tienen entrega marcada). Fix 2026-06-19.
    try:
        fresh_ids = {e["factura_id"] for e in envios_dia_gye(fecha, dias_atras=7)}
        rec = activity_state.reconcile_envios_snapshot(
            JOSE_EMAIL, fresh_ids, fecha=fecha_str
        )
        if rec.get("removed"):
            logger.info(
                "reconcile envios José: %d falsos positivos removidos del snapshot",
                rec["removed"],
            )
    except Exception as e:
        logger.exception("reconcile envios José falló: %s", e)
    res["ok"] = True
    return res

def _jose_actividades_items(email: str) -> list[dict[str, Any]]:
    """Items del card para las actividades diarias/semanales que gerencia DELEGA
    a José (2026-06-25). Se ven y se marcan dentro de su card de ruta (no recibe
    el check-in normal). Devuelve [] si no tiene ninguna (no satura el card).
    Reusa los ids estado__/valor__/razon__/avance__/notas__ del check-in."""
    try:
        wk = activity_state.get_week(email)
    except Exception:
        return []
    acts = wk.get("activities", {})
    diarias = [
        (aid, a) for aid, a in acts.items()
        if a.get("tipo") == "diaria" and not aid.startswith("cobranza-")
    ]
    semanales = [
        (aid, a) for aid, a in acts.items()
        if a.get("tipo") != "diaria"
        and activity_state.task_effective_status(a) != "finalizada"
    ]
    if not diarias and not semanales:
        return []

    items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "📋 Actividades asignadas",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }, {
        "type": "TextBlock",
        "text": "Tareas que te asignó gerencia. Marcalas acá.",
        "wrap": True, "isSubtle": True, "size": "Small", "spacing": "None",
    }]
    for aid, a in diarias:
        meta = a.get("meta")
        items.append({
            "type": "TextBlock",
            "text": f"**{a.get('nombre', aid)}**" + (f" (meta {meta})" if meta else ""),
            "wrap": True, "spacing": "Medium", "weight": "Bolder",
        })
        items.append({
            "type": "Input.ChoiceSet", "id": f"estado__{aid}", "style": "expanded",
            "value": "skip",
            "choices": [
                {"title": "✅ Hecho", "value": "hecho"},
                {"title": "⚠️ Parcial", "value": "parcial"},
                {"title": "❌ No hecho", "value": "no_hecho"},
                {"title": "— Saltar", "value": "skip"},
            ],
        })
        items.append({
            "type": "Input.Number", "id": f"valor__{aid}",
            "placeholder": "¿Cuánto se hizo? (cantidad)", "min": 0,
        })
        items.append({
            "type": "Input.Text", "id": f"razon__{aid}",
            "placeholder": "Si Parcial o No hecho: ¿por qué?",
        })
    for aid, a in semanales:
        current = a.get("avance") or 0
        items.append({
            "type": "TextBlock",
            "text": f"**{a.get('nombre', aid)}** — avance actual {current:.0f}%",
            "wrap": True, "spacing": "Medium", "weight": "Bolder",
        })
        items.append({
            "type": "Input.Number", "id": f"avance__{aid}",
            "placeholder": "Nuevo % avance (0-100)", "min": 0, "max": 100,
        })
        items.append({
            "type": "Input.Text", "id": f"notas__{aid}",
            "placeholder": "Notas (opcional)",
        })
    items.append({
        "type": "ActionSet",
        "actions": [{
            "type": "Action.Submit",
            "title": "💾 Guardar actividades",
            "style": "positive",
            "data": {"intent": "jose_marcar_actividades"},
        }],
    })
    return items

def _build_checkin_card(
    user_email: str | None = None,
    alt_sender: str | None = None,
) -> Activity:
    """Construye una Adaptive Card con casillas/inputs para el check-in.

    Args:
        alt_sender: email adicional autorizado a hacer submit de ESTE card
            (rotación de sábados: el chofer cubre al asistente 1 de su
            sucursal y llena el card de ese rol — las marcas van al state
            de `user_email`, no al del remitente).
    """
    wk = activity_state.get_week(user_email)
    diarias = [
        (aid, a) for aid, a in wk["activities"].items() if a["tipo"] == "diaria"
    ]
    # Phase L: ordenar carry-overs primero, después por priority alta→media→baja
    from datetime import date as _date_cls2, timedelta as _td2
    _today_iso = activity_state._today().isoformat()  # TZ Ecuador (A9)
    _yest_iso = (activity_state._today() - _td2(days=1)).isoformat()
    diarias = activity_state.sort_activities_by_priority_then_carryover(
        diarias, _today_iso, _yest_iso
    )
    # Sábado: SIN cobranzas — no se gestionan ese día (2026-07-04).
    if activity_state._today().weekday() == 5:
        diarias = [(aid, a) for aid, a in diarias if not aid.startswith("cobranza-")]
    # Las finalizadas NO se muestran en el card (2026-06-24): cuando el
    # colaborador confirma "quitar del card" una actividad al 100%, se finaliza
    # y deja de aparecer acá. Las recolocadas vuelven a pendiente y sí aparecen.
    semanales = [
        (aid, a) for aid, a in wk["activities"].items()
        if a["tipo"] != "diaria"
        and activity_state.task_effective_status(a) != "finalizada"
    ]

    hoy = datetime.now(activity_state.LOCAL_TZ)
    fecha_str = f"{DIAS_ES[hoy.weekday()]} {hoy.day:02d}/{hoy.month:02d}"

    # Phase S+ (2026-06-09): cada sección envuelta en Container con
    # style="emphasis" para que se vean visualmente separadas como cuadros.
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "📋 Cierre del día",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": fecha_str.capitalize(),
            "spacing": "None",
            "isSubtle": True,
        },
    ]

    if alt_sender:
        _owner_name = core_config.display_name_for(user_email) or user_email
        _suc_name = core_config.sucursal_name_for(user_email)
        body.append({
            "type": "TextBlock",
            "text": (
                f"🔁 **Turno del sábado** — hoy cubrís la sucursal "
                f"{_suc_name or 'asignada'}: estas son las actividades de "
                f"{_owner_name} (Asistente 1). Llenalas vos."
            ),
            "wrap": True,
            "color": "Accent",
            "spacing": "Small",
        })

    # ===== CONTAINER 1: Horario de hoy (helper compartido con José) =====
    horario_items = _horario_card_items(hoy.date())
    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": horario_items,
    })

    if not diarias and not semanales:
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": [{
                "type": "TextBlock",
                "text": (
                    "No tenés actividades configuradas todavía. "
                    "Para sumar una, simplemente escribime en este chat: "
                    "_'agregame visita a Cliente X'_, _'sumame revisar carteras semanales'_, "
                    "etc. — y las voy armando juntos a tu rutina."
                ),
                "wrap": True,
                "color": "Accent",
            }],
        })

    if diarias:
        diarias_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📅 Actividades diarias",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        for aid, a in diarias:
            meta = a.get("meta")
            unidad = a.get("unidad", "")
            meta_txt = f" (meta {meta} {unidad})" if meta else ""
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            is_co = activity_state.is_carryover_alta(a, _today_iso, _yest_iso)
            co_prefix = "⚠️ PENDIENTE DE AYER · " if is_co else ""
            color = "Attention" if is_co else "Default"
            es_cobranza = aid.startswith("cobranza-")
            es_sin_credito = aid.startswith("cobranza-sc-")
            diarias_items.append({
                "type": "TextBlock",
                "text": f"{co_prefix}{prio_badge}**{a['nombre']}**{meta_txt}",
                "wrap": True,
                "spacing": "Medium",
                "color": color,
            })
            if es_sin_credito:
                # Cliente sin crédito aprobado al que se facturó sin registrar el
                # pago: avisar y pedir el motivo del no-pago como observación.
                diarias_items.append({
                    "type": "TextBlock",
                    "text": ("ℹ️ Este cliente **no tiene crédito aprobado**. "
                             "Indica por qué no ha pagado."),
                    "wrap": True,
                    "spacing": "None",
                    "isSubtle": True,
                })
            if es_cobranza:
                diarias_items.append({
                    "type": "Input.ChoiceSet",
                    "id": f"estado__{aid}",
                    "style": "expanded",
                    "value": "no_contactado",
                    "choices": [
                        {"title": "📞 Contactado", "value": "contactado"},
                        {"title": "❌ No contactado", "value": "no_contactado"},
                    ],
                })
                diarias_items.append({
                    "type": "Input.Text",
                    "id": f"razon__{aid}",
                    "placeholder": (
                        "¿Por qué no ha pagado? "
                        "(ej. 'paga el lunes', 'se facturó sin registrar el pago')"
                        if es_sin_credito else
                        "¿Qué te dijo el cliente? "
                        "(ej. 'paga el viernes', 'no contesta', 'pidió plazo de 15 días')"
                    ),
                    "isMultiline": True,
                })
            else:
                diarias_items.append({
                    "type": "Input.ChoiceSet",
                    "id": f"estado__{aid}",
                    "style": "expanded",
                    "value": "skip",
                    "choices": [
                        {"title": "✅ Hecho", "value": "hecho"},
                        {"title": "⚠️ Parcial", "value": "parcial"},
                        {"title": "❌ No hecho", "value": "no_hecho"},
                        {"title": "— Sin actividad / saltar", "value": "skip"},
                    ],
                })
                placeholder = (
                    f"Cuánto se hizo? (meta {meta})"
                    if meta is not None
                    else "Cuánto se hizo? (cantidad)"
                )
                diarias_items.append({
                    "type": "Input.Number",
                    "id": f"valor__{aid}",
                    "placeholder": placeholder,
                    "min": 0,
                })
                diarias_items.append({
                    "type": "Input.Text",
                    "id": f"razon__{aid}",
                    "placeholder": "Si Parcial o No hecho: por qué?",
                    "isMultiline": False,
                })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": diarias_items,
        })

    if semanales:
        semanales_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📌 Proyectos semanales",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        for aid, a in semanales:
            current = a.get("avance") or 0
            semanales_items.append({
                "type": "TextBlock",
                "text": f"**{a['nombre']}** — actual {current:.0f}%",
                "wrap": True,
                "spacing": "Medium",
            })
            semanales_items.append({
                "type": "Input.Number",
                "id": f"avance__{aid}",
                "placeholder": "Nuevo % avance (0-100). Vacío si no avanzaste.",
                "min": 0,
                "max": 100,
            })
            semanales_items.append({
                "type": "Input.Text",
                "id": f"notas__{aid}",
                "placeholder": "Notas breves (opcional)",
                "isMultiline": False,
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": semanales_items,
        })

    # Phase R (2026-06-08) — TikTok seguidores semanales (para users con
    # actividad de videos TikTok). Pregunta una vez por semana, principalmente
    # lunes. 2026-06-24: actividad vigente "tiktok-videos-diarios" (+compat con
    # la vieja "video-tiktok").
    has_tiktok = any(
        aid in ("tiktok-videos-diarios", "video-tiktok")
        for aid in wk["activities"].keys()
    )
    if has_tiktok:
        tt = activity_state.get_tiktok_seguidores_semana(user_email)
        tiktok_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📱 TikTok — seguidores",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        if tt:
            seguidores = tt.get("seguidores", 0)
            delta = tt.get("delta_vs_semana_anterior")
            if delta is not None:
                if delta > 0:
                    delta_str = f"📈 +{delta} vs semana anterior"
                    delta_color = "Good"
                elif delta < 0:
                    delta_str = f"📉 {delta} vs semana anterior"
                    delta_color = "Attention"
                else:
                    delta_str = "≈ igual que semana anterior"
                    delta_color = "Default"
                tiktok_items.append({
                    "type": "TextBlock",
                    "text": (
                        f"Esta semana arrancaste con **{seguidores}** seguidores · {delta_str}"
                    ),
                    "wrap": True,
                    "color": delta_color,
                    "spacing": "Small",
                })
            else:
                tiktok_items.append({
                    "type": "TextBlock",
                    "text": f"Esta semana arrancaste con **{seguidores}** seguidores.",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "Small",
                })
            tiktok_items.append({
                "type": "TextBlock",
                "text": "_(Si te equivocaste cargando, completá abajo para corregirlo.)_",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "None",
            })
            tiktok_items.append({
                "type": "Input.Number",
                "id": "tiktok_seguidores_inicio",
                "label": "Corregir seguidores de la semana (opcional)",
                "placeholder": str(seguidores),
                "min": 0,
            })
        else:
            tiktok_items.append({
                "type": "TextBlock",
                "text": (
                    "Es el inicio de la semana o todavía no cargaste tus seguidores. "
                    "¿Con cuántos seguidores arrancaste esta semana en TikTok?"
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            })
            tiktok_items.append({
                "type": "Input.Number",
                "id": "tiktok_seguidores_inicio",
                "label": "Seguidores al inicio de la semana",
                "placeholder": "0",
                "min": 0,
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": tiktok_items,
        })

    # Phase N — cierre de caja para info@/quito@
    user_email_l = (user_email or "").strip().lower()
    if user_email_l in CIERRE_CAJA_USERS:
        sucursal = SUCURSAL_POR_USER.get(user_email_l, "")
        fondo_sucursal = activity_state.get_fondo_caja(sucursal)
        cierre_items: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "text": f"💵 Cierre de caja {sucursal}",
                "weight": "Bolder",
                "size": "ExtraLarge",
                "color": "Accent",
            },
            {
                "type": "TextBlock",
                "text": (
                    f"Contá las denominaciones del FONDO que dejás en caja "
                    f"(no las ventas — esas son aparte). "
                    f"El fondo de caja de {sucursal} debe ser **${fondo_sucursal:,.0f}**. "
                    "Yo verifico que las denominaciones sumen ese monto."
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            },
            {
                "type": "TextBlock",
                "text": "BILLETES (cantidad)",
                "weight": "Bolder",
                "size": "Small",
                "color": "Good",
                "spacing": "Medium",
            },
        ]
        for fid, label in [
            ("caja_b100", "$100"),
            ("caja_b50", "$50"),
            ("caja_b20", "$20"),
            ("caja_b10", "$10"),
            ("caja_b5", "$5"),
            ("caja_b1", "$1 (billete)"),
        ]:
            cierre_items.append({
                "type": "Input.Number",
                "id": fid,
                "label": label,
                "placeholder": "0",
                "min": 0,
            })
        cierre_items.append({
            "type": "TextBlock",
            "text": "MONEDAS (cantidad)",
            "weight": "Bolder",
            "size": "Small",
            "color": "Good",
            "spacing": "Medium",
        })
        for fid, label in [
            ("caja_m1", "$1 (moneda)"),
            ("caja_m050", "50¢"),
            ("caja_m025", "25¢"),
            ("caja_m010", "10¢"),
            ("caja_m005", "5¢"),
            ("caja_m001", "1¢"),
        ]:
            cierre_items.append({
                "type": "Input.Number",
                "id": fid,
                "label": label,
                "placeholder": "0",
                "min": 0,
            })
        cierre_items.append({
            "type": "Input.Text",
            "id": "caja_notas",
            "label": "Notas (opcional)",
            "placeholder": "Solo si hay algo a aclarar (ej. faltó moneda de 25¢)",
            "isMultiline": False,
            "spacing": "Medium",
        })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": cierre_items,
        })

        # ===== 🍫 Chocolates de reviews (Phase Q+R) =====
        choco = activity_state.get_chocolates_semana(user_email)
        chocolates_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "🍫 Chocolates (reviews Google / Facebook)",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        if not choco or not choco.get("stock_inicial"):
            chocolates_items.append({
                "type": "TextBlock",
                "text": (
                    "Es el inicio de la semana o todavía no cargaste tu stock. "
                    "¿Con cuántos chocolates arrancás esta semana?"
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            })
            chocolates_items.append({
                "type": "Input.Number",
                "id": "chocolates_inicial",
                "label": "Stock inicial de chocolates (no se podrá modificar después)",
                "placeholder": "0",
                "min": 0,
            })
        else:
            stock_actual = choco.get("stock_actual", 0)
            stock_inicial = choco.get("stock_inicial", 0)
            entregado = choco.get("total_entregado", 0)
            recargado = choco.get("total_recargado", 0)
            color_stock = (
                "Attention" if stock_actual <= activity_state.CHOCOLATES_UMBRAL
                else "Good"
            )
            stock_msg = (
                f"📦 **Stock actual: {stock_actual} chocolates**\n"
                f"_(inicial {stock_inicial} + recargas {recargado} − entregas {entregado})_"
            )
            chocolates_items.append({
                "type": "TextBlock",
                "text": stock_msg,
                "wrap": True,
                "color": color_stock,
                "weight": "Bolder",
                "spacing": "Small",
            })
            if stock_actual <= activity_state.CHOCOLATES_UMBRAL:
                chocolates_items.append({
                    "type": "TextBlock",
                    "text": (
                        "⚠️ **Quedan pocos chocolates.** "
                        "Solicitá más antes de quedarte sin."
                    ),
                    "wrap": True,
                    "color": "Attention",
                    "isSubtle": False,
                    "spacing": "Small",
                })
        chocolates_items.append({
            "type": "Input.Number",
            "id": "chocolates_recarga",
            "label": "Recarga / Restock recibido hoy (opcional)",
            "placeholder": "Solo si te dieron más chocolates hoy",
            "min": 0,
            "spacing": "Medium",
        })
        chocolates_items.append({
            "type": "Input.Number",
            "id": "chocolates_entregas",
            "label": "¿Cuántas entregas hiciste hoy? (= reviews recibidos)",
            "placeholder": "0",
            "min": 0,
            "spacing": "Small",
        })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": chocolates_items,
        })

    body.append({
        "type": "TextBlock",
        "text": "Al enviar, marco todo y mando el resumen a Daniel y Gabriela.",
        "isSubtle": True,
        "wrap": True,
        "spacing": "Large",
    })

    # Fase 2 (auditoría A5): el card embebe el contexto con el que fue
    # generado (usuario, fecha, semana). El submit los valida — un card
    # viejo que quedó vivo en el chat de Teams ya no escribe marcas en la
    # fecha/semana equivocada ni en otro usuario.
    _ctx = {
        "ctx_user": (user_email or "").strip().lower(),
        "ctx_fecha": activity_state._today().isoformat(),
        "ctx_wk": activity_state.week_key(),
        # Rotación de sábados: remitente alternativo autorizado (chofer que
        # cubre al asistente 1). Vacío en el card normal.
        "ctx_alt": (alt_sender or "").strip().lower(),
    }
    actions: list[dict[str, Any]] = []
    if user_email_l in CIERRE_CAJA_USERS:
        actions.append({
            "type": "Action.Submit",
            "title": "🧮 Calcular total",
            "data": {"intent": "calc_cierre_caja", **_ctx},
        })
    actions.append({
        "type": "Action.Submit",
        "title": "💾 GUARDAR Y ENVIAR RESUMEN",
        "style": "positive",  # botón VERDE para que se resalte
        "data": {"intent": "submit_checkin", **_ctx},
    })

    card_json: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }

    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])

def _build_done_activities_card(
    user_email: str, items: list[tuple[str, str]],
    alt_sender: str | None = None,
) -> Activity:
    """Tarjeta de seguimiento (2026-06-24): por cada actividad que quedó al 100%
    pregunta si quitarla del card (finalizar), recolocarla para otro día (con
    fecha a elección) o dejarla como está."""
    hoy_iso = activity_state._today().isoformat()
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "🎉 ¡Actividades al 100%!",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Good",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": ("Estas actividades quedaron al 100%. ¿Qué hacés con cada una? "
                     "Si elegís *recolocar*, decime para qué día."),
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]
    for aid, nombre in items:
        body.append({
            "type": "TextBlock",
            "text": f"✅ **{nombre}**",
            "wrap": True,
            "weight": "Bolder",
            "spacing": "Medium",
            "separator": True,
        })
        body.append({
            "type": "Input.ChoiceSet",
            "id": f"done_action__{aid}",
            "style": "expanded",
            "choices": [
                {"title": "🗑️ Quitarla del card (ya está terminada)", "value": "quitar"},
                {"title": "🔁 Recolocarla para hacerla otro día", "value": "recolocar"},
            ],
        })
        body.append({
            "type": "Input.Date",
            "id": f"recolocar_fecha__{aid}",
            "label": "Si la recolocás: ¿para qué día?",
            "spacing": "Small",
        })
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{
            "type": "Action.Submit",
            "title": "💾 Confirmar",
            "style": "positive",
            "data": {"intent": "confirm_done", "ctx_user": user_email,
                     "ctx_fecha": hoy_iso,
                     "ctx_alt": (alt_sender or "").strip().lower()},
        }],
    }
    return Activity(
        type=ActivityTypes.message,
        attachments=[Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=card,
        )],
    )

def _build_task_confirmation_card(
    user_email: str,
    activity_id: str,
    nombre: str,
    fecha_limite: str | None,
    status_efectivo: str,
) -> Activity:
    """Card proactivo que pregunta si una tarea ya se completó (Feature
    confirmación de cierre, 2026-06-15).

    SOLO "Sí, completada" finaliza la tarea. Las otras opciones la mantienen
    viva: sigue en proceso, posponer N días, o actualizar la fecha límite. Así
    una tarea recurrente o de largo plazo nunca desaparece por error.
    """
    hoy_iso = activity_state._today().isoformat()
    fl_human = ""
    if fecha_limite:
        try:
            fl_human = datetime.fromisoformat(fecha_limite).strftime("%d/%m/%Y")
        except ValueError:
            fl_human = fecha_limite
    venc_txt = " (⚠️ vencida)" if status_efectivo == "vencida" else ""
    intro = (
        f"📌 La tarea **{nombre}**"
        + (f" llegó a su fecha límite **{fl_human}**{venc_txt}" if fl_human else "")
        + ".\n\n¿La actividad ya fue culminada?"
    )
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": "✅ Confirmación de tarea",
         "size": "Large", "weight": "Bolder", "color": "Accent"},
        {"type": "TextBlock", "text": intro, "wrap": True, "spacing": "Small"},
        {"type": "Input.ChoiceSet", "id": "task_action", "style": "expanded",
         "value": "si_completada",
         "choices": [
             {"title": "✅ Sí, actividad completada", "value": "si_completada"},
             {"title": "🔄 No, continúa en proceso", "value": "no_proceso"},
             {"title": "⏰ Posponer (indicá días abajo)", "value": "posponer"},
             {"title": "📅 Actualizar fecha (indicá abajo)", "value": "actualizar_fecha"},
         ]},
        {"type": "Input.Number", "id": "task_snooze_dias",
         "placeholder": "Días a posponer (default 3)", "value": 3, "min": 1},
        {"type": "Input.Text", "id": "task_nueva_fecha",
         "placeholder": "Nueva fecha AAAA-MM-DD (solo si elegís 'Actualizar fecha')"},
    ]
    card_json = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{
            "type": "Action.Submit",
            "title": "Confirmar",
            "data": {
                "intent": "confirm_task",
                "task_aid": activity_id,
                "ctx_user": user_email.lower(),
                "ctx_fecha": hoy_iso,
            },
        }],
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])

def _build_confirmacion_cierre_card(
    emisor_email: str,
    fecha: str,
    sucursal: str,
    total: float,
    entregado: float,
    fondo: float,
    es_recordatorio: bool = False,
) -> Activity:
    """Construye el Adaptive Card que se manda al validador (Daniel/Gabriela)
    para que confirme la recepción del efectivo del cierre.

    Phase P (2026-06-05).
    """
    emisor_alias = emisor_email.split("@")[0]
    fecha_obj = datetime.fromisoformat(fecha).date()
    fecha_humana = fecha_obj.strftime("%d/%m/%Y")

    header_text = (
        f"⏰ RECORDATORIO · Confirmación de cierre pendiente"
        if es_recordatorio
        else f"📥 Confirmación de cierre de caja"
    )
    header_color = "Attention" if es_recordatorio else "Accent"
    intro = (
        f"**{emisor_alias}** ({sucursal}) reportó entrega del cierre del "
        f"**{fecha_humana}**.\n"
        f"¿Confirmás que recibiste este monto?"
    )

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": header_text,
            "size": "Large",
            "weight": "Bolder",
            "color": header_color,
        },
        {
            "type": "TextBlock",
            "text": intro,
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "FactSet",
            "spacing": "Medium",
            "facts": [
                {"title": "Total en caja:", "value": f"${total:,.2f}"},
                {"title": "(–) Fondo fijo:", "value": f"${fondo:,.2f}"},
                {"title": "VALOR A ENTREGAR:", "value": f"${entregado:,.2f}"},
            ],
        },
        {
            "type": "TextBlock",
            "text": "Marca abajo qué pasó:",
            "weight": "Bolder",
            "spacing": "Medium",
        },
        {
            "type": "Input.ChoiceSet",
            "id": "confirm_estado",
            "style": "expanded",
            "value": "confirmado",
            "choices": [
                {"title": f"✅ Sí, recibí exactamente ${entregado:,.2f}", "value": "confirmado"},
                {"title": "⚠️ Recibí un monto distinto (completar abajo)", "value": "discrepancia"},
                {"title": "📝 Pendiente de recibir todavía", "value": "no_recibido"},
            ],
        },
        {
            "type": "Input.Number",
            "id": "confirm_monto",
            "placeholder": "Monto real recibido (solo si fue distinto)",
            "min": 0,
        },
        {
            "type": "Input.Text",
            "id": "confirm_razon",
            "placeholder": "Razón si hay diferencia o si está pendiente (opcional)",
            "isMultiline": True,
        },
    ]

    card_json = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{
            "type": "Action.Submit",
            "title": "✔️ Confirmar recepción",
            "data": {
                "intent": "confirmar_cierre",
                "emisor_email": emisor_email,
                "fecha": fecha,
                "sucursal": sucursal,
                "entregado": entregado,
            },
        }],
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])

def _build_jose_ruta_card(
    user_email: str | None = None, skip_refresh: bool = False
) -> Activity:
    """Phase V (2026-06-10): Adaptive Card para José (chofer GYE).

    Estructura:
      1. Header con botón ACTUALIZAR
      2. Estado de ruta: SALIDA/LLEGADA + historial salidas del día
      3. Envíos pendientes (ayer + hoy, carry-over de no entregados)
      4. Caja chica con alerta roja ≤ $30
    """
    email = (user_email or JOSE_EMAIL).lower()
    hoy = activity_state._today()
    hoy_str = hoy.isoformat()
    fecha_label = hoy.strftime("%A %d/%m/%Y")

    # Refrescar snapshot antes de armar el card (skip si ya se hizo refresh
    # explícito desde el handler de Actualizar).
    if not skip_refresh:
        try:
            _refresh_envios_jose(hoy)
        except Exception as e:
            logger.exception("refresh envios José en card build: %s", e)

    try:
        ruta = activity_state.get_ruta_dia(email, hoy_str)
        salidas = ruta.get("salidas", []) or []
        salida_abierta = next(
            (s for s in salidas if not s.get("fin_ts")), None
        )
        entregas_consol = activity_state.get_entregas_consolidadas_dia(
            email, hoy_str
        ) or {}
        cc = activity_state.get_caja_chica(email) or {"inicial": None, "saldo": 0.0, "movimientos": []}
    except Exception as e:
        logger.exception("error leyendo state de José: %s", e)
        ruta = {"salidas": [], "envios_snapshot": {}}
        salidas = []
        salida_abierta = None
        entregas_consol = {}
        cc = {"inicial": None, "saldo": 0.0, "movimientos": []}

    # Helpers
    def _hora_local(iso: str) -> str:
        try:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(iso.replace("Z", "+00:00"))
            return d.astimezone().strftime("%H:%M")
        except Exception:
            return "?"

    def _fmt_fecha_emision(fe: str) -> str:
        """Convierte 'DD/MM/YYYY' (Contifico) o 'YYYY-MM-DD' (ISO) a etiqueta corta."""
        try:
            if "/" in fe:
                d, m, y = fe.split("/")
                from datetime import date as _dt2
                fe_date = _dt2(int(y), int(m), int(d))
            else:
                from datetime import date as _dt2
                fe_date = _dt2.fromisoformat(fe)
            if fe_date == hoy:
                return "HOY"
            from datetime import timedelta as _td2
            if fe_date == hoy - _td2(days=1):
                return "AYER"
            return fe_date.strftime("%d/%m")
        except Exception:
            return fe[:5] if fe else "?"

    # Calcular contadores
    n_entregadas = sum(1 for e in entregas_consol.values() if e.get("status") == "entregado")
    n_no_entregadas = sum(1 for e in entregas_consol.values() if e.get("status") == "no_entregado")
    n_pendientes = sum(1 for e in entregas_consol.values() if e.get("status") == "pendiente")

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"🚚 Ruta de José — {fecha_label}",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"⏳ Pendientes: **{n_pendientes}**  ·  "
                f"✅ Entregadas: **{n_entregadas}**  ·  "
                f"❌ No entregadas: **{n_no_entregadas}**"
            ),
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    # ============ BLOQUE 1: Estado de ruta + Salida/Llegada ============
    ruta_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "📍 Estado de ruta",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]

    if salida_abierta:
        ini_local = _hora_local(salida_abierta.get("inicio_ts", ""))
        ruta_items.append({
            "type": "TextBlock",
            "text": f"🟢 **EN RUTA** desde las {ini_local}",
            "wrap": True,
            "color": "Good",
            "weight": "Bolder",
            "size": "Large",
        })
    else:
        ruta_items.append({
            "type": "TextBlock",
            "text": "🏢 **EN OFICINA**",
            "wrap": True,
            "color": "Default",
            "weight": "Bolder",
            "size": "Large",
        })

    # Historial de salidas del día (compacto)
    if salidas:
        hist_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "Salidas de hoy:",
            "weight": "Bolder",
            "isSubtle": True,
            "spacing": "Medium",
            "size": "Small",
        }]
        for i, s in enumerate(salidas, 1):
            if s.get("marcado_en_oficina"):
                continue  # salidas virtuales para entregas en oficina, ocultas
            ini = _hora_local(s.get("inicio_ts", ""))
            fin = _hora_local(s.get("fin_ts", "")) if s.get("fin_ts") else "(en curso)"
            entr_n = sum(
                1 for e in (s.get("entregas") or {}).values()
                if e.get("status") == "entregado"
            )
            hist_items.append({
                "type": "TextBlock",
                "text": f"#{i}: {ini} → {fin}  ({entr_n} entregas)",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "None",
            })
        if len(hist_items) > 1:
            ruta_items.append({
                "type": "Container",
                "items": hist_items,
            })

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": ruta_items,
    })

    # ============ BLOQUE 2: Lista de envíos ============
    envios_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "📦 Envíos",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]

    if not entregas_consol:
        envios_items.append({
            "type": "TextBlock",
            "text": "_(No hay envíos pendientes hoy. Apretá 🔄 Actualizar si esperás nuevas facturas.)_",
            "wrap": True,
            "isSubtle": True,
        })
    else:
        # Ordenar: pendientes primero (por fecha asc), después no_entregado, después entregadas
        def _orden(item):
            fid, env = item
            status = env.get("status", "pendiente")
            prio = {"pendiente": 0, "no_entregado": 1, "entregado": 2}.get(status, 3)
            return (prio, env.get("fecha_emision", ""))
        envios_ordenados = sorted(entregas_consol.items(), key=_orden)

        # Opción A (2026-06-23): las entregas YA hechas se colapsan en una sola
        # línea compacta al final, para que el card sea más corto cuando José
        # vuelve. Solo los PENDIENTES y los NO ENTREGADOS (accionables) se
        # muestran expandidos.
        entregados_compactos: list[dict[str, Any]] = []

        for fid, env in envios_ordenados:
            cliente = env.get("cliente", "?")
            doc = env.get("documento", "?")
            dir_fac = env.get("direccion_factura", "")
            total = env.get("total", 0)
            status = env.get("status", "pendiente")
            dir_real_guardada = env.get("direccion_real", "") or ""
            obs_guardada = env.get("observacion", "") or ""
            razon_guardada = env.get("razon_no_entrega", "") or ""
            fe = env.get("fecha_emision", "")
            badge = _fmt_fecha_emision(fe)

            # Color del Container por estado
            box_style = "default"
            if status == "entregado":
                box_style = "good"
            elif status == "no_entregado":
                box_style = "attention"

            # Phase V: destinos ad-hoc tienen su propio badge
            if env.get("adhoc"):
                tipo_a = (env.get("tipo_adhoc") or "entrega").upper()
                badge_tag = f"➕ {tipo_a}"
                total_str = f"${total:,.2f}" if total > 0 else "(sin monto)"
            else:
                badge_tag = badge
                total_str = f"${total:,.2f}"
            envio_items: list[dict[str, Any]] = [
                {
                    "type": "TextBlock",
                    "text": f"[{badge_tag}] **{cliente}** — {total_str}",
                    "wrap": True,
                    "weight": "Bolder",
                    "size": "Medium",
                },
                {
                    "type": "TextBlock",
                    "text": f"📄 {doc}",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "None",
                    "size": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": f"📍 Dirección factura: {dir_fac or '(sin dirección)'}",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "None",
                    "size": "Small",
                },
            ]

            if status == "entregado":
                # Colapsado: una sola línea (cliente + hora). El detalle
                # (dirección real, pago, obs) ya quedó guardado y aparece en el
                # resumen del equipo; acá no satura el card.
                hora_e = _hora_local(env.get("entrega_ts", "")) if env.get("entrega_ts") else ""
                linea = f"✅ **{cliente}**" + (f" · {hora_e}" if hora_e else "")
                if env.get("pago_envio"):
                    linea += f" · 💰${env['pago_envio']:,.2f}"
                entregados_compactos.append({
                    "type": "TextBlock",
                    "text": linea,
                    "wrap": True,
                    "spacing": "None",
                    "size": "Small",
                })
            elif status == "no_entregado":
                envio_items.append({
                    "type": "TextBlock",
                    "text": f"❌ **NO ENTREGADO** — {razon_guardada or 'sin razón'}",
                    "color": "Attention",
                    "weight": "Bolder",
                    "spacing": "Small",
                    "wrap": True,
                })
                # Permitir re-marcar como entregado (botón)
                envios_items.append({
                    "type": "Container",
                    "style": box_style,
                    "spacing": "Medium",
                    "separator": True,
                    "items": envio_items,
                })
                envios_items.append({
                    "type": "ActionSet",
                    "actions": [{
                        "type": "Action.Submit",
                        "title": "🔄 Reintentar (marcar como pendiente)",
                        "data": {"intent": "jose_reintentar_envio", "factura_id": fid},
                    }],
                })
            else:
                # PENDIENTE — inputs + botones
                envio_items.extend([
                    {
                        "type": "Input.ChoiceSet",
                        "id": f"jose_dir_ok_{fid}",
                        "label": "¿La dirección de la factura es correcta?",
                        "style": "expanded",
                        "isMultiSelect": False,
                        "value": "si",
                        "choices": [
                            {"title": "✅ Sí, la dirección está bien", "value": "si"},
                            {"title": "✏️ No, la real es otra", "value": "no"},
                        ],
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_dir_alt_{fid}",
                        "placeholder": "Si dijiste 'No', escribí la dirección real",
                        "value": dir_real_guardada,
                    },
                    {
                        "type": "Input.Number",
                        "id": f"jose_pago_{fid}",
                        "label": "💰 Valor de envío (USD) — opcional",
                        "min": 0,
                        "placeholder": "Ej. 3.60 (se resta de tu caja chica)",
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_obs_{fid}",
                        "label": "📝 Observación — opcional",
                        "placeholder": "Ej. dejado en recepción, llamar antes…",
                        "value": obs_guardada,
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_razon_{fid}",
                        "label": "Si NO se pudo entregar, ¿por qué? — opcional",
                        "placeholder": "Ej. no llegaba ikopack, cerrado…",
                    },
                ])
                envios_items.append({
                    "type": "Container",
                    "style": box_style,
                    "spacing": "Medium",
                    "separator": True,
                    "items": envio_items,
                })
                envios_items.append({
                    "type": "ActionSet",
                    "actions": [
                        {
                            "type": "Action.Submit",
                            "title": "✅ Entregado",
                            "style": "positive",
                            "data": {
                                "intent": "jose_marcar_entrega",
                                "factura_id": fid,
                            },
                        },
                        {
                            "type": "Action.Submit",
                            "title": "❌ No entregado",
                            "style": "destructive",
                            "data": {
                                "intent": "jose_marcar_no_entregado",
                                "factura_id": fid,
                            },
                        },
                    ],
                })

        # Entregadas colapsadas (resumen compacto al final del bloque de envíos)
        if entregados_compactos:
            envios_items.append({
                "type": "Container",
                "style": "good",
                "spacing": "Medium",
                "separator": True,
                "items": [
                    {
                        "type": "TextBlock",
                        "text": f"✅ Ya entregadas hoy ({len(entregados_compactos)})",
                        "weight": "Bolder",
                        "color": "Good",
                        "size": "Small",
                    },
                    *entregados_compactos,
                ],
            })

    # Sub-bloque al final: añadir destino ad-hoc (cuando José tiene que ir a
    # un lugar no facturado: retiro, encargo extra, devolución, etc.)
    envios_items.extend([
        {
            "type": "TextBlock",
            "text": "➕ Añadir destino o entrega ad-hoc",
            "weight": "Bolder",
            "size": "Medium",
            "spacing": "Large",
            "separator": True,
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": (
                "Si tenés que ir a recoger algo, hacer un envío extra o "
                "cualquier destino que NO esté facturado en Contifico, "
                "agregalo acá."
            ),
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "jose_adhoc_cliente",
            "label": "Motivo",
            "placeholder": "Ej. retirar bobina, devolución cliente XX, encargo extra",
        },
        {
            "type": "Input.Text",
            "id": "jose_adhoc_direccion",
            "label": "Dirección",
            "placeholder": "Ej. Av. Las Américas N123 y Loja",
        },
        {
            "type": "Input.Text",
            "id": "jose_adhoc_obs",
            "label": "📝 Observación — a dónde fuiste / detalle",
            "isMultiline": True,
            "placeholder": "Ej. fui a la bodega del cliente, retiré 2 bobinas, dejé factura…",
        },
        {
            "type": "Input.Number",
            "id": "jose_adhoc_monto",
            "label": "💰 Valor de envío (USD) — opcional",
            "min": 0,
            "placeholder": "Si lo cobras de caja chica, monto",
        },
        {
            "type": "ActionSet",
            "actions": [{
                "type": "Action.Submit",
                "title": "➕ Agregar a la lista",
                "style": "positive",
                "data": {"intent": "jose_add_destino"},
            }],
        },
    ])

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": envios_items,
    })

    # ============ BLOQUE 3: Caja chica ============
    cc_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "💵 Caja chica",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]
    if cc.get("inicial") is None:
        # Primera vez — pedir saldo inicial
        cc_items.extend([
            {
                "type": "TextBlock",
                "text": (
                    "¿Con cuánto arrancás la caja chica? "
                    "Una sola vez se setea y queda. Después solo registrás gastos y reposiciones."
                ),
                "wrap": True,
                "isSubtle": True,
            },
            {
                "type": "Input.Number",
                "id": "jose_cc_inicial",
                "label": "Saldo inicial (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 20.00",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "💾 Guardar saldo inicial",
                    "style": "positive",
                    "data": {"intent": "jose_caja_inicial"},
                }],
            },
        ])
    else:
        saldo = cc.get("saldo", 0.0)
        # Phase V: alerta roja si ≤ $30
        if saldo <= CAJA_CHICA_ALERTA_ROJO:
            saldo_color = "Attention"  # rojo
            saldo_extra = f"  ⚠️ BAJO — pedí reposición"
        elif saldo <= CAJA_CHICA_ALERTA_ROJO * 2:
            saldo_color = "Warning"  # amarillo
            saldo_extra = ""
        else:
            saldo_color = "Good"  # verde
            saldo_extra = ""
        cc_items.append({
            "type": "TextBlock",
            "text": f"💰 Saldo actual: **${saldo:,.2f}**{saldo_extra}",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": saldo_color,
            "spacing": "Small",
        })
        cc_items.append({
            "type": "TextBlock",
            "text": (
                f"Inicial: ${cc.get('inicial') or 0:,.2f}  ·  "
                f"Movimientos: {len(cc.get('movimientos') or [])}"
            ),
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "spacing": "None",
        })

        # Movimientos de HOY (resumen rápido)
        movs_hoy = activity_state.caja_chica_movimientos_dia(email, hoy_str)
        if movs_hoy:
            res_items: list[dict[str, Any]] = []
            for m in movs_hoy[-5:]:  # últimos 5
                sign = "+" if m["tipo"] == "reposicion" else "-"
                color = "Good" if m["tipo"] == "reposicion" else "Attention"
                res_items.append({
                    "type": "TextBlock",
                    "text": (
                        f"{sign}${m['monto']:,.2f} — "
                        f"{m.get('descripcion') or m['tipo']}"
                    ),
                    "color": color,
                    "wrap": True,
                    "spacing": "None",
                })
            cc_items.append({
                "type": "Container",
                "items": [{
                    "type": "TextBlock",
                    "text": "Últimos movimientos de hoy:",
                    "weight": "Bolder",
                    "isSubtle": True,
                    "spacing": "Small",
                }] + res_items,
                "spacing": "Small",
            })

        # Registrar GASTO
        cc_items.extend([
            {
                "type": "TextBlock",
                "text": "➖ Registrar gasto",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
                "color": "Attention",
            },
            {
                "type": "Input.Text",
                "id": "jose_gasto_desc",
                "label": "¿En qué gastaste?",
                "placeholder": "Ej. Envío Reina del Paramo",
            },
            {
                "type": "Input.Number",
                "id": "jose_gasto_monto",
                "label": "Monto (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 3.60",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "➖ Registrar gasto",
                    "style": "destructive",
                    "data": {"intent": "jose_caja_gasto"},
                }],
            },
            {
                "type": "TextBlock",
                "text": "➕ Registrar reposición (cuando Daniel te da más efectivo)",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
                "color": "Good",
            },
            {
                "type": "Input.Number",
                "id": "jose_reposicion_monto",
                "label": "Monto recibido (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 50.00",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "➕ Sumar reposición",
                    "style": "positive",
                    "data": {"intent": "jose_caja_reposicion"},
                }],
            },
        ])

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": cc_items,
    })

    # Actividades diarias/semanales DELEGADAS por gerencia (2026-06-25): José
    # las ve y marca acá. Vacío si no tiene ninguna (no satura el card).
    _act_items = _jose_actividades_items(email)
    if _act_items:
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "ExtraLarge",
            "separator": True,
            "bleed": True,
            "items": _act_items,
        })

    # Asistencia / horario: ya NO va en el card de ruta (2026-06-23). José la
    # marca UNA sola vez al día en un card dedicado a las 17:10 (como Asistente 1
    # UIO/GYE) — ver send_jose_asistencia_card_job. Esto evita que el botón de
    # asistencia reaparezca en cada card de ruta y lo confunda.

    # Acciones principales del card: ACTUALIZAR + SALIDA/LLEGADA
    actions: list[dict[str, Any]] = [
        {
            "type": "Action.Submit",
            "title": "🔄 ACTUALIZAR LISTA",
            "data": {"intent": "jose_actualizar"},
        },
    ]
    if salida_abierta:
        actions.append({
            "type": "Action.Submit",
            "title": "🏁 LLEGADA (volví a la oficina)",
            "style": "destructive",
            "data": {"intent": "jose_end_ruta"},
        })
    else:
        actions.append({
            "type": "Action.Submit",
            "title": "▶️ SALIDA (voy a entregar)",
            "style": "positive",
            "data": {"intent": "jose_start_ruta"},
        })

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }

    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card,
    )
    return Activity(
        type=ActivityTypes.message,
        attachments=[attachment],
    )

def _build_jose_ruta_card_closed(user_email: str | None, fecha_str: str) -> Activity:
    """Card CONTRAÍDO y de solo lectura del día anterior (2026-06-23). Sin
    botones ni inputs: resume la jornada cerrada para que no se modifique y no
    se confunda con la del día en curso."""
    email = (user_email or JOSE_EMAIL).lower()
    try:
        consol = activity_state.get_entregas_consolidadas_dia(email, fecha_str) or {}
    except Exception:
        consol = {}
    try:
        fecha_lbl = date.fromisoformat(fecha_str).strftime("%A %d/%m/%Y")
    except Exception:
        fecha_lbl = fecha_str
    n_ok = sum(1 for e in consol.values() if e.get("status") == "entregado")
    n_no = sum(1 for e in consol.values() if e.get("status") == "no_entregado")
    n_pend = sum(1 for e in consol.values() if e.get("status") == "pendiente")

    lineas: list[dict[str, Any]] = []
    for _fid, e in sorted(consol.items(), key=lambda kv: kv[1].get("cliente", "")):
        st = e.get("status", "pendiente")
        ic = {"entregado": "✅", "no_entregado": "❌"}.get(st, "⏳")
        extra = ""
        if st == "no_entregado" and e.get("razon_no_entrega"):
            extra = f" — {e['razon_no_entrega']}"
        lineas.append({
            "type": "TextBlock",
            "text": f"{ic} {e.get('cliente', '?')}{extra}",
            "wrap": True,
            "size": "Small",
            "spacing": "None",
            "isSubtle": True,
        })

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"🗓️ Ruta del {fecha_lbl} — CERRADA",
            "size": "Large",
            "weight": "Bolder",
            "wrap": True,
            "color": "Default",
        },
        {
            "type": "TextBlock",
            "text": (
                f"✅ {n_ok} entregadas · ❌ {n_no} no entregadas · ⏳ {n_pend} quedaron pendientes\n"
                "_(Cerrado — los pendientes pasaron al card de hoy.)_"
            ),
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]
    if lineas:
        body.append({"type": "Container", "items": lineas, "spacing": "Small"})

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        # sin actions → no se puede modificar
    }
    return Activity(
        type=ActivityTypes.message,
        attachments=[Attachment(
            content_type="application/vnd.microsoft.card.adaptive",
            content=card,
        )],
    )

def _build_jose_asistencia_card(user_email: str | None = None) -> Activity:
    """Card dedicado de asistencia para José (2026-06-23). Se envía UNA vez al
    día a las 17:10 (como el check-in de Asistente 1 UIO/GYE), en lugar de tener
    el botón de asistencia repetido en cada card de ruta."""
    hoy = activity_state._today()
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "🚚 José — registro de asistencia",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": "Marcá tu jornada de hoy (una sola vez al día).",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
        {"type": "Container", "items": _horario_card_items(hoy)},
    ]
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{
            "type": "Action.Submit",
            "title": "💾 Guardar asistencia",
            "data": {"intent": "jose_asistencia"},
        }],
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])

def _build_apertura_caja_card(user_email: str) -> Activity:
    """Phase S+ (2026-06-09): card matinal 8:15 AM con resumen de actividades
    del día (recordatorio informativo, sin inputs ni submit). Para info@, quito@
    y gsanchez@.
    """
    fecha_humana = datetime.now(activity_state.LOCAL_TZ).strftime("%A %d/%m/%Y")

    # Activities del día
    wk = activity_state.get_week(user_email)
    diarias = [(aid, a) for aid, a in wk["activities"].items()
               if a["tipo"] == "diaria"]
    cobranzas = [(aid, a) for aid, a in diarias if aid.startswith("cobranza-")]
    otras_diarias = [(aid, a) for aid, a in diarias if not aid.startswith("cobranza-")]
    semanales = [(aid, a) for aid, a in wk["activities"].items()
                 if a["tipo"] != "diaria"]

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "☀️ Buen día — tu agenda de hoy",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": fecha_humana.capitalize(),
            "spacing": "None",
            "isSubtle": True,
        },
    ]

    # Phase S+ (2026-06-09): cada sección en su Container para separación visual
    # Cobranzas (si tiene)
    if cobranzas:
        cob_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": f"📞 {len(cobranzas)} cobranzas para contactar",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in cobranzas:
            nombre = a.get("nombre", "").replace("📞 Cobranza:", "").strip()
            cob_items.append({
                "type": "TextBlock",
                "text": f"• {nombre}",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": cob_items,
        })

    # Otras actividades diarias
    if otras_diarias:
        od_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📅 Actividades diarias",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in otras_diarias:
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            meta = a.get("meta")
            unidad = a.get("unidad", "")
            meta_txt = f" (meta {meta} {unidad})" if meta else ""
            od_items.append({
                "type": "TextBlock",
                "text": f"• {prio_badge}{a.get('nombre', aid)}{meta_txt}",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": od_items,
        })

    # Proyectos semanales (resumen breve)
    if semanales:
        sem_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📌 Proyectos semanales en curso",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in semanales:
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            avance = a.get("avance", 0) or 0
            sem_items.append({
                "type": "TextBlock",
                "text": f"• {prio_badge}{a.get('nombre', aid)} — {avance:.0f}%",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": sem_items,
        })

    if not cobranzas and not otras_diarias and not semanales:
        body.append({
            "type": "TextBlock",
            "text": "Hoy no tenés actividades asignadas todavía. ¡Buen día!",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Medium",
        })

    # Footer
    body.append({
        "type": "TextBlock",
        "text": (
            "Tu check-in con el detalle de lo hecho lo hacés a la tarde "
            "(4:30 PM Mateo/Gabriela · 5:00 PM Gladys/Gabriela Bravo). "
            "¡Buen día y mucha suerte!"
        ),
        "isSubtle": True,
        "wrap": True,
        "spacing": "Large",
        "size": "Small",
    })

    card_json = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])
