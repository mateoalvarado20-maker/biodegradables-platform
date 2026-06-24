"""demo_guard — red de seguridad anti-fuga del entorno DEMO (Fase 0).

Dos garantías complementarias al sandbox de `graph_mail.py`:

1. `scan_for_real_data(text)` / `assert_no_real_data(text)` — escanean texto
   renderizado (HTML de un correo, JSON de estado, prompt de un bot) buscando
   identificadores del cliente REAL. Pensado para tests y para un check opcional
   antes de enviar/mostrar contenido en una demo.

2. `verify_demo_config()` — check de arranque: cuando DEMO_MODE=1, valida que la
   configuración apunte a un tenant/dominio demo y NO al cliente real. Falla
   cerrado (RuntimeError) para abortar el arranque de un demo mal configurado.

Los identificadores prohibidos por defecto son los del cliente real actual
(Biodegradables Ecuador). Se pueden ampliar por env var DEMO_FORBIDDEN_EXTRA
(coma-separado) sin tocar código — útil al sumar más clientes reales.

Sin DEMO_MODE, `verify_demo_config()` es no-op: no cambia nada en producción.
"""
from __future__ import annotations

import os

# Identificadores del cliente REAL que JAMÁS deben aparecer en un demo.
# Se comparan case-insensitive. Mantener acá los tokens inequívocos del cliente
# (dominio, marca, nombres completos de personas). Evitar tokens genéricos que
# den falsos positivos (p.ej. solo "Quito" o solo un apellido común).
_DEFAULT_FORBIDDEN: tuple[str, ...] = (
    "biodegradablesecuador",
    "biodegradables ecuador",
    "daniel sánchez",
    "daniel sanchez",
    "gabriela sánchez",
    "gabriela sanchez",
    "gabriela bravo",
    "mateo alvarado",
    "gladys lópez",
    "gladys lopez",
    "josé solórzano",
    "jose solorzano",
)


def _forbidden_terms() -> list[str]:
    terms = list(_DEFAULT_FORBIDDEN)
    extra = os.environ.get("DEMO_FORBIDDEN_EXTRA", "")
    terms.extend(t.strip().lower() for t in extra.split(",") if t.strip())
    return terms


def scan_for_real_data(text: str) -> list[str]:
    """Devuelve la lista de identificadores reales hallados en `text`
    (vacía si está limpio). Case-insensitive."""
    if not text:
        return []
    hay = text.lower()
    return [term for term in _forbidden_terms() if term in hay]


def assert_no_real_data(text: str, *, context: str = "") -> None:
    """Aborta si `text` contiene cualquier identificador del cliente real."""
    hits = scan_for_real_data(text)
    if hits:
        where = f" en {context}" if context else ""
        raise RuntimeError(
            f"[DEMO] Fuga de datos reales detectada{where}: {sorted(set(hits))}. "
            "El contenido no debe salir en un entorno demo."
        )


def is_demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "").strip() == "1"


def verify_demo_config() -> None:
    """Check de arranque del entorno demo. No-op si DEMO_MODE != 1.

    Cuando DEMO_MODE=1, valida que:
      - TENANT_SLUG no sea el del cliente real ('biodegradables'),
      - DEMO_EMAIL_TO / DEMO_FROM_USER pertenezcan al dominio demo permitido.
    Falla cerrado para no arrancar un demo que pueda filtrar datos reales.
    """
    if not is_demo_mode():
        return

    slug = os.environ.get("TENANT_SLUG", "").strip().lower()
    if slug == "biodegradables":
        raise RuntimeError(
            "[DEMO_MODE] TENANT_SLUG=biodegradables es el cliente REAL. "
            "Un demo debe usar un tenant ficticio (p.ej. TENANT_SLUG=andex)."
        )

    domain = os.environ.get("DEMO_EMAIL_DOMAIN", "andexdemo.com").strip().lower().lstrip("@")
    to_raw = os.environ.get("DEMO_EMAIL_TO", "demo@andexdemo.com")
    addrs = [e.strip().lower() for e in to_raw.split(",") if e.strip()]
    addrs.append(os.environ.get("DEMO_FROM_USER", "demo@andexdemo.com").strip().lower())
    for a in addrs:
        if a and not a.endswith("@" + domain):
            raise RuntimeError(
                f"[DEMO_MODE] '{a}' no pertenece al dominio demo '@{domain}'. "
                "Revisá DEMO_EMAIL_TO / DEMO_FROM_USER / DEMO_EMAIL_DOMAIN."
            )
