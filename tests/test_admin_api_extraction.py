"""Tests F4.4b (VER-IA 2026-07-04): extracción de admin_api desde teams_bot.

Contrato:
- Los 39 endpoints /admin/* quedan montados en la app (include_router al
  final de teams_bot — sin ciclos porque a esa altura el módulo está completo).
- teams_bot ya no define rutas /admin propias.
- teams_bot sigue bajando de tamaño.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_los_39_endpoints_admin_siguen_montados():
    import teams_bot
    rutas_admin = {
        r.path for r in teams_bot.app.routes
        if getattr(r, "path", "").startswith("/admin")
    }
    assert len(rutas_admin) == 39, sorted(rutas_admin)
    # Muestras representativas de cada familia
    for path in ("/admin/trigger-checkin", "/admin/llm-usage",
                 "/admin/schedule-one-time-email", "/admin/wipe-user-from-activities",
                 "/admin/trigger-reply-agent", "/admin/state-debug"):
        assert path in rutas_admin, path


def test_teams_bot_no_define_rutas_admin():
    """Las rutas admin viven SOLO en admin_api — un @app.post("/admin/...")
    nuevo en teams_bot es el módulo equivocado."""
    tree = ast.parse((ROOT / "teams_bot.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith("/admin")
        ):
            raise AssertionError(
                f"teams_bot.py:{node.lineno} define una ruta /admin — va en admin_api.py"
            )


def test_health_y_bots_siguen_en_teams_bot():
    """Lo que NO es admin se queda en el entrypoint."""
    import teams_bot
    paths = {getattr(r, "path", "") for r in teams_bot.app.routes}
    for p in ("/", "/health", "/health/deliveries",
              "/api/messages", "/api/activities/messages"):
        assert p in paths, p


def test_teams_bot_sigue_bajando():
    lineas = len((ROOT / "teams_bot.py").read_text(encoding="utf-8").splitlines())
    assert lineas < 4300, f"teams_bot.py tiene {lineas} líneas"
