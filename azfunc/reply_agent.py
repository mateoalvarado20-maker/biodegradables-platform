"""Agente automatizado de respuestas a prospectos Apollo.

Flujo:
1. Lee correos no leídos del inbox de Mateo desde el último check
2. Filtra correos internos, no-reply, sistemas automáticos
3. Para cada correo restante, enriquece al remitente vía Apollo
4. Si Apollo lo identifica como prospecto, genera borrador personalizado con Claude
5. Crea el borrador en Outlook Drafts (queda enhebrado con el correo original)
6. Persiste estado en ~/.claude-agent/reply_state.json para no duplicar

Uso:
    python reply_agent.py                # corrida normal (crea borradores)
    python reply_agent.py --dry-run      # imprime lo que generaría, no crea drafts
    python reply_agent.py --since-hours 48  # procesa correos de últimas 48h
    python reply_agent.py --limit 5      # solo procesa los primeros 5 candidatos
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic

import apollo_rest
import outlook_client

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000

# F2.4 (VER-IA 2026-07-02): identidad del tenant desde core_config — nada de
# marca ni firmante horneado en el prompt (un tenant nuevo firma con SU gente).
import core_config

OWN_DOMAIN = core_config.COMPANY_DOMAIN
NO_REPLY_PATTERNS = re.compile(
    r"(no.?reply|noreply|mailer.daemon|postmaster|notifications?|automated|donotreply|news)@",
    re.IGNORECASE,
)

CONTEXT_PATH = Path(__file__).parent / "company_context.md"

# ============== STATE ==============
# Fase 4: delegado a reply_state.py — módulo ÚNICO para ambos runtimes
# (Azure Table en producción, archivo local endurecido con safe_json en la
# PC). Antes la copia raíz y la de azfunc tenían states disjuntos (P3).
import reply_state


# ============== FILTRADO ==============
def _is_candidate(msg: dict) -> tuple[bool, str]:
    """Decide si un correo es candidato a respuesta automática.

    Returns: (is_candidate, reason_if_not)
    """
    sender = (msg.get("from") or {}).get("emailAddress") or {}
    addr = (sender.get("address") or "").lower()

    if not addr:
        return False, "sin remitente"
    if OWN_DOMAIN in addr:
        return False, f"interno ({addr})"
    if NO_REPLY_PATTERNS.search(addr):
        return False, f"no-reply ({addr})"

    subject = (msg.get("subject") or "").lower()
    if subject.startswith(("undeliverable:", "out of office", "fuera de la oficina")):
        return False, "auto-respuesta"

    return True, ""


# ============== CLAUDE ==============
def _load_context() -> str:
    if not CONTEXT_PATH.exists():
        raise RuntimeError(f"Falta {CONTEXT_PATH}. Es el contexto de la empresa.")
    return CONTEXT_PATH.read_text(encoding="utf-8")


def _system_prompt(company_context: str) -> str:
    company = core_config.COMPANY_NAME
    signer_name = core_config.outbound_signer_name()
    signer_email = core_config.OUTBOUND_SIGNER_EMAIL
    website = core_config.COMPANY_WEBSITE
    website_label = website.replace("https://", "").replace("http://", "").strip("/")
    return f"""Eres un asistente comercial de {company}. Tu trabajo es
generar borradores de respuesta a correos de prospectos que llegan al inbox
de {signer_name} ({signer_email}).

CONTEXTO COMPLETO DE LA EMPRESA (catálogo, diferenciadores, reglas):
========================================================================
{company_context}
========================================================================

INSTRUCCIONES PARA GENERAR LA RESPUESTA:

1. Lee el thread del correo entrante completo (puede tener varios mensajes
   anteriores). Identifica:
   - Qué pidió el prospecto (cotización, info general, producto específico, etc.)
   - Si es primer contacto o ya hubo intercambio previo

2. Usa los datos de enriquecimiento de Apollo (nombre, cargo, empresa,
   industria) para personalizar el saludo y recomendar la categoría correcta
   según la tabla de matching del contexto.

3. Sigue ESTRICTAMENTE las reglas del contexto:
   - No inventes precios, MOQ, tiempos, certificaciones, ni productos
   - No ofrezcas muestras
   - No menciones a terceros de la empresa ({signer_name} maneja personalmente el primer contacto)
   - No uses emojis
   - Máximo 8-10 líneas
   - Cierra con disposición personal de {signer_name} a coordinar siguientes pasos

4. Responde en el mismo idioma del correo entrante.

FORMATO DE TU RESPUESTA:

Devuelve EXCLUSIVAMENTE un objeto JSON con esta estructura, sin texto antes ni después:

{{
  "should_draft": true|false,
  "reason_if_skip": "razón si should_draft=false",
  "subject_prefix": "Re: " o vacío,
  "body_html": "<p>...</p>"
}}

REGLAS PARA should_draft=false:
- El correo no es realmente un prospecto interesado (spam, factura, soporte interno)
- El correo necesita acción humana específica que tú no puedes resolver
- No hay info suficiente para personalizar (ni del thread ni de Apollo)

El body_html debe ser HTML válido y conservador (solo <p>, <br>, <strong>, <a>,
<ul>, <li>). Outlook borra CSS de <style>, así que nada de clases ni styles
complicados. Saltos de línea con <br> o párrafos con <p>.

Firma sugerida al final (incluir tal cual):
<p>Saludos,<br>{signer_name}<br>{company}<br>
<a href="{website}">{website_label}</a></p>
"""


def _format_thread_for_prompt(thread: list[dict]) -> str:
    """Resume el thread para que Claude lo lea."""
    lines = []
    for i, m in enumerate(thread, 1):
        sender = (m.get("from") or {}).get("emailAddress") or {}
        addr = sender.get("address", "?")
        name = sender.get("name", "")
        when = m.get("receivedDateTime", "")
        subj = m.get("subject", "")
        body = m.get("body") or {}
        # bodyPreview es texto plano corto; body.content puede ser HTML.
        # Para el prompt preferimos body.content si es text, sino el preview.
        if body.get("contentType") == "text":
            body_text = (body.get("content") or "")[:3000]
        else:
            # Strip HTML simple
            content = body.get("content") or m.get("bodyPreview", "")
            body_text = re.sub(r"<[^>]+>", " ", content)
            body_text = re.sub(r"\s+", " ", body_text).strip()[:3000]
        lines.append(
            f"--- Mensaje {i} ({when}) ---\n"
            f"De: {name} <{addr}>\n"
            f"Asunto: {subj}\n"
            f"Cuerpo: {body_text}\n"
        )
    return "\n".join(lines)


def generate_draft(
    thread: list[dict],
    prospect_data: dict | None,
    company_context: str,
    *,
    verbose: bool = False,
) -> dict:
    """Llama a Claude y devuelve el dict parseado con should_draft / body_html."""
    client = Anthropic()

    user_msg = f"""THREAD DEL CORREO ENTRANTE (cronológico, el último es el que tienes que responder):

{_format_thread_for_prompt(thread)}

DATOS DE APOLLO SOBRE EL REMITENTE:
{json.dumps(prospect_data, ensure_ascii=False, indent=2) if prospect_data else "(Apollo no enriqueció este contacto)"}

Genera el borrador de respuesta siguiendo las reglas. Devuelve solo el JSON."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": _system_prompt(company_context),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    # F3 (VER-IA): metering de consumo — nunca lanza.
    import llm_usage
    llm_usage.record("reply_agent", MODEL, response.usage)

    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text

    if verbose:
        print(f"  [claude raw] {text[:300]}...", file=sys.stderr)

    # Extraer JSON (puede venir envuelto en ```json ... ``` o sin)
    # Fase 3 (auditoría C3): parse_error=True distingue "Claude FALLÓ en
    # formatear" (transitorio → reintento) de "Claude decidió no responder"
    # (definitivo → se marca procesado).
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {
            "should_draft": False,
            "parse_error": True,
            "reason_if_skip": f"Claude no devolvió JSON parseable: {text[:200]}",
        }
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {
            "should_draft": False,
            "parse_error": True,
            "reason_if_skip": f"JSON inválido: {e}",
        }


# ============== MAIN ==============
def process_inbox(
    *, dry_run: bool = False, since_hours: int = 24, limit: int | None = None,
    verbose: bool = False,
) -> dict:
    """Procesa el inbox y devuelve un resumen de lo hecho."""
    # Calcular ventana de búsqueda
    since_iso = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[INFO] Buscando correos no leídos desde {since_iso}...")
    msgs = outlook_client.list_unread_inbox(since_iso=since_iso, top=100)
    print(f"[INFO] {len(msgs)} no leídos encontrados.")

    company_context = _load_context()

    stats = {"total": len(msgs), "skipped_filter": 0, "skipped_processed": 0,
             "skipped_no_apollo": 0, "skipped_claude": 0,
             "drafts_created": 0, "errors": 0}

    candidates_processed = 0
    for msg in msgs:
        msg_id = msg.get("id", "")
        sender_addr = (msg.get("from") or {}).get("emailAddress", {}).get("address", "?")
        subject = msg.get("subject", "(sin asunto)")
        label = f"{sender_addr} | {subject[:50]}"

        if reply_state.is_processed(msg_id):
            stats["skipped_processed"] += 1
            if verbose:
                print(f"  [skip-procesado] {label}")
            continue

        ok, reason = _is_candidate(msg)
        if not ok:
            stats["skipped_filter"] += 1
            if verbose:
                print(f"  [skip-filtro:{reason}] {label}")
            continue

        if limit is not None and candidates_processed >= limit:
            print(f"[INFO] Límite de {limit} candidatos alcanzado, parando.")
            break

        candidates_processed += 1
        print(f"\n[PROCESANDO] {label}")

        # Enriquecer remitente
        try:
            prospect = apollo_rest.enrich_by_email(sender_addr)
        except apollo_rest.ApolloAPIError as e:
            # Fase 3 (auditoría C2): error transitorio de Apollo — NO se
            # marca como procesado; el próximo tick lo reintenta.
            stats["errors"] += 1
            print(f"  → ERROR Apollo (transitorio): {e}. Reintento en el próximo tick.")
            continue
        if prospect is None:
            stats["skipped_no_apollo"] += 1
            print(f"  → Apollo no encontró {sender_addr}. Skip.")
            # Igual marcarlo como procesado para no reintentar
            reply_state.mark_processed(msg_id)
            continue

        print(f"  → Apollo: {prospect.get('name')} ({prospect.get('title')}) "
              f"en {(prospect.get('organization') or {}).get('name')}")

        # Traer el correo completo con body (el body de un Re: ya incluye el thread citado)
        try:
            full_msg = outlook_client.get_message(msg_id)
            thread = [full_msg]
        except Exception as e:
            print(f"  → ERROR trayendo correo: {e}")
            stats["errors"] += 1
            continue

        # Generar borrador con Claude
        try:
            result = generate_draft(thread, prospect, company_context, verbose=verbose)
        except Exception as e:
            print(f"  → ERROR Claude: {e}")
            stats["errors"] += 1
            continue

        if result.get("parse_error"):
            # Fallo de formato transitorio — NO marcar procesado (C3).
            stats["errors"] += 1
            print(f"  → ERROR formato Claude: {result.get('reason_if_skip', '?')}. "
                  "Reintento en el próximo tick.")
            continue

        if not result.get("should_draft"):
            stats["skipped_claude"] += 1
            print(f"  → Claude decidió SKIP: {result.get('reason_if_skip', '?')}")
            reply_state.mark_processed(msg_id)
            continue

        body_html = result.get("body_html", "")
        if not body_html:
            stats["errors"] += 1
            print("  → ERROR: should_draft=true pero body_html vacío")
            continue

        if dry_run:
            print("  → [DRY-RUN] Borrador NO creado. Preview:")
            print("  " + "\n  ".join(body_html.splitlines()[:20]))
            stats["drafts_created"] += 1
        else:
            try:
                draft = outlook_client.create_draft_reply(msg_id, body_html)
                web_link = draft.get("webLink", "(sin link)")
                print(f"  → Borrador creado: {web_link}")
                stats["drafts_created"] += 1
                reply_state.mark_processed(msg_id)
            except Exception as e:
                print(f"  → ERROR creando draft: {e}")
                stats["errors"] += 1
                continue

    # Guardar timestamp del check (solo informativo)
    reply_state.set_last_check(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    return stats


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="No crea drafts, solo imprime lo que generaría")
    parser.add_argument("--since-hours", type=int, default=24,
                        help="Cuántas horas hacia atrás buscar correos (default 24)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Máximo de candidatos a procesar (default: todos)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    stats = process_inbox(
        dry_run=args.dry_run,
        since_hours=args.since_hours,
        limit=args.limit,
        verbose=args.verbose,
    )

    print("\n=== RESUMEN ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if args.dry_run:
        print("\n(DRY-RUN: ningún borrador fue creado en Outlook)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
