"""build_bot_package — genera el zip de deploy del Teams bot (App Service).

Fase 4 del refactor. Reemplaza el flujo manual de copiar archivos a
`bot_deploy_stage\\` (que quedaba desactualizado y era OTRA fuente de drift).
El zip se genera SIEMPRE desde la raíz (fuente única).

Uso:
    python tools/build_bot_package.py            # genera bot_deploy.zip
    python tools/build_bot_package.py --out x.zip

Deploy (desde PowerShell con az login):
    az webapp deploy -g rg-biodegradables-prod -n biodegradables-bot-app `
        --src-path bot_deploy.zip --type zip

Rollback: re-deployar el zip anterior (guardar el actual antes).
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Todo lo que el App Service necesita para correr teams_bot.py
BOT_FILES = [
    # entrypoint + agentes
    "teams_bot.py",
    "ask_agent.py",
    "team_reports.py",
    "bot_cards.py",
    "tenant_roles.py",
    "admin_api.py",
    # infraestructura (Fases 1-5 + F3 VER-IA)
    "safe_json.py",
    "send_ledger.py",
    "core_config.py",
    "llm_usage.py",
    # state y dominio
    "activity_state.py",
    "cobranzas_sync.py",
    "conversation_history.py",
    "reminders.py",
    "dispatch_state.py",
    "reply_state.py",
    "marketing_l0_state.py",
    "tiktok_connector.py",
    # clientes
    "contifico_client.py",
    "credito_excel.py",
    "hubspot_client.py",
    "graph_mail.py",
    "graph_calendar_app.py",
    "pbi_cloud.py",
    "apollo_rest.py",
    # reportes que corren dentro del bot
    "daily_report.py",
    "daily_logistics_report.py",
    "monthly_recap.py",
    "news_brief.py",
    "forecasting.py",
    # prospección outbound (F4.3: migrada de azfunc/PC al bot)
    # (apollo_completion_notifier retirado 2026-07-04 → archive/)
    "reply_agent.py",
    "outlook_client.py",
    # demo (Fase 5: DEMO_MODE=1 en el App Service sirve datos sintéticos;
    # sin estos archivos el flag caía en fail-soft a datos REALES)
    "demo_contifico.py",
    "demo_hubspot.py",
    "demo_seed.py",
    # config / templates
    "requirements.txt",
    "company_context.md",
    "condiciones_credito.json",
    "activities_template.json",
    "activities_template_gsanchez.json",
    "activities_template_info.json",
    "activities_template_quito.json",
]

# Directorios del paquete multiempresa. Solo se USAN si TENANT_CONFIG_SOURCE=yaml,
# pero deben viajar en el zip para que el flag se pueda prender en producción sin
# que el import perezoso de core_config falle (ModuleNotFoundError: core).
BOT_DIRS = ["core", "connectors", "tenants"]


def _iter_dir_files(base: Path):
    """Todos los archivos de `base`, recursivo, salvo caches de Python."""
    for p in sorted(base.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            yield p


# Módulos raíz que un módulo empaquetado puede importar SIN viajar en el zip.
# Solo entra aquí un import que sea genuinamente opcional en el App Service
# (guardado por flag/try-except) — justificar cada entrada.
IMPORT_ALLOWLIST: set[str] = set()


def _check_imports() -> list[str]:
    """Gate F4 (VER-IA 2026-07-03): ningún módulo empaquetado puede importar
    un módulo RAÍZ que no viaje en el zip. Detecta imports top-level Y lazy
    (dentro de funciones) vía AST — el incidente del 2026-07-03 fue exactamente
    esto: ask_agent ganó `import llm_usage` y el empaquetador no lo supo.
    Devuelve lista de errores 'modulo <- importador'."""
    import ast
    root_modules = {p.stem for p in ROOT.glob("*.py")}
    packaged_py = [n for n in BOT_FILES if n.endswith(".py")]
    packaged_mods = {Path(n).stem for n in packaged_py}
    # Los paquetes (core/, connectors/) viajan como directorios completos.
    packaged_pkgs = set(BOT_DIRS)
    errores: list[str] = []
    for name in packaged_py:
        try:
            tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        except SyntaxError as e:
            errores.append(f"{name}: no parsea ({e})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            else:
                continue
            for m in mods:
                if (
                    m in root_modules
                    and m not in packaged_mods
                    and m not in packaged_pkgs
                    and m not in IMPORT_ALLOWLIST
                ):
                    errores.append(f"{m}.py <- importado por {name} (agregar a BOT_FILES)")
    return sorted(set(errores))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="bot_deploy.zip")
    args = parser.parse_args()

    missing = [f for f in BOT_FILES if not (ROOT / f).exists()]
    missing += [d for d in BOT_DIRS if not (ROOT / d).is_dir()]
    if missing:
        print(f"ERROR: faltan archivos/directorios: {missing}", file=sys.stderr)
        return 1

    import_errs = _check_imports()
    if import_errs:
        print("ERROR: el paquete importa módulos raíz que NO viajan en el zip "
              "(ModuleNotFoundError en producción):", file=sys.stderr)
        for e in import_errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    out = ROOT / args.out
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in BOT_FILES:
            zf.write(ROOT / name, arcname=name)
            count += 1
        for d in BOT_DIRS:
            for p in _iter_dir_files(ROOT / d):
                zf.write(p, arcname=p.relative_to(ROOT).as_posix())
                count += 1

    print(f"OK: {out.name} generado con {count} archivos.")
    print("Deploy: az webapp deploy -g rg-biodegradables-prod "
          "-n biodegradables-bot-app --src-path " + args.out + " --type zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
