# Runbook — Fase 5: bots de la demo EN VIVO en Teams

Provisiona los bots de la demo (Andex) para que un prospecto chatee con el Data
Bot y llene el check-in en su propio Teams. **Esta fase requiere acción humana
en Azure/M365** (Claude no puede crear recursos cloud). Las Fases 0–4 ya corren
100% local sin esto; la 5 es el "extra wow" para reuniones presenciales.

> Principio: el demo vive en recursos **separados** de producción (otro tenant
> M365, otra App Registration, otro App Service). Nunca se mezcla con datos reales.

---

## 0. Pre-requisitos
- Un **tenant M365 de demo** (Microsoft 365 Developer Program da uno gratis con
  25 usuarios) — NO el tenant real de la empresa.
- Crear ahí los usuarios demo: `rsalinas@`, `cvega@`, `amora@`, `info@`,
  `quito@`, `mtipan@` (dominio `@<tudemo>.onmicrosoft.com` o `@andexdemo.com` si
  registrás el dominio). Ajustá `DEMO_EMAIL_DOMAIN` y `tenants/andex/config.yaml`
  al dominio que uses.
- Suscripción Azure (la misma de prod sirve; los recursos van en un Resource
  Group aparte, p.ej. `rg-andex-demo`).

## 1. App Registrations (2 bots)
En Azure AD del tenant demo → App registrations → New:
1. `andex-demo-data-bot` → copiá el **Application (client) ID** → es
   `DEMO_DATA_BOT_APP_ID`. Creá un client secret → `DEMO_DATA_BOT_SECRET`.
2. `andex-demo-activities-bot` → ídem → `DEMO_ACTIVITIES_BOT_APP_ID` / secret.
3. En cada una: API permissions → Microsoft Graph → **Application** →
   `Mail.Send`, `User.Read.All`, `Calendars.ReadWrite` → **Grant admin consent**.
4. "Allow public client flows": no hace falta (los bots usan client credentials).

## 2. Azure Bot resources (2)
Azure → "Azure Bot" → Create (uno por bot), en `rg-andex-demo`:
- Messaging endpoint del Data Bot:        `https://<tu-app>.azurewebsites.net/api/messages`
- Messaging endpoint del Activities Bot:  `https://<tu-app>.azurewebsites.net/api/activities/messages`
- En cada Azure Bot → Channels → habilitar **Microsoft Teams**.
- Usar como App ID el de la App Registration correspondiente del paso 1.

## 3. App Service (hosting del bot)
- Creá un App Service Linux Python en `rg-andex-demo`.
- Deploy del código: `python tools/build_bot_package.py` y `az webapp deploy`
  (mismo flujo que producción; ver `CLAUDE.md` y `azure_setup_checklist.md`).
- **App settings** (Configuration) — cargá el bloque del `.env.demo.example`:
  ```
  DEMO_MODE=1
  TENANT_CONFIG_SOURCE=yaml
  TENANT_SLUG=andex
  DEMO_EMAIL_DOMAIN=<tu dominio demo>
  DEMO_EMAIL_TO=demo@<tu dominio demo>
  DEMO_FROM_USER=amora@<tu dominio demo>
  STATE_DIR=/home/site/andex-demo-state
  CONTIFICO_API_TOKEN=demo
  HUBSPOT_TOKEN=demo
  ANTHROPIC_API_KEY=<real>
  MICROSOFT_APP_ID=<DEMO_DATA_BOT_APP_ID>
  MICROSOFT_APP_PASSWORD=<DEMO_DATA_BOT_SECRET>
  MICROSOFT_APP_TENANT_ID=<DEMO_TENANT_ID>
  ADMIN_API_TOKEN=<token random>
  BOT_ALLOWED_USERS_DATA=rsalinas@<dom>,cvega@<dom>,amora@<dom>
  TRACKER_TARGET_USER=amora@<dom>
  ```
  > El Activities Bot corre en el MISMO App Service (endpoint
  > `/api/activities/messages`); su App ID/secret van como settings del adapter
  > secundario (ver cómo prod maneja los 2 bots en `teams_bot.py`).

## 4. Sembrar el estado del equipo
Una vez desplegado, sembrá el estado demo (para que el check-in y el consolidado
salgan poblados). Localmente apuntando al mismo STATE_DIR, o vía un job:
```
DEMO_MODE=1 TENANT_SLUG=andex STATE_DIR=<el del App Service> python seed_demo_state.py
```

## 5. Empaquetar y subir los manifests de Teams
1. Generá los iconos: `python generate_bot_icons.py` (usa el verde corp.; para
   Andex podés cambiar el color a `#0B6E99` en ese script si querés).
2. Reemplazá los placeholders en:
   - `tenants/andex/teams/manifest_data.json` → `DEMO_DATA_BOT_APP_ID`
   - `tenants/andex/teams/manifest_activities.json` → `DEMO_ACTIVITIES_BOT_APP_ID`
3. Empaquetá cada manifest con `color.png` + `outline.png` en un `.zip`.
4. Teams (tenant demo) → Apps → **Upload a custom app** → subí ambos `.zip`.

## 6. Preflight ANTES de presentar (obligatorio)
Con las env vars del demo cargadas, corré el chequeo go/no-go:
```
python demo_preflight.py
```
Debe imprimir **✅ TODO OK**. Si algo sale ❌ (p.ej. un correo fuera del dominio
demo, o el tenant no cargó), NO salgas en vivo hasta resolverlo. Esto evita que
un dato real se filtre en la reunión.

## 7. Demo en vivo
- Abrí Teams del tenant demo (o pedile al prospecto que instale el custom app).
- Chateá con **Andex Data Bot**: "¿cuánto vendimos ayer?", "top deudores GYE".
- Mostrá el **check-in** del Activities Bot (dispará el card con el endpoint
  admin: `POST /admin/trigger-checkin` con `X-Admin-Token: <ADMIN_API_TOKEN>`).
- El resumen consolidado y los correos van todos a `DEMO_EMAIL_TO` (sandbox).

## 8. Teardown / higiene
- Los recursos viven en `rg-andex-demo` → borrá el Resource Group para limpiar.
- El STATE_DIR demo es desechable; `seed_demo_state.py` lo re-crea.
- Nunca cargues tokens de producción en el App Service demo.
