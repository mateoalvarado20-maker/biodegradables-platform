"""provision_tenant — aprovisiona la infraestructura de UN cliente (F5.2).

Automatiza el alta que hoy toma 2-4 días manuales (auditoría 2026-07-02):

  1. Dos App Registrations en el M365 DEL CLIENTE (Data Bot + Activities
     Bot, SingleTenant) con secret, y los permisos application de Graph
     que usa la plataforma (Mail.Send, Calendars.ReadWrite).
  2. Dos Azure Bot resources apuntando a los endpoints de la instancia.
  3. App Service Plan + Web App (Linux/Python) con los app settings
     derivados de tenants/<slug>/config.yaml (TENANT_SLUG, switch yaml,
     ADMIN_API_TOKEN aleatorio, flags de módulos) — los SECRETS de
     integraciones (Contifico, HubSpot, Anthropic…) quedan listados como
     pendientes para cargar a mano o desde Key Vault.
  4. Imprime: link de ADMIN CONSENT para el cliente, el comando de
     gen_teams_app con los App IDs nuevos, y el resumen JSON.

Uso:
  python ops/provision_tenant.py <slug> --location eastus2 [--dry-run]
      [--resource-group rg-<slug>-prod] [--plan-sku B1]

Seguridad de operación:
  - --dry-run imprime el PLAN completo (cada comando az) sin ejecutar nada.
  - En vivo exige que `az account show` esté logueado en el tenant del
    CLIENTE (--expected-tenant-id) — evita crear recursos en el tenant
    equivocado.

Hardening (aprendizajes de la primera corrida en vivo, Andex 2026-07-06):
  - Registra los resource providers (las suscripciones nuevas vienen sin
    Microsoft.Web/BotService/etc y todo fallaría).
  - Si la región pedida no tiene cuota de VMs para el plan (limit 0 en subs
    nuevas), reintenta en regiones de fallback y reporta la efectiva.
  - Setea el startup command de gunicorn/uvicorn y Always On (sin esto la
    webapp responde 404 y el scheduler se duerme).
  - Otorga el ADMIN CONSENT de Graph vía appRoleAssignments (estamos
    logueados como admin del cliente) — sin paso interactivo. Se puede
    saltar con --no-grant-consent (imprime el link clásico).
  - RESUMIBLE: si una corrida falla a mitad, re-ejecutar reutiliza lo que
    ya existe (app regs por display name, bots/plan/webapp por show) en
    vez de duplicar. Los secrets se regeneran (credential reset) porque
    no persisten entre corridas.
"""
from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config.integrations import load_tenant_integrations  # noqa: E402
from core.config.loader import load_tenant_config  # noqa: E402

# Graph application permissions que usa la plataforma (resourceAppId de
# Microsoft Graph + los app roles): Mail.Send y Calendars.ReadWrite.
GRAPH_RESOURCE = {
    "resourceAppId": "00000003-0000-0000-c000-000000000000",
    "resourceAccess": [
        {"id": "b633e1c5-b582-4048-a93e-9f11b44c7e96", "type": "Role"},  # Mail.Send
        {"id": "ef54d2bf-783f-4e0f-bca1-3210c0444d99", "type": "Role"},  # Calendars.ReadWrite
    ],
}


class Plan:
    """Acumula comandos az; ejecuta o imprime según dry_run."""

    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.ejecutados: list[str] = []

    def az(self, *args: str, capture: bool = False, sensitive: bool = False,
           tolerar: str | None = None) -> dict | None:
        # sensitive: enmascara los VALORES clave=valor (los secretos van ahí);
        # el comando y los nombres de setting quedan legibles en el plan.
        # tolerar: si el comando falla y el stderr contiene ese substring,
        # devuelve None en vez de abortar (ej. "already exists" en resume).
        mostrado = " ".join(
            f"{a.split('=', 1)[0]}=***" if (sensitive and "=" in a) else a
            for a in args
        )
        self.ejecutados.append(f"az {mostrado}")
        if self.dry_run:
            print(f"[DRY] az {mostrado}")
            return None
        print(f"[RUN] az {mostrado}")
        cmd = ["az", *args, "-o", "json"] if capture else ["az", *args]
        res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if res.returncode != 0:
            if tolerar and tolerar.lower() in res.stderr.lower():
                print(f"[OK] tolerado ({tolerar})")
                return None
            raise SystemExit(f"az falló ({res.returncode}): {res.stderr[:800]}")
        return json.loads(res.stdout) if capture and res.stdout.strip() else None

    def existente(self, *show_args: str) -> dict | list | None:
        """Consulta si un recurso ya existe (para corridas RESUMIBLES).
        No forma parte del plan (es solo lectura); en dry-run devuelve None
        para que el plan muestre siempre el camino de creación completo."""
        if self.dry_run:
            return None
        res = subprocess.run(["az", *show_args, "-o", "json"],
                             capture_output=True, text=True, shell=True)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        return json.loads(res.stdout)


def _app_settings(slug: str, cfg, admin_token: str) -> dict[str, str]:
    """App settings NO-secretos derivables de la config del tenant."""
    return {
        "TENANT_SLUG": slug,
        "TENANT_CONFIG_SOURCE": "yaml",
        "ADMIN_API_TOKEN": admin_token,
        "ALERT_EMAIL": ",".join(cfg.recipients.commercial_report_cc
                                or cfg.recipients.commercial_report),
        "STATE_DIR": "/home/.claude-agent",
        "WEBSITES_PORT": "8000",
        "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
    }


# Secrets fallback cuando el tenant NO tiene integrations.yaml (F5.3): el
# operador los carga a mano. Con integrations.yaml, la lista real sale de ahí.
SECRETS_PENDIENTES_DEFAULT = [
    "ANTHROPIC_API_KEY", "CONTIFICO_API_TOKEN", "HUBSPOT_TOKEN",
    "APOLLO_API_KEY", "MSAL_CACHE_B64 (si contrata prospección)",
    "AzureWebJobsStorage (si contrata prospección)",
]

PLACEHOLDER_KV = "REEMPLAZAR"

# Resource providers que usa la instancia. Las suscripciones RECIÉN creadas
# no traen ninguno registrado y cada `az ... create` fallaría.
PROVIDERS = ["Microsoft.Web", "Microsoft.BotService", "Microsoft.KeyVault",
             "Microsoft.Insights", "Microsoft.Storage"]

# Regiones alternativas cuando la pedida no tiene cuota de VMs (las subs
# nuevas suelen arrancar con Total VMs = 0 en varias regiones).
REGIONES_FALLBACK = ["centralus", "eastus", "westus2", "southcentralus"]

# Sin esto la webapp responde 404 (Oryx no adivina el entrypoint FastAPI).
# 1 worker DELIBERADO: lease del scheduler y locks son por proceso.
STARTUP_CMD = ("gunicorn -w 1 -k uvicorn.workers.UvicornWorker "
               "--bind 0.0.0.0:8000 --timeout 120 teams_bot:app")


def _crear_plan_con_fallback(plan: Plan, rg: str, plan_name: str,
                             location: str, sku: str) -> str:
    """Crea el App Service Plan; si la región no tiene cuota, prueba las
    de fallback. Devuelve la región efectiva."""
    ya = plan.existente("appservice", "plan", "show", "-g", rg, "-n", plan_name)
    if ya:
        loc = str(ya.get("location", location)).replace(" ", "").lower()
        print(f"[SKIP] plan {plan_name} ya existe en {loc} (resume)")
        return loc
    regiones = [location] + [r for r in REGIONES_FALLBACK if r != location]
    for i, loc in enumerate(regiones):
        try:
            plan.az("appservice", "plan", "create", "-g", rg, "-n", plan_name,
                    "-l", loc, "--sku", sku, "--is-linux")
            return loc
        except SystemExit as e:
            if "quota" not in str(e).lower() or i == len(regiones) - 1:
                raise
            print(f"[WARN] sin cuota de VMs en {loc} — probando {regiones[i + 1]}")
    return location  # inalcanzable; para el type checker


def _grant_admin_consent(plan: Plan, app_ids: list[str]) -> bool:
    """Otorga el admin consent SIN paso interactivo: crea el service
    principal de cada app y le asigna los app roles de Graph vía
    appRoleAssignments (equivale a aprobar el consent URL). Requiere que
    la sesión az sea admin del tenant del cliente."""
    graph = plan.az("ad", "sp", "show", "--id",
                    GRAPH_RESOURCE["resourceAppId"], capture=True)
    graph_oid = graph["id"] if graph else "<graph-sp-oid>"
    for app_id in app_ids:
        plan.az("ad", "sp", "create", "--id", app_id, tolerar="already exists")
        sp = plan.az("ad", "sp", "show", "--id", app_id, capture=True)
        sp_oid = sp["id"] if sp else f"<sp-oid-{app_id}>"
        for acceso in GRAPH_RESOURCE["resourceAccess"]:
            body = json.dumps({"principalId": sp_oid, "resourceId": graph_oid,
                               "appRoleId": acceso["id"]})
            plan.az("rest", "--method", "post",
                    "--url", ("https://graph.microsoft.com/v1.0/"
                              f"servicePrincipals/{sp_oid}/appRoleAssignments"),
                    "--body", body,
                    "--headers", "Content-Type=application/json",
                    tolerar="Permission being assigned already exists")
    return True


def _kv_name(slug: str) -> str:
    """Nombre de Key Vault válido (3-24 chars, alfanumérico y guiones)."""
    base = f"kv-{slug}-veria".replace("_", "-")
    return base[:24].rstrip("-")


def provision(slug: str, location: str, resource_group: str | None,
              plan_sku: str, expected_tenant_id: str | None,
              dry_run: bool, grant_consent: bool = True) -> dict:
    cfg = load_tenant_config(slug)
    rg = resource_group or f"rg-{slug}-prod"
    plan_name = f"plan-{slug}"
    webapp = f"{slug}-veria-app"
    display = cfg.display_name
    plan = Plan(dry_run)

    # 0) Guardia de tenant: no crear recursos en el directorio equivocado.
    if not dry_run:
        res = subprocess.run(["az", "account", "show", "-o", "json"],
                             capture_output=True, text=True, shell=True)
        if res.returncode != 0:
            raise SystemExit("az no está logueado. `az login --tenant <cliente>` primero.")
        cuenta = json.loads(res.stdout)
        if expected_tenant_id and cuenta.get("tenantId") != expected_tenant_id:
            raise SystemExit(
                f"az está logueado en el tenant {cuenta.get('tenantId')}, "
                f"se esperaba {expected_tenant_id}. Abortando (guardia F5.2)."
            )
        print(f"[OK] tenant activo: {cuenta.get('tenantId')} ({cuenta.get('name')})")
    elif not expected_tenant_id:
        print("[DRY] (en vivo, pasar --expected-tenant-id del CLIENTE)")

    resultado: dict = {"slug": slug, "resource_group": rg, "webapp": webapp}

    # 0.5) Resource providers (las subs nuevas no traen NINGUNO registrado).
    # --wait bloquea hasta Registered; re-registrar es no-op.
    for ns in PROVIDERS:
        plan.az("provider", "register", "--namespace", ns, "--wait")

    # 1) App Registrations (una por bot) + secret + permisos Graph.
    # RESUME: si ya existe una app con ese display name, se reutiliza (el
    # secret se regenera igual — no persiste de corridas anteriores).
    bots = {}
    for kind in ("data", "activities"):
        nombre = f"{display} — {'Data Bot' if kind == 'data' else 'Activities Bot'}"
        ya = plan.existente("ad", "app", "list", "--display-name", nombre)
        if ya:
            app_id = ya[0]["appId"]
            print(f"[SKIP] app '{nombre}' ya existe ({app_id}) — reutilizando (resume)")
        else:
            app = plan.az("ad", "app", "create",
                          "--display-name", nombre,
                          "--sign-in-audience", "AzureADMyOrg",
                          "--required-resource-accesses", json.dumps([GRAPH_RESOURCE]),
                          capture=True)
            app_id = app["appId"] if app else f"<{kind}-app-id>"
        cred = plan.az("ad", "app", "credential", "reset",
                       "--id", app_id, "--years", "2",
                       capture=True, sensitive=False)
        secret_val = cred["password"] if cred else f"<{kind}-secret>"
        bots[kind] = {"app_id": app_id, "secret": secret_val, "nombre": nombre}

    # 2) Resource group + Azure Bots (RESUME: skip si ya existen)
    plan.az("group", "create", "-n", rg, "-l", location)
    endpoint_base = f"https://{webapp}.azurewebsites.net"
    for kind, ruta in (("data", "/api/messages"),
                       ("activities", "/api/activities/messages")):
        bot_name = f"{slug}-{kind}-bot"
        if plan.existente("bot", "show", "-g", rg, "-n", bot_name):
            print(f"[SKIP] bot {bot_name} ya existe (resume)")
            continue
        plan.az("bot", "create",
                "-g", rg, "-n", bot_name,
                "--app-type", "SingleTenant",
                "--appid", bots[kind]["app_id"],
                "--tenant-id", expected_tenant_id or "<tenant-cliente>",
                "--endpoint", f"{endpoint_base}{ruta}",
                "--sku", "F0")
        plan.az("bot", "msteams", "create", "-g", rg, "-n", bot_name)

    # 3) App Service + settings derivados del YAML. La región del plan puede
    # terminar siendo otra si la pedida no tiene cuota (subs nuevas).
    location_efectiva = _crear_plan_con_fallback(plan, rg, plan_name,
                                                 location, plan_sku)
    if plan.existente("webapp", "show", "-g", rg, "-n", webapp):
        print(f"[SKIP] webapp {webapp} ya existe (resume)")
    else:
        plan.az("webapp", "create", "-g", rg, "-n", webapp,
                "--plan", plan_name, "--runtime", "PYTHON:3.12")
    # Entrypoint + Always On: sin startup command la webapp da 404; sin
    # Always On el App Service duerme la instancia y el scheduler con ella.
    plan.az("webapp", "config", "set", "-g", rg, "-n", webapp,
            "--startup-file", STARTUP_CMD, "--always-on", "true")
    admin_token = secrets.token_hex(32)
    settings = _app_settings(slug, cfg, admin_token)
    settings.update({
        "MICROSOFT_APP_ID": bots["data"]["app_id"],
        "MICROSOFT_APP_PASSWORD": bots["data"]["secret"],
        "MICROSOFT_APP_TENANT_ID": expected_tenant_id or "<tenant-cliente>",
        "MICROSOFT_APP_TYPE": "SingleTenant",
        "ACTIVITIES_APP_ID": bots["activities"]["app_id"],
        "ACTIVITIES_APP_PASSWORD": bots["activities"]["secret"],
    })
    plan.az("webapp", "config", "appsettings", "set", "-g", rg, "-n", webapp,
            "--settings", *[f"{k}={v}" for k, v in settings.items()],
            sensitive=True)

    # 4) Key Vault (F5.3) — solo si el tenant declara secrets con fuente
    # keyvault en integrations.yaml. Siembra placeholders y cablea los app
    # settings como referencias @Microsoft.KeyVault (el App Service los
    # resuelve con su managed identity).
    integ = load_tenant_integrations(slug)
    pendientes: list[str] = []
    kv_secrets: dict[str, str] = {}  # APP_SETTING -> nombre en el vault
    if integ:
        for setting, ref in integ.all_secrets().items():
            if ref.keyvault:
                kv_secrets[setting] = ref.keyvault
            else:
                pendientes.append(f"{setting} (app setting manual)")
    else:
        pendientes = list(SECRETS_PENDIENTES_DEFAULT)

    kv = None
    if kv_secrets:
        kv = _kv_name(slug)
        plan.az("keyvault", "create", "-g", rg, "-n", kv, "-l", location,
                "--enable-rbac-authorization", "false")
        plan.az("webapp", "identity", "assign", "-g", rg, "-n", webapp)
        # En vivo, el principalId sale del comando anterior; en el plan se
        # referencia simbólicamente.
        plan.az("keyvault", "set-policy", "-n", kv,
                "--object-id", "<principalId-del-webapp>",
                "--secret-permissions", "get", "list")
        kv_refs: dict[str, str] = {}
        for setting, secret_name in sorted(kv_secrets.items()):
            plan.az("keyvault", "secret", "set", "--vault-name", kv,
                    "--name", secret_name, "--value", PLACEHOLDER_KV,
                    sensitive=True)
            kv_refs[setting] = (
                f"@Microsoft.KeyVault(SecretUri=https://{kv}.vault.azure.net/"
                f"secrets/{secret_name}/)"
            )
            pendientes.append(
                f"{setting}: az keyvault secret set --vault-name {kv} "
                f"--name {secret_name} --value <REAL>"
            )
        plan.az("webapp", "config", "appsettings", "set", "-g", rg, "-n", webapp,
                "--settings", *[f"{k}={v}" for k, v in kv_refs.items()])

    # 4.5) Admin consent de Graph — programático (estamos logueados como
    # admin del cliente). Si falla, no aborta: queda el link clásico.
    consent_otorgado = False
    if grant_consent:
        try:
            consent_otorgado = _grant_admin_consent(
                plan, [bots["data"]["app_id"], bots["activities"]["app_id"]])
        except SystemExit as e:
            print(f"[WARN] no pude otorgar el consent programático ({e}). "
                  "Usar el admin_consent_url del resumen.")

    # 5) Salida: consent link, siguiente comando, pendientes.
    tenant_ref = expected_tenant_id or "<tenant-cliente>"
    consent = (f"https://login.microsoftonline.com/{tenant_ref}/adminconsent"
               f"?client_id={bots['data']['app_id']}")
    resultado.update({
        "location_efectiva": location_efectiva,
        "consent_otorgado": consent_otorgado and not dry_run,
        "data_app_id": bots["data"]["app_id"],
        "activities_app_id": bots["activities"]["app_id"],
        "admin_api_token": admin_token if not dry_run else "<generado en vivo>",
        "admin_consent_url": consent,
        "siguiente_gen_teams_app": (
            f"python ops/gen_teams_app.py {slug} "
            f"--data-app-id {bots['data']['app_id']} "
            f"--activities-app-id {bots['activities']['app_id']}"
        ),
        "keyvault": kv,
        "secrets_pendientes": pendientes,
        "deploy": (
            f"python tools/build_bot_package.py && az webapp deploy -g {rg} "
            f"-n {webapp} --src-path bot_deploy.zip --type zip"
        ),
        "comandos": plan.ejecutados,
    })
    return resultado


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("slug")
    p.add_argument("--location", default="eastus2")
    p.add_argument("--resource-group", default=None)
    p.add_argument("--plan-sku", default="B1")
    p.add_argument("--expected-tenant-id", default=None,
                   help="tenant M365 del CLIENTE (guardia anti-equivocación)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-grant-consent", action="store_true",
                   help="NO otorgar el admin consent programático "
                        "(quedará el link para que lo apruebe el cliente)")
    args = p.parse_args()
    r = provision(args.slug, args.location, args.resource_group,
                  args.plan_sku, args.expected_tenant_id, args.dry_run,
                  grant_consent=not args.no_grant_consent)
    print("\n===== RESUMEN =====")
    print(json.dumps({k: v for k, v in r.items() if k != "comandos"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
