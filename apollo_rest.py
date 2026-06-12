"""Cliente Apollo.io REST API para enriquecimiento de prospectos.

Usado por reply_agent.py para identificar quién es el remitente de un correo
entrante (nombre, cargo, empresa, industria) antes de generar la respuesta.

Cache local en ~/.claude-agent/apollo_cache.json para no quemar créditos
re-enriqueciendo el mismo correo. TTL de 30 días (datos B2B son estables).

Scopes requeridos en la API key:
- api/v1/people/match           (enrich por email, el más importante)
- api/v1/organizations/enrich   (datos de la empresa)
- api/v1/contacts/search        (buscar en tu CRM Apollo)
- api/v1/mixed_people/api_search (búsqueda amplia en la base global)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.apollo.io/v1"
API_KEY = os.environ.get("APOLLO_API_KEY", "")

CACHE_PATH = Path.home() / ".claude-agent" / "apollo_cache.json"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 días

_cache: dict[str, dict] | None = None


def _headers() -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError(
            "Falta APOLLO_API_KEY en variables de entorno. "
            "Genérala en https://developer.apollo.io/keys/"
        )
    return {
        "Cache-Control": "no-cache",
        "Content-Type": "application/json",
        "X-Api-Key": API_KEY,
    }


def _load_cache() -> dict[str, dict]:
    # Fase 1: safe_json (atómico + cuarentena). Es solo un cache (perderlo
    # quema créditos Apollo, no datos de negocio), pero la escritura atómica
    # evita corromperlo a mitad de un run del Task Scheduler.
    global _cache
    if _cache is not None:
        return _cache
    import safe_json
    _cache = safe_json.load_json(CACHE_PATH, dict)
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    import safe_json
    safe_json.save_json(CACHE_PATH, _cache)


def _cache_get(key: str) -> dict | None:
    cache = _load_cache()
    entry = cache.get(key)
    if not entry:
        return None
    if time.time() - entry.get("_cached_at", 0) > CACHE_TTL_SECONDS:
        return None
    return entry.get("data")


def _cache_set(key: str, data: dict) -> None:
    cache = _load_cache()
    cache[key] = {"_cached_at": time.time(), "data": data}
    _save_cache()


def _post(path: str, body: dict | None = None) -> dict:
    r = httpx.post(
        f"{API_BASE}{path}",
        headers=_headers(),
        json=body or {},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Apollo {path} → {r.status_code}: {r.text[:300]}")
    return r.json()


def _get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(
        f"{API_BASE}{path}",
        headers=_headers(),
        params=params or {},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Apollo {path} → {r.status_code}: {r.text[:300]}")
    return r.json()


def enrich_by_email(email: str, *, use_cache: bool = True) -> dict | None:
    """Enriquece un contacto a partir de su email.

    Devuelve un dict con datos del contacto + organización, o None si Apollo
    no lo encontró en su base.

    Estructura del resultado (campos relevantes):
        {
            "name": "John Doe",
            "first_name": "John",
            "title": "Operations Manager",
            "email": "john@acme.com",
            "linkedin_url": "...",
            "city": "Quito",
            "country": "Ecuador",
            "organization": {
                "name": "Acme Restaurants",
                "website_url": "...",
                "industry": "Food & Beverages",
                "estimated_num_employees": 120,
                "short_description": "...",
            },
        }
    """
    email_lc = email.lower().strip()
    if use_cache:
        cached = _cache_get(f"email:{email_lc}")
        if cached is not None:
            return cached if cached else None

    try:
        data = _post(
            "/people/match",
            {"email": email_lc, "reveal_personal_emails": False},
        )
    except RuntimeError as e:
        # Error real de API (auth, network, etc.) - no cachear, propagar visiblemente
        print(f"[apollo_rest] ERROR enriqueciendo {email_lc}: {e}", file=sys.stderr)
        return None

    person = data.get("person") or {}
    if not person:
        # Cachear el "no match" también para no reintentar
        _cache_set(f"email:{email_lc}", {})
        return None

    org = person.get("organization") or {}
    result = {
        "name": person.get("name"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "title": person.get("title"),
        "email": person.get("email") or email_lc,
        "linkedin_url": person.get("linkedin_url"),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        "organization": {
            "name": org.get("name"),
            "website_url": org.get("website_url"),
            "industry": org.get("industry"),
            "estimated_num_employees": org.get("estimated_num_employees"),
            "short_description": org.get("short_description"),
            "country": org.get("country"),
        } if org else None,
    }
    _cache_set(f"email:{email_lc}", result)
    return result


def enrich_organization(domain: str, *, use_cache: bool = True) -> dict | None:
    """Enriquece una organización por dominio (ej. 'acme.com')."""
    domain_lc = domain.lower().strip()
    if use_cache:
        cached = _cache_get(f"org:{domain_lc}")
        if cached is not None:
            return cached if cached else None

    try:
        data = _post("/organizations/enrich", {"domain": domain_lc})
    except RuntimeError as e:
        print(f"[apollo_rest] ERROR enriqueciendo org {domain_lc}: {e}", file=sys.stderr)
        return None

    org = data.get("organization") or {}
    if not org:
        _cache_set(f"org:{domain_lc}", {})
        return None

    result = {
        "name": org.get("name"),
        "website_url": org.get("website_url"),
        "industry": org.get("industry"),
        "estimated_num_employees": org.get("estimated_num_employees"),
        "short_description": org.get("short_description"),
        "country": org.get("country"),
        "founded_year": org.get("founded_year"),
        "keywords": org.get("keywords", [])[:10],
    }
    _cache_set(f"org:{domain_lc}", result)
    return result


# ---------------------------------------------------------------------------
# Sequence management (emailer_campaigns)
# Estas funciones requieren que APOLLO_API_KEY sea una Master API Key.
# Sin permiso master, /approve y /abort devuelven 403.
# ---------------------------------------------------------------------------


def list_sequences(*, per_page: int = 100, include_archived: bool = False) -> list[dict]:
    """Lista todas las secuencias del equipo en Apollo.

    Pagina hasta agotar resultados. Devuelve solo no-archivadas por defecto.
    """
    out: list[dict] = []
    page = 1
    while True:
        data = _post(
            "/emailer_campaigns/search",
            {"per_page": per_page, "page": page},
        )
        seqs = data.get("emailer_campaigns") or []
        if not include_archived:
            seqs = [s for s in seqs if not s.get("archived")]
        out.extend(seqs)
        pagination = data.get("pagination") or {}
        if page >= int(pagination.get("total_pages") or 1):
            break
        page += 1
    return out


def get_sequence(sequence_id: str) -> dict | None:
    """Trae el detalle de una secuencia buscándola por id en /search."""
    for s in list_sequences(include_archived=True):
        if s.get("id") == sequence_id:
            return s
    return None


def activate_sequence(sequence_id: str) -> dict:
    """Activa (approve) una secuencia. Requiere Master API Key."""
    return _post(f"/emailer_campaigns/{sequence_id}/approve")


def deactivate_sequence(sequence_id: str) -> dict:
    """Desactiva (abort) una secuencia. Pausa el envío a todos los contactos.
    Requiere Master API Key."""
    return _post(f"/emailer_campaigns/{sequence_id}/abort")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso:")
        print("  python apollo_rest.py <email>              # enrich")
        print("  python apollo_rest.py --list-sequences     # listar secuencias")
        sys.exit(1)

    if sys.argv[1] == "--list-sequences":
        seqs = list_sequences()
        for s in seqs:
            active = "✓ ACTIVA" if s.get("active") else "  pausada"
            scheduled = s.get("unique_scheduled")
            print(f"{active}  scheduled={scheduled:<8}  {s['id']}  {s['name']}")
        print(f"\nTotal: {len(seqs)} secuencias")
        sys.exit(0)

    email = sys.argv[1]
    print(f"Enriqueciendo {email}...")
    result = enrich_by_email(email, use_cache=False)
    if result is None:
        print("Apollo no encontró este contacto.")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
