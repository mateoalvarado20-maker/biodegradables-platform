"""Notificador de secuencias Apollo completadas.

Cuando una secuencia activa llega a `unique_scheduled == 0` (terminó de mandar
todos los correos pendientes de step 1), envía un correo a Mateo con las stats
finales y una sugerencia accionable para reabrir el ciclo agregando contactos.

Estado: persiste por secuencia el `unique_delivered` al momento de notificar,
para evitar duplicados pero permitir re-notificar cuando se agregan más
contactos y la secuencia vuelve a quedar en `scheduled=0` con más envíos.

Uso:
    python apollo_completion_notifier.py             # tick normal (lo que Task Scheduler corre)
    python apollo_completion_notifier.py --dry-run   # mostrar a quién notificaría sin enviar
    python apollo_completion_notifier.py --status    # estado de cada secuencia activa
    python apollo_completion_notifier.py --reset ID  # borrar state de una secuencia (re-notificar)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import apollo_rest
import core_config
from pbi_cloud import send_email

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador UTC-5
# F2.4: destinatario y marca desde core_config (antes hardcodeados).
NOTIFY_TO = os.environ.get("APOLLO_NOTIFY_TO", core_config.MIO).strip()
STATE_PATH = Path.home() / ".claude-agent" / "apollo_completion_state.json"
CONTEXT_PATH = Path(__file__).parent / "company_context.md"
CLAUDE_MODEL = "claude-sonnet-4-6"


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def safe_int(v) -> int:
    return v if isinstance(v, int) else 0


def _load_company_context() -> str:
    """Carga company_context.md para dar contexto al modelo. Si falta el
    archivo, devuelve string vacío (la sugerencia será más genérica)."""
    if not CONTEXT_PATH.exists():
        return ""
    text = CONTEXT_PATH.read_text(encoding="utf-8")
    # Truncar a ~3000 chars para no quemar tokens — la primera mitad
    # del archivo (identidad, catálogo, ICP) es lo más relevante.
    return text[:3000]


def suggest_apollo_filters(seq_name: str) -> dict | None:
    """Llama Claude API para generar sugerencia de filtros Apollo
    basados en el nombre de la secuencia + contexto de la empresa.

    Devuelve dict con campos {industria, cargo, ubicacion, tamano_empleados,
    keywords, nota} o None si falla (network, API key, parse error).
    El correo se enviará sin la sección de sugerencia si esto falla."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        ctx = _load_company_context()
        prompt = f"""Empresa: {core_config.COMPANY_NAME} ({core_config.COMPANY_SECTOR}).

Contexto de la empresa:
{ctx}

Tengo una secuencia de prospección en Apollo llamada "{seq_name}" que terminó de mandar todos sus correos. Quiero agregar más contactos del mismo perfil. Sugiéreme los filtros exactos para buscar en Apollo Search.

Devolvé ÚNICAMENTE un JSON válido con esta estructura (sin texto adicional, sin markdown):
{{
  "industria": ["industria1", "industria2"],
  "cargo": ["cargo1", "cargo2", "cargo3"],
  "ubicacion": ["Ecuador", "ciudad si aplica"],
  "tamano_empleados": "rango ej 10-200",
  "keywords": ["keyword1", "keyword2"],
  "nota": "1 línea explicando por qué este perfil encaja con el producto"
}}"""
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            system="Eres asistente experto en prospección B2B con Apollo. Respondes en español neutro, devuelves SOLO JSON válido.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        return json.loads(m.group())
    except Exception as e:
        print(f"[notifier] WARN no se pudo generar sugerencia para '{seq_name}': {e}", file=sys.stderr)
        return None


def _render_filters_block(filters: dict | None) -> str:
    """Renderiza el bloque HTML con la sugerencia. Si filters es None,
    devuelve un placeholder simple."""
    if not filters:
        return ""

    def _list(v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        return str(v) if v else ""

    industria = _list(filters.get("industria"))
    cargo = _list(filters.get("cargo"))
    ubicacion = _list(filters.get("ubicacion"))
    tamano = filters.get("tamano_empleados") or ""
    keywords = _list(filters.get("keywords"))
    nota = filters.get("nota") or ""

    rows = []
    if industria:
        rows.append(("Industria", industria))
    if cargo:
        rows.append(("Cargo / Título", cargo))
    if ubicacion:
        rows.append(("Ubicación", ubicacion))
    if tamano:
        rows.append(("Tamaño empresa", f"{tamano} empleados"))
    if keywords:
        rows.append(("Palabras clave", keywords))

    rows_html = "".join(
        f'<tr><td style="padding:6px 12px; color:#666; font-weight:600; vertical-align:top; white-space:nowrap;">{k}</td>'
        f'<td style="padding:6px 12px;">{v}</td></tr>'
        for k, v in rows
    )

    nota_html = (
        f'<p style="margin: 12px 0 0; padding: 8px 12px; background:#fffde7; '
        f'border-left: 3px solid #f9a825; font-size: 13px; color: #555;">'
        f'<strong>Por qué este perfil:</strong> {nota}</p>'
    ) if nota else ""

    return f"""
<div style="margin-top: 24px; padding: 16px; background:#f0f7fa; border-left: 4px solid #00838f; border-radius: 4px;">
  <h3 style="margin: 0 0 12px; color: #00838f; font-size: 15px;">🔎 Filtros sugeridos para enriquecer esta secuencia</h3>
  <table cellpadding="0" cellspacing="0" border="0" style="font-size: 14px; border-collapse: collapse;">
    {rows_html}
  </table>
  {nota_html}
  <p style="margin: 14px 0 0; font-size: 12px; color: #888;">
    Apollo → Search → People → aplica estos filtros → Add to Sequence → "{filters.get('_seq_name', '...')}"
  </p>
</div>"""


def is_completed(seq: dict) -> bool:
    """Una secuencia se considera 'completada' si está activa, ya entregó >0
    correos, y no le quedan envíos en cola (scheduled == 0).

    Si Apollo devuelve 'loading' en unique_scheduled (campo aún calculándose),
    devolvemos False para no falsamente disparar la notificación — esperamos
    al siguiente tick cuando Apollo ya tenga el número real."""
    if not seq.get("active"):
        return False
    scheduled = seq.get("unique_scheduled")
    if not isinstance(scheduled, int):
        # "loading" u otro estado transitorio — no decidir todavía
        return False
    if scheduled > 0:
        return False
    if safe_int(seq.get("unique_delivered")) <= 0:
        return False
    return True


def should_notify(seq: dict, state: dict) -> bool:
    """Notificar si es la primera vez que la secuencia llega a estado
    completado, o si el delivered cambió desde la última notificación
    (señal de que se agregaron más contactos y se completó un nuevo ciclo)."""
    if not is_completed(seq):
        return False
    entry = state.get(seq["id"])
    if entry is None:
        return True
    return entry.get("delivered_at_notify") != safe_int(seq.get("unique_delivered"))


def build_email_html(seq: dict, filters: dict | None = None) -> str:
    name = seq.get("name", "—")
    delivered = safe_int(seq.get("unique_delivered"))
    opened = safe_int(seq.get("unique_opened"))
    replied = safe_int(seq.get("unique_replied"))
    bounced = safe_int(seq.get("unique_bounced"))
    clicked = safe_int(seq.get("unique_clicked"))
    unsubscribed = safe_int(seq.get("unique_unsubscribed"))

    open_rate = (seq.get("open_rate") or 0) * 100
    reply_rate = (seq.get("reply_rate") or 0) * 100
    bounce_rate = (seq.get("bounce_rate") or 0) * 100
    click_rate = (seq.get("click_rate") or 0) * 100

    # Tag el nombre de la secuencia en los filtros para que el footer pueda referenciarlo
    if filters is not None:
        filters = {**filters, "_seq_name": name}
    filters_block = _render_filters_block(filters)

    return f"""
<html><body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; color: #2e3b3f; max-width: 620px;">
<h2 style="color: #2e7d32;">✅ Secuencia completada</h2>
<p><strong>{name}</strong> ya envió todos los correos pendientes (cola en 0).</p>

<table cellpadding="8" cellspacing="0" border="0" style="border-collapse:collapse; margin: 16px 0; font-size: 14px;">
  <tr style="background:#f0f4f5;"><td><strong>Entregados</strong></td><td style="text-align:right;">{delivered}</td></tr>
  <tr><td>Abiertos</td><td style="text-align:right;">{opened} ({open_rate:.1f}%)</td></tr>
  <tr style="background:#f0f4f5;"><td>Respondidos</td><td style="text-align:right;">{replied} ({reply_rate:.1f}%)</td></tr>
  <tr><td>Clicks</td><td style="text-align:right;">{clicked} ({click_rate:.1f}%)</td></tr>
  <tr style="background:#f0f4f5;"><td>Rebotes</td><td style="text-align:right;">{bounced} ({bounce_rate:.1f}%)</td></tr>
  <tr><td>Bajas (unsubscribed)</td><td style="text-align:right;">{unsubscribed}</td></tr>
</table>
{filters_block}

<p style="margin-top: 20px; padding: 12px; background: #e8f5e9; border-left: 4px solid #2e7d32; font-size: 13px; color: #555;">
Te aviso de nuevo cuando vuelva a completarse un ciclo (si agregas contactos y se vuelven a entregar todos).
</p>

<p style="margin-top: 24px; font-size: 11px; color: #888;">
Generado por <code>apollo_completion_notifier.py</code> · {now_local():%Y-%m-%d %H:%M}
</p>
</body></html>"""


def cmd_status() -> None:
    seqs = apollo_rest.list_sequences()
    state = load_state()
    print(f"=== Apollo completion notifier status @ {now_local():%Y-%m-%d %H:%M %Z} ===\n")

    active = [s for s in seqs if s.get("active")]
    print(f"Secuencias activas ({len(active)}):")
    for s in active:
        scheduled = safe_int(s.get("unique_scheduled"))
        delivered = safe_int(s.get("unique_delivered"))
        completed = is_completed(s)
        will_notify = should_notify(s, state)
        last = state.get(s["id"], {}).get("notified_at", "nunca")

        marker = "→" if will_notify else " "
        status = "COMPLETADA" if completed else f"en curso (scheduled={scheduled})"
        print(f"  {marker} {s['name']:<45} delivered={delivered:<4} {status}  último aviso: {last}")

    pending = [s for s in active if should_notify(s, state)]
    print(f"\nSe notificarían {len(pending)} en el próximo tick.")


def cmd_reset(seq_id: str) -> int:
    state = load_state()
    if seq_id in state:
        del state[seq_id]
        save_state(state)
        print(f"State borrado para {seq_id}. Próximo tick re-notificará si cumple condiciones.")
        return 0
    print(f"No hay state para {seq_id}.")
    return 1


def cmd_tick(dry_run: bool, verbose: bool = False) -> int:
    seqs = apollo_rest.list_sequences()
    state = load_state()
    to_notify = [s for s in seqs if should_notify(s, state)]

    if not to_notify:
        if verbose:
            print(f"[{('DRY' if dry_run else 'LIVE')}] sin secuencias por notificar")
        return 0

    for seq in to_notify:
        name = seq.get("name", "—")
        delivered = safe_int(seq.get("unique_delivered"))
        prefix = "[DRY]" if dry_run else "[LIVE]"

        if dry_run:
            print(f"{prefix} NOTIFY  {name}  (delivered={delivered})")
            continue

        try:
            filters = suggest_apollo_filters(name)  # None si falla — se omite el bloque
            html = build_email_html(seq, filters)
            send_email(
                to=NOTIFY_TO,
                subject=f"✅ Secuencia '{name}' completada",
                body_html=html,
                interactive_ok=False,
            )
            print(f"{prefix} OK  NOTIFY  {name}  (delivered={delivered})")
            state[seq["id"]] = {
                "notified_at": now_local().isoformat(),
                "delivered_at_notify": delivered,
                "name": name,
            }
            save_state(state)
        except Exception as e:
            print(f"{prefix} ERROR  NOTIFY  {name}  → {e}")
            return 1

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Notificador de secuencias Apollo completadas")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="Mostrar estado actual sin enviar")
    g.add_argument("--reset", metavar="SEQ_ID", help="Borrar state de una secuencia (forzar re-notificación)")
    p.add_argument("--dry-run", action="store_true", help="No enviar correos, solo mostrar")
    p.add_argument("--verbose", "-v", action="store_true", help="Mostrar más detalle")
    args = p.parse_args()

    try:
        if args.status:
            cmd_status()
            return 0
        if args.reset:
            return cmd_reset(args.reset)
        return cmd_tick(args.dry_run, args.verbose)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if "403" in str(e):
            print("\nHint: La API key necesita ser Master API Key.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
