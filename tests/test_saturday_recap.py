"""Tests del recap del sábado y horario reducido de sábados (2026-06-15).

Cubre:
  - horario estándar dependiente del día (sáb 9–13, resto 8:30–17:30)
  - cálculo del último sábado para el recap del lunes
  - título e intro del recap del sábado vs consolidado normal
  - rotación GYE: José/asistente sin reporte el sábado = ausencia esperada
  - observaciones de José propagadas al resumen (fix get_entregas_consolidadas_dia)
"""
from __future__ import annotations

import importlib
from datetime import date


def _reload_ask_agent():
    import ask_agent
    return importlib.reload(ask_agent)


# ---------- Horario estándar por día ----------

def test_horario_estandar_depende_del_dia(state_env):
    a = state_env.activity_state
    # 2026-06-13 sábado · 2026-06-15 lunes
    assert a.es_sabado("2026-06-13") is True
    assert a.es_sabado("2026-06-15") is False
    assert a.horario_estandar("2026-06-13") == ("9:00 AM", "1:00 PM")
    assert a.horario_estandar("2026-06-15") == ("8:30 AM", "5:30 PM")
    assert a.horario_estandar_label("2026-06-13") == "9:00 AM – 1:00 PM"
    assert a.horario_estandar_corto("2026-06-13") == "9:00–13:00"
    assert a.horario_estandar_corto("2026-06-15") == "8:30–17:30"


# ---------- Último sábado (recap del lunes) ----------

def test_ultimo_sabado(state_env):
    ask = _reload_ask_agent()
    assert ask._ultimo_sabado(date(2026, 6, 15)) == date(2026, 6, 13)  # lunes
    assert ask._ultimo_sabado(date(2026, 6, 16)) == date(2026, 6, 13)  # martes
    assert ask._ultimo_sabado(date(2026, 6, 13)) == date(2026, 6, 13)  # sábado
    assert ask._ultimo_sabado(date(2026, 6, 14)) == date(2026, 6, 13)  # domingo


# ---------- Título del recap vs consolidado normal ----------

def test_consolidado_normal_titulo_sin_cambios(state_env):
    ask = _reload_ask_agent()
    html = ask._consolidated_daily_summary_html([])  # target_date=None
    assert "Resumen diario del equipo" in html
    assert "Resumen del sábado" not in html


def test_recap_sabado_titulo(state_env):
    ask = _reload_ask_agent()
    html = ask._consolidated_daily_summary_html([], target_date=date(2026, 6, 13))
    assert "Resumen del sábado" in html


# ---------- Rotación GYE: ausencia esperada ----------

def test_jose_sabado_sin_data_es_ausencia_esperada(state_env):
    ask = _reload_ask_agent()
    html = ask._jose_consolidated_block_html("2026-06-13")  # sábado, sin data
    assert "ASISTENTE 2 GYE" in html
    assert "Ausencia esperada" in html


def test_jose_viernes_sin_data_no_es_ausencia(state_env):
    ask = _reload_ask_agent()
    html = ask._jose_consolidated_block_html("2026-06-12")  # viernes
    # En día laboral, sin data NO se marca ausencia rotativa
    assert "Ausencia esperada" not in html


# ---------- Observaciones de José en el resumen ----------

def test_observaciones_de_jose_aparecen_en_resumen(state_env):
    a = state_env.activity_state
    ask = _reload_ask_agent()
    jose = ask.JOSE_EMAIL_CONS
    fecha = "2026-06-12"  # viernes — evita la rama de ausencia de sábado

    st = a.load()
    user = a._get_user_state(st, jose)
    user["rutas"] = {
        fecha: {
            "envios_snapshot": {
                "F-1": {
                    "cliente": "ACME S.A.",
                    "documento": "001-002-000123",
                    "total": 50.0,
                    "fecha_emision": fecha,
                }
            },
            "salidas": [
                {
                    "inicio_ts": f"{fecha}T09:00:00-05:00",
                    "fin_ts": f"{fecha}T10:00:00-05:00",
                    "entregas": {
                        "F-1": {
                            "status": "entregado",
                            "ts": f"{fecha}T09:30:00-05:00",
                            "observacion": "Cliente pidió factura física",
                        }
                    },
                }
            ],
        }
    }
    a.save(st)

    # 1) la observación se propaga en el consolidado de entregas (fix raíz)
    consol = a.get_entregas_consolidadas_dia(jose, fecha)
    assert consol["F-1"]["observacion"] == "Cliente pidió factura física"

    # 2) el bloque de José la muestra en su sección dedicada
    html = ask._jose_consolidated_block_html(fecha)
    assert "Observaciones de José" in html
    assert "Cliente pidió factura física" in html
    assert "ACME S.A." in html


def test_jose_sin_observaciones_muestra_seccion_vacia(state_env):
    ask = _reload_ask_agent()
    html = ask._jose_consolidated_block_html("2026-06-12")  # viernes, sin data
    assert "sin observaciones registradas" in html
