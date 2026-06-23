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
    # infraestructura (Fases 1-5)
    "safe_json.py",
    "send_ledger.py",
    "core_config.py",
    # state y dominio
    "activity_state.py",
    "conversation_history.py",
    "reminders.py",
    "dispatch_state.py",
    "reply_state.py",
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
