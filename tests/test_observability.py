"""Tests observabilidad (H1) + sincronía de requirements.

- App Insights se activa SOLO con la connection string y jamás rompe el
  arranque del bot.
- Gate anti-drift de dependencias: TODO paquete runtime de
  requirements.txt (el que viaja en el zip de deploy y Oryx instala) debe
  estar también en requirements_bot.txt (dev/CI) — y al revés para los
  paquetes que el bot importa. Incidente latente detectado 2026-07-04:
  azure-data-tables se agregó solo a requirements_bot y produción funcionó
  de casualidad porque la raíz ya lo tenía.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _paquetes(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        nombre = re.split(r"[><=\[;]", line)[0].strip().lower()
        if nombre:
            out.add(nombre)
    return out


# ---------- App Insights ----------

def test_sin_connection_string_no_hace_nada(monkeypatch):
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    import teams_bot
    assert teams_bot._init_app_insights() is False


def test_fallo_de_paquete_no_rompe_el_arranque(monkeypatch):
    """Con la env seteada pero el SDK roto/ausente, devuelve False con un
    warning — el bot arranca igual con logging local."""
    monkeypatch.setenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "InstrumentationKey=00000000-0000-0000-0000-000000000000",
    )
    import teams_bot
    monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", None)
    assert teams_bot._init_app_insights() is False  # no lanza


# ---------- Sincronía de requirements ----------

def test_requirements_de_deploy_es_subconjunto_de_dev():
    """Todo lo que Oryx instala en producción debe existir también en el
    entorno de dev/CI (que corre los tests contra ese código)."""
    deploy = _paquetes(ROOT / "requirements.txt")
    dev = _paquetes(ROOT / "requirements_bot.txt")
    faltan_en_dev = deploy - dev
    assert not faltan_en_dev, (
        f"paquetes en requirements.txt (deploy) ausentes de "
        f"requirements_bot.txt (dev/CI): {sorted(faltan_en_dev)}"
    )


def test_runtime_criticos_en_ambos():
    """Los paquetes que el código del bot importa en runtime deben estar en
    AMBOS archivos — agregar a uno solo es el incidente latente de F4.3."""
    criticos = {
        "fastapi", "gunicorn", "botbuilder-core", "anthropic", "httpx",
        "msal", "apscheduler", "pytz", "matplotlib", "azure-data-tables",
        "pyyaml", "azure-monitor-opentelemetry",
    }
    deploy = _paquetes(ROOT / "requirements.txt")
    dev = _paquetes(ROOT / "requirements_bot.txt")
    assert criticos <= deploy, sorted(criticos - deploy)
    assert criticos <= dev, sorted(criticos - dev)
