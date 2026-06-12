# Runbook operativo

Procedimientos para incidentes y operación diaria. Actualizado 2026-06-12.

## "No llegó el reporte de la mañana" (comercial, 8:00)

1. `GET https://biodegradables-bot-app.azurewebsites.net/health`
   - `sends_today` → ¿`morning_sales:<hoy>` está `sent`? Si está `sent`,
     el correo salió: revisar spam/reglas de Outlook del destinatario.
   - ¿`scheduler_running: false` o lease vencido? → restart del App Service;
     al arrancar, el catch-up envía lo que falte del día automáticamente.
2. ¿Llegó una ALERTA a malvarado@ ("Job morning_sales_report FALLÓ")? →
   el job agotó 3 reintentos. El motivo está en la alerta (típico: Contifico
   caído — el reporte YA NO se envía con $0, se retiene).
3. Disparo manual cuando la causa esté resuelta:
   - `POST /admin/trigger-morning-sales-job` (header `X-Admin-Token`), o
   - en la PC: `.\run_morning.bat` (el ledger del bot no lo ve — verificar
     antes en /health que no haya salido ya, para no duplicar).

## "No llegó la logística de Gabriela"

- Hoy corre en el Function App (`logistics_morning`, 13:00 UTC). Revisar
  Application Insights de `func-biodegradables-ec`.
- Manual: `POST https://func-biodegradables-ec.azurewebsites.net/api/trigger/logistics?code=<function-key>`
- Tras el cutover (`LOGISTICS_IN_BOT=1`): mismo flujo que el comercial.

## "Las actividades de X aparecen raras / no le llega el check-in"

1. `GET /admin/show-activities-for-user?user_email=...` — ver su state real.
2. Logs del bot: buscar `_user_email` — ¿X está resolviendo a
   `unidentified-<aad8>@`? → registrarlo:
   `POST /admin/aad-lookup/set` con `{aad_short, email}`.
3. El bot ya NO adivina identidades por nombre: un usuario nuevo SIEMPRE
   queda aislado hasta este registro. Es intencional.
4. Si un state quedó corrupto: llega alerta "STATE CORRUPTO" a malvarado@.
   El archivo en cuarentena (`*.corrupt-<ts>`) está junto al original en
   `/home/.claude-agent` — los datos NO se perdieron; safe_json restauró el
   `.bak` automáticamente si existía.

## Registrar un colaborador nuevo (checklist completo)

1. Env del App Service: agregar alias a `KNOWN_COLLABORATORS` y
   `TRACKER_EMAIL_TO_<ALIAS>`.
2. (Opcional) template `activities_template_<slug>.json` en el repo → PR.
3. El colaborador le escribe al Activities Bot; si queda `unidentified-*`,
   registrar AAD (ver arriba).
4. `POST /admin/seed-template-for-user` para sembrar su semana actual.

## Cutover de logística al bot (pendiente, una sola vez)

```powershell
# 1. Deploy del bot con el código actual (incluye el job gateado)
python tools/build_bot_package.py
az webapp deploy -g rg-biodegradables-prod -n biodegradables-bot-app --src-path bot_deploy.zip --type zip
# 2. En la MISMA ventana: apagar el timer azfunc y prender el flag del bot
az functionapp config appsettings set -n func-biodegradables-ec -g rg-biodegradables-prod --settings AzureWebJobs.logistics_morning.Disabled=true
az webapp config appsettings set -n biodegradables-bot-app -g rg-biodegradables-prod --settings LOGISTICS_IN_BOT=1
# 3. Mañana 8:05: verificar /health → logistics_morning:<fecha> = sent
```

## Tokens y credenciales

| Credencial | Vence | Renovación |
|---|---|---|
| MSAL refresh token (PC) | 90 días | `python pbi_cloud.py` interactivo (device-code). Síntomas de vencido: ApolloNotifier deja de mandar correos. |
| Secret del bot (`MICROSOFT_APP_PASSWORD`) | según App Registration | Azure Portal → rotar → actualizar App Service + scripts. |
| `ADMIN_API_TOKEN` | recomendado setear YA | App Service settings; separa el admin del secret OAuth. |
| `CONTIFICO_API_TOKEN`, `HUBSPOT_TOKEN`, `APOLLO_API_KEY`, `ANTHROPIC_API_KEY` | no vencen solos | rotar en el proveedor → actualizar env (User-scope local + App Service + Function App). |

## Mantenimiento recurrente

| Cuándo | Qué |
|---|---|
| Cada mes (día 1-3) | Verificar `PY_OVERRIDE` del mes en `core_config.py` vs Contifico real. |
| Noviembre | Cargar feriados del año siguiente en `core_config.EC_HOLIDAYS` con los traslados oficiales (el test `test_core_config.py` empieza a fallar si falta). |
| Tras cada deploy | `GET /health` + un vistazo a logs por `STATE CORRUPTO` / `CATCH-UP`. |
| Cada 90 días | Re-auth MSAL en la PC (ver tabla de tokens). |
