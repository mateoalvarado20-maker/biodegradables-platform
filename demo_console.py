r"""demo_console — "control remoto" de la demo comercial (Fase 4).

Renderiza cada artefacto del producto (reporte comercial, logística, resumen del
equipo, recap mensual) a archivos HTML que se abren en el navegador durante la
presentación, y deja consultar el Data Bot por CLI. NO necesita servidor, Azure
ni enviar correos: todo local, con los datos sintéticos de Andex.

Cada artefacto se escanea con demo_guard ANTES de escribirse: si por algún motivo
apareciera un dato real, aborta (defensa en profundidad sobre el sandbox).

Requisitos (los setea el operador, no este script):
    set DEMO_MODE=1
    set TENANT_CONFIG_SOURCE=yaml
    set TENANT_SLUG=andex
    set DEMO_EMAIL_DOMAIN=andexdemo.com
    set STATE_DIR=%USERPROFILE%\.andex-demo        (estado del equipo, dedicado)
    set DEMO_TODAY=2026-06-24                       (opcional, fija "hoy")
    set ANTHROPIC_API_KEY=...                       (solo para `databot`)

Uso:
    python demo_console.py all                 # siembra + renderiza todo + index + demo.html
    python demo_console.py site                # igual que `all` (sitio + demo.html 1-archivo)
    python demo_console.py comercial           # solo el reporte comercial
    python demo_console.py logistica
    python demo_console.py equipo
    python demo_console.py recap
    python demo_console.py seed                # siembra el estado del equipo
    python demo_console.py databot "cuánto vendimos ayer?"
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import demo_guard

_EC_TZ = timezone(timedelta(hours=-5))
OUT_DIR = os.environ.get("DEMO_OUT", "demo_out")


def _today() -> date:
    override = os.environ.get("DEMO_TODAY")
    if override:
        return date.fromisoformat(override.strip())
    return datetime.now(_EC_TZ).date()


def _write(name: str, html: str, titulo: str) -> str:
    """Escanea y escribe un artefacto HTML. Devuelve la ruta."""
    demo_guard.assert_no_real_data(html, context=name)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [OK] {titulo} → {path}")
    return path


def _team_emails() -> list[str]:
    """Colaboradores no-supervisor y no-chofer (el chofer se intercala solo)."""
    import core_config
    return [
        e for e, p in core_config.PEOPLE.items()
        if not p.get("supervisor") and p.get("role") != "chofer"
    ]


def render_comercial() -> str:
    import daily_report
    return _write("comercial.html", daily_report.html_morning(),
                  "Reporte comercial 8 AM")


def render_logistica() -> str:
    import daily_logistics_report as logi
    hoy = _today()
    desde = hoy - timedelta(days=logi.DIAS_DESDE)
    hasta = hoy - timedelta(days=logi.DIAS_HASTA)
    envios = logi.build_envios(desde, hasta)
    html = logi._render_html(envios, desde, hasta)
    return _write("logistica.html", html, f"Reporte de logística ({len(envios)} envíos)")


def render_equipo() -> str:
    import ask_agent
    html = ask_agent._consolidated_daily_summary_html(_team_emails(), target_date=_today())
    return _write("equipo.html", html, "Resumen diario del equipo")


def render_recap() -> str:
    import monthly_recap
    hoy = _today()
    year, month = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    ventas = monthly_recap._build_sales_recap_html(year, month)
    try:
        actividades = monthly_recap._build_activities_recap_html(year, month)
    except Exception as e:  # noqa: BLE001
        actividades = f"<p>(recap de actividades no disponible: {e})</p>"
    html = (
        "<html><body style='font-family:Segoe UI,Arial,sans-serif;'>"
        + ventas + "<hr style='margin:32px 0;'/>" + actividades + "</body></html>"
    )
    return _write("recap.html", html, f"Recap mensual {month:02d}/{year}")


def _collect_html() -> list[tuple[str, str, str]]:
    """Devuelve [(slug, titulo, html)] de los 4 artefactos, sin escribir archivos."""
    import ask_agent
    import daily_logistics_report as logi
    import daily_report
    import monthly_recap
    hoy = _today()
    desde = hoy - timedelta(days=logi.DIAS_DESDE)
    hasta = hoy - timedelta(days=logi.DIAS_HASTA)
    envios = logi.build_envios(desde, hasta)
    year, month = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    try:
        recap_act = monthly_recap._build_activities_recap_html(year, month)
    except Exception as e:  # noqa: BLE001
        recap_act = f"<p>(recap de actividades no disponible: {e})</p>"
    recap = (monthly_recap._build_sales_recap_html(year, month)
             + "<hr style='margin:32px 0;'/>" + recap_act)
    return [
        ("comercial", "📊 Reporte comercial 8 AM", daily_report.html_morning()),
        ("logistica", "🚚 Logística", logi._render_html(envios, desde, hasta)),
        ("equipo", "👥 Resumen del equipo",
         ask_agent._consolidated_daily_summary_html(_team_emails(), target_date=hoy)),
        ("recap", "📅 Recap mensual", recap),
    ]


def build_single_file() -> str:
    """Genera UN solo `demo.html` autocontenido con los 4 reportes APILADOS en una
    sola página scrolleable (no tabs): así se ven todos los ejemplos de corrido.
    Menú sticky arriba para saltar a cada sección. Cada reporte va en un iframe
    srcdoc que se auto-ajusta a su alto. Ideal para compartir por link."""
    from html import escape
    import core_config
    arts = _collect_html()
    for _slug, _t, html in arts:
        demo_guard.assert_no_real_data(html, context="single-file")
    nav = "".join(f'<a href="#{slug}">{escape(t)}</a>' for slug, t, _h in arts)
    sections = "".join(
        f'<section id="{slug}"><h2 class="sec">{escape(t)}</h2>'
        f'<iframe class="rep" srcdoc="{escape(html, quote=True)}" '
        # auto-ajusta el alto del iframe a su contenido (srcdoc = same-origin)
        f'onload="this.style.height=(this.contentWindow.document.body.scrollHeight+40)+&#39;px&#39;">'
        f'</iframe></section>'
        for slug, t, html in arts
    )
    page = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo — {escape(core_config.COMPANY_NAME)}</title>
<style>
  body{{margin:0;font-family:'Segoe UI',Arial,sans-serif;background:#f1f5f9;color:#0f172a;}}
  header{{background:#0B6E99;color:#fff;padding:16px 22px;}}
  header h1{{margin:0;font-size:20px;}} header p{{margin:4px 0 0;opacity:.85;font-size:13px;}}
  .nav{{display:flex;gap:6px;flex-wrap:wrap;padding:10px 22px;background:#fff;border-bottom:1px solid #e2e8f0;position:sticky;top:0;z-index:10;}}
  .nav a{{text-decoration:none;color:#0B6E99;border:1px solid #cbd5e1;background:#f8fafc;border-radius:8px;padding:6px 12px;font-size:13px;}}
  .nav a:hover{{background:#0B6E99;color:#fff;border-color:#0B6E99;}}
  section{{max-width:980px;margin:22px auto;background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);scroll-margin-top:64px;}}
  h2.sec{{margin:0;padding:12px 18px;background:#eef6fb;color:#0B6E99;border-bottom:1px solid #e2e8f0;font-size:17px;}}
  .rep{{display:block;width:100%;min-height:300px;border:0;background:#fff;}}
  footer{{padding:14px 22px;font-size:12px;color:#64748b;text-align:center;}}
</style></head><body>
<header><h1>Demo — {escape(core_config.COMPANY_NAME)}</h1>
<p>{escape(core_config.COMPANY_SECTOR.capitalize())} · sucursales en {escape(core_config.COMPANY_SUCURSALES_DESC)} · datos 100% ficticios</p></header>
<div class="nav">{nav}</div>
{sections}
<footer>Entorno de demostración — no contiene datos de ningún cliente real.</footer>
</body></html>"""
    return _write("demo.html", page, "Demo en un solo archivo (reportes apilados)")


def run_databot(pregunta: str) -> None:
    import ask_agent
    print(f"\n  Pregunta: {pregunta}")
    try:
        resp = ask_agent.ask(pregunta, mode="data")
    except Exception as e:  # noqa: BLE001
        print(f"  [ERROR] {e}\n  (¿falta ANTHROPIC_API_KEY?)", file=sys.stderr)
        return
    hits = demo_guard.scan_for_real_data(resp)
    if hits:
        print(f"  [⚠️ FUGA] la respuesta menciona datos reales: {hits}", file=sys.stderr)
    print("\n" + resp + "\n")


def run_seed() -> None:
    import seed_demo_state
    seed_demo_state.seed_activities(_today())
    n = seed_demo_state.seed_dispatch(_today())
    print(f"  [OK] estado del equipo sembrado ({n} despachos)")


def write_index(paths: dict[str, str]) -> str:
    import core_config
    links = "".join(
        f'<li><a href="{os.path.basename(p)}">{titulo}</a></li>'
        for titulo, p in paths.items()
    )
    html = f"""<html><head><meta charset="utf-8"><title>Demo {core_config.COMPANY_NAME}</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:40px auto;color:#1f2937;">
<h1 style="color:{core_config.SUCURSAL_NAMES and '#0B6E99'};">Demo — {core_config.COMPANY_NAME}</h1>
<p>{core_config.COMPANY_SECTOR.capitalize()} · sucursales en {core_config.COMPANY_SUCURSALES_DESC}.</p>
<p>Artefactos generados (datos 100% ficticios):</p>
<ul style="line-height:2;font-size:16px;">{links}</ul>
<p style="color:#6b7280;font-size:13px;">Generado por demo_console.py — entorno DEMO, sin datos de ningún cliente real.</p>
</body></html>"""
    return _write("index.html", html, "Índice de la demo")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # Falla cerrado si el entorno no es un demo válido.
    demo_guard.verify_demo_config()
    if os.environ.get("DEMO_MODE") != "1":
        print("[demo_console] DEMO_MODE != 1 — abortando.", file=sys.stderr)
        return 2

    cmd = sys.argv[1] if len(sys.argv) >= 2 else "all"
    print(f"== demo_console: {cmd} ==")

    if cmd == "comercial":
        render_comercial()
    elif cmd == "logistica":
        render_logistica()
    elif cmd == "equipo":
        render_equipo()
    elif cmd == "recap":
        render_recap()
    elif cmd == "seed":
        run_seed()
    elif cmd == "databot":
        run_databot(" ".join(sys.argv[2:]) or "¿cuánto vendimos ayer?")
    elif cmd in ("all", "site"):
        run_seed()
        paths = {
            "📊 Reporte comercial 8 AM": render_comercial(),
            "🚚 Reporte de logística": render_logistica(),
            "👥 Resumen diario del equipo": render_equipo(),
            "📅 Recap mensual": render_recap(),
        }
        idx = write_index(paths)
        single = build_single_file()
        print(f"\nSitio (multi-archivo): {os.path.abspath(idx)}")
        print(f"Para compartir por LINK (1 archivo): {os.path.abspath(single)}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
