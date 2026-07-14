"""Preflight de la corrida diaria — M1 (decisión del board 2026-07-13).

El incidente "npm ci faltante" no puede repetirse: antes de gastar un centavo,
la corrida valida su entorno y AUTO-REPARA lo que es seguro reparar:

- Node/npx disponibles (agrega `tools/node-*` al PATH si hace falta).
- `marketing/render/node_modules` presente → si falta, **npm ci automático**
  (el fix del 2026-07-13, ahora sin humanos).
- Env vars requeridas por cada servicio real.
- Espacio en disco mínimo.
- Archivos del tenant (marketing.yaml, contexto de marca, pilares activos).

Devuelve la lista de problemas BLOQUEANTES restantes (vacía = despegar).
El CLI de la corrida aborta con alerta si hay bloqueantes — falla barato y
avisando, nunca a mitad del render.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("marketing.preflight")

_REPO_ROOT = Path(__file__).parent.parent
RENDER_DIR = _REPO_ROOT / "marketing" / "render"
MIN_DISK_GB = 2.0
REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "guionista/gate (Claude)",
    "AZURE_SPEECH_KEY": "voz TTS",
    "AZURE_SPEECH_REGION": "voz TTS",
    "PEXELS_API_KEY": "b-roll",
}
NOTIFY_ENV_ALTERNATIVES = (
    ("MICROSOFT_APP_ID", "GRAPH_CLIENT_ID"),
    ("MICROSOFT_APP_PASSWORD", "GRAPH_CLIENT_SECRET"),
    ("MICROSOFT_APP_TENANT_ID", "GRAPH_TENANT_ID"),
)


def _ensure_node_on_path() -> bool:
    if shutil.which("npx") or shutil.which("npx.cmd"):
        return True
    tools = Path.home() / "tools"
    if tools.exists():
        for d in sorted(tools.glob("node-*")):
            if (d / "npx.cmd").exists() or (d / "npx").exists():
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(d)
                logger.info("preflight: node agregado al PATH desde %s", d)
                return True
    return False


def _ensure_render_deps(auto_fix: bool, npm_runner=None) -> str | None:
    """None = OK; str = problema bloqueante."""
    marker = RENDER_DIR / "node_modules" / "remotion"
    if marker.exists():
        return None
    if not auto_fix:
        return f"faltan dependencias de render ({marker} no existe) y auto_fix=False"
    logger.warning("preflight: node_modules ausente — ejecutando npm ci automático")

    def _default_npm(cwd: Path):
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            raise RuntimeError("npm no está en el PATH")
        result = subprocess.run(
            [npm, "ci"], cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(f"npm ci exit {result.returncode}: {result.stderr[-500:]}")

    try:
        (npm_runner or _default_npm)(RENDER_DIR)
    except Exception as exc:
        return f"npm ci automático falló: {exc}"
    if not marker.exists():
        return "npm ci corrió pero node_modules/remotion sigue ausente"
    logger.info("preflight: dependencias de render instaladas automáticamente")
    return None


def preflight(tenant_slug: str, *, auto_fix: bool = True, npm_runner=None) -> list[str]:
    problems: list[str] = []

    if not _ensure_node_on_path():
        problems.append("Node/npx no disponibles (ni en PATH ni en ~/tools/node-*)")
    else:
        dep = _ensure_render_deps(auto_fix, npm_runner)
        if dep:
            problems.append(dep)

    for var, uso in REQUIRED_ENV.items():
        if not os.environ.get(var, "").strip():
            problems.append(f"falta env var {var} ({uso})")
    for alternativas in NOTIFY_ENV_ALTERNATIVES:
        if not any(os.environ.get(v, "").strip() for v in alternativas):
            problems.append(
                f"faltan credenciales de correo ({' o '.join(alternativas)}) — "
                "sin ellas no hay resumen ni ALERTAS (bloqueante por regla #26)"
            )

    total, _, free = shutil.disk_usage(Path.home())
    if free / 1e9 < MIN_DISK_GB:
        problems.append(f"disco insuficiente: {free / 1e9:.1f} GB libres < {MIN_DISK_GB} GB")

    try:
        from marketing.brand import load_brand_context, load_tts_voice
        from marketing.pillars import active_pillars

        load_brand_context(tenant_slug)
        load_tts_voice(tenant_slug)
        if not active_pillars(tenant_slug):
            problems.append(f"el tenant {tenant_slug} no tiene pilares activos")
    except Exception as exc:
        problems.append(f"configuración del tenant inválida: {exc}")

    if problems:
        logger.error("preflight BLOQUEADO: %s", "; ".join(problems))
    else:
        logger.info("preflight OK")
    return problems
