"""Conectar la cuenta TikTok de un tenant — un solo comando (M3.0d).

    python -m marketing.tiktok_connect biodegradables

Habla con el bot (VERIA_BOT_BASE_URL + ADMIN_API_TOKEN, los mismos del puente
L0): pide la URL de autorización, la abre en el navegador, y espera a que el
dueño de la cuenta acepte en TikTok (el callback del bot guarda los tokens).
Cuando `status` devuelve connected, termina. Todo inyectable para tests.
"""

from __future__ import annotations

import sys
import time
import webbrowser

from marketing.l0_remote import Caller, _bot_config, _default_caller


def connect(
    tenant_id: str,
    *,
    caller: Caller | None = None,
    opener=webbrowser.open,
    sleeper=time.sleep,
    max_wait_s: int = 15 * 60,
    poll_s: int = 5,
    out=print,
) -> bool:
    if caller is None and _bot_config() is None:
        out("Falta configurar VERIA_BOT_BASE_URL y ADMIN_API_TOKEN (env vars User).")
        return False
    call = caller or _default_caller

    estado = call("GET", f"/admin/marketing/tiktok/status?tenant_id={tenant_id}", None)
    if estado.get("connected"):
        out(f"✅ {tenant_id} ya tiene su cuenta TikTok conectada "
            f"(open_id={estado.get('open_id', '')}). Nada que hacer.")
        return True

    inicio = call("POST", "/admin/marketing/tiktok/connect-start",
                  {"tenant_id": tenant_id})
    url = inicio["authorize_url"]
    out("Abrí esta URL e iniciá sesión con la cuenta TikTok del tenant:")
    out(f"  {url}")
    try:
        opener(url)
    except Exception:
        pass  # sin navegador (SSH/headless): la URL impresa alcanza

    out(f"Esperando la autorización (máx {max_wait_s // 60} min)...")
    esperado = 0
    while esperado < max_wait_s:
        sleeper(poll_s)
        esperado += poll_s
        estado = call("GET", f"/admin/marketing/tiktok/status?tenant_id={tenant_id}", None)
        if estado.get("connected"):
            out(f"✅ Cuenta conectada (open_id={estado.get('open_id', '')}, "
                f"scopes={estado.get('scopes', '')}). Los tokens quedaron "
                "cifrados en el bot; se renuevan solos.")
            return True
    out("⏱️ Tiempo agotado sin autorización — corré el comando de nuevo "
        "cuando el dueño de la cuenta esté listo (el state anterior expira solo).")
    return False


if __name__ == "__main__":  # pragma: no cover
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tenant = sys.argv[1] if len(sys.argv) > 1 else "biodegradables"
    raise SystemExit(0 if connect(tenant) else 1)
