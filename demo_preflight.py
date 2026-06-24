"""demo_preflight — chequeo go/no-go del entorno DEMO antes de salir en vivo.

Valida que la configuración apunte SOLO a la empresa ficticia y al dominio demo,
para que sea imposible filtrar datos de un cliente real durante una presentación
(sobre todo en la Fase 5, con bots en vivo). Pensado para correr justo antes de
arrancar el bot/demo:

    python demo_preflight.py     # imprime el reporte; exit 0 = OK, 1 = hay fallas

Cada check es fail-closed: ante la duda, FALLA. Complementa el sandbox de
graph_mail y el scanner de demo_guard.
"""
from __future__ import annotations

import os
import sys

import core_config
import demo_guard


def _domain() -> str:
    return os.environ.get("DEMO_EMAIL_DOMAIN", "andexdemo.com").strip().lower().lstrip("@")


def _all_in_domain(emails: list[str], domain: str) -> list[str]:
    """Devuelve los emails que NO pertenecen al dominio demo (offenders)."""
    bad = []
    for e in emails:
        e = (e or "").strip().lower()
        if e and not e.endswith("@" + domain):
            bad.append(e)
    return bad


def checks() -> list[dict]:
    out: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    domain = _domain()

    add("DEMO_MODE=1", os.environ.get("DEMO_MODE") == "1",
        os.environ.get("DEMO_MODE", "(no seteado)"))
    add("TENANT_CONFIG_SOURCE=yaml", os.environ.get("TENANT_CONFIG_SOURCE") == "yaml",
        os.environ.get("TENANT_CONFIG_SOURCE", "(no seteado)"))

    slug = os.environ.get("TENANT_SLUG", "").strip().lower()
    add("TENANT_SLUG no es el cliente real", bool(slug) and slug != "biodegradables", slug or "(vacío)")

    # El tenant carga y el yaml tomó efecto (COMPANY_NAME ya no es el real).
    add("Tenant cargado (no Biodegradables)",
        core_config.COMPANY_NAME != "Biodegradables Ecuador",
        f"COMPANY_NAME={core_config.COMPANY_NAME!r}")

    # Todos los destinatarios de negocio en el dominio demo.
    recipients = [
        *core_config.JEFE, core_config.MIO, core_config.GABRIELA,
        *core_config.CHECKIN_OFICINA, *core_config.CHECKIN_SUCURSALES,
        *core_config.PEOPLE.keys(),
    ]
    bad_recip = _all_in_domain(recipients, domain)
    add(f"Destinatarios/personas en @{domain}", not bad_recip,
        "todos OK" if not bad_recip else f"fuera de dominio: {bad_recip}")

    # Buzones del sandbox de correo.
    bad_mail = _all_in_domain(
        [os.environ.get("DEMO_EMAIL_TO", ""), os.environ.get("DEMO_FROM_USER", "")], domain
    )
    add("DEMO_EMAIL_TO/FROM en dominio demo", not bad_mail,
        "OK" if not bad_mail else f"fuera de dominio: {bad_mail}")

    # Allowlist del bot (si está seteada).
    allow = os.environ.get("BOT_ALLOWED_USERS_DATA", "")
    if allow.strip():
        bad_allow = _all_in_domain([e for e in allow.split(",")], domain)
        add("BOT_ALLOWED_USERS_DATA en dominio demo", not bad_allow,
            "OK" if not bad_allow else f"fuera de dominio: {bad_allow}")

    # El prompt del Data Bot no contiene identificadores del cliente real.
    try:
        import ask_agent
        prompt = ask_agent._system_prompt_data()
        hits = demo_guard.scan_for_real_data(prompt)
        add("Prompt del Data Bot sin datos reales", not hits,
            "limpio" if not hits else f"fuga: {hits}")
    except Exception as e:  # noqa: BLE001
        add("Prompt del Data Bot sin datos reales", False, f"no se pudo evaluar: {e}")

    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    results = checks()
    print(f"== demo_preflight — tenant={os.environ.get('TENANT_SLUG','?')} dominio=@{_domain()} ==")
    for r in results:
        mark = "✅" if r["ok"] else "❌"
        print(f"  {mark} {r['check']}: {r['detail']}")
    ok = all(r["ok"] for r in results)
    print("\n" + ("✅ TODO OK — el demo es seguro para salir en vivo."
                  if ok else "❌ HAY FALLAS — NO salgas en vivo hasta resolverlas."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
