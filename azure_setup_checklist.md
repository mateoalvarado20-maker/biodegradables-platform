# Checklist: provisionar Azure Bot para Teams

Documento dirigido a quien tenga rol **Global Administrator** del tenant
Biodegradables Ecuador (`aec07a63-9c6c-4bc1-af6f-edb9aa826d0b`). Daniel.

Resultado al terminar: `teams_bot.py` corriendo en Azure App Service y
accesible desde Microsoft Teams como app interna. El bot ya tiene el código —
solo falta la infraestructura.

---

## Pre-requisitos

- [ ] Suscripción Azure activa con permisos de creación de recursos
- [ ] Plan de App Service: el plan Basic B1 (~$13/mes) alcanza para empezar.
      Si se quiere ahorrar, F1 Free funciona para pruebas pero se duerme.
- [ ] Acceso a Microsoft 365 admin center para subir el manifest a Teams

---

## Paso 1 — Crear el Azure Bot resource

1. Entrar a [portal.azure.com](https://portal.azure.com) → **Create a resource**
2. Buscar **"Azure Bot"** y crear
3. Configurar:
   - **Bot handle:** `biodegradables-data-bot`
   - **Subscription:** la corporativa
   - **Resource group:** crear nuevo `rg-biodegradables-bot` (o usar uno existente)
   - **Region:** `East US` (donde menos latencia tiene Bot Framework)
   - **Pricing tier:** **F0 (Free)** — alcanza para nuestro volumen
   - **Microsoft App ID:**
     - Type: **Single Tenant**
     - Creation type: **Create new Microsoft App ID**
   - **App type:** *SingleTenant*
4. **Review + create** → **Create**
5. Esperar ~2 min a que se provisione

## Paso 2 — Obtener credenciales

1. Una vez creado, ir al recurso → **Configuration** (menú izquierdo)
2. Copiar el **Microsoft App ID** (formato GUID)
3. Click en **Manage Password** → te lleva a App Registration → **Certificates & secrets**
4. **New client secret** → describir como "teams-bot-prod" → expira en **24 meses**
5. **Copiar el "Value"** (no el Secret ID). Solo se muestra UNA vez.
6. Anotar también el **Tenant ID** (ya lo sabemos: `aec07a63-9c6c-4bc1-af6f-edb9aa826d0b`)

## Paso 3 — Agregar API permissions a la App Registration

En la App Registration creada (la que generó el bot):

1. **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**
2. Buscar y agregar:
   - `Mail.Send` (para mandar correos)
   - `Mail.ReadWrite` (para reply_agent)
   - `Calendars.ReadWrite` (para recordatorios)
3. **Grant admin consent for Biodegradables Ecuador** (botón azul)

*Nota: estos permisos los necesita el bot para integrarse con los otros agentes
existentes. Power BI Service ya está cubierto por la app `claude-agent`
existente, no necesita duplicarse acá.*

## Paso 4 — Crear App Service para hostear el código

Opción A — **App Service** (clásico, predecible):

1. **Create a resource** → **Web App**
2. Configurar:
   - **Name:** `biodegradables-bot-app` (debe ser único globalmente)
   - **Publish:** Code
   - **Runtime stack:** Python 3.11 (o más reciente)
   - **OS:** Linux
   - **Region:** misma que el bot (East US)
   - **Pricing plan:** Basic B1 (~$13/mes) o F1 Free
3. Create → esperar ~5 min

Opción B — **Container App** (más moderno, escala a 0): saltar si esto es nuevo.

## Paso 5 — Configurar variables de entorno en el App Service

En el App Service → **Configuration** → **Application settings** → **New application setting**:

| Name | Value |
|---|---|
| `MICROSOFT_APP_ID` | (del Paso 2) |
| `MICROSOFT_APP_PASSWORD` | (del Paso 2, el Value del secret) |
| `MICROSOFT_APP_TENANT_ID` | `aec07a63-9c6c-4bc1-af6f-edb9aa826d0b` |
| `MICROSOFT_APP_TYPE` | `SingleTenant` |
| `ANTHROPIC_API_KEY` | (copiar de la PC de Mateo) |
| `GRAPH_CLIENT_ID` | `8b85d6bf-a34c-4482-821c-ab7a70717776` |
| `GRAPH_TENANT_ID` | `aec07a63-9c6c-4bc1-af6f-edb9aa826d0b` |
| `HUBSPOT_TOKEN` | (copiar de la PC de Mateo) |
| `APOLLO_API_KEY` | (copiar de la PC de Mateo) |
| `BOT_ALLOWED_USERS` | `dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com,malvarado@biodegradablesecuador.com` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |
| `WEBSITES_PORT` | `3978` |

**Save** y reiniciar el App Service.

## Paso 6 — Conectar el bot al App Service

En el Azure Bot resource → **Configuration**:

- **Messaging endpoint:** `https://biodegradables-bot-app.azurewebsites.net/api/messages`
  (el dominio del App Service del Paso 4 + `/api/messages`)

**Apply**.

## Paso 7 — Desplegar el código

Desde la PC de Mateo:

```powershell
# Empaquetar el repo (excluir cache, logs, .json sensibles)
cd C:\Users\Mateo
Compress-Archive -Path teams_bot.py,ask_agent.py,pbi_cloud.py,hubspot_client.py,activity_state.py,activity_tracker.py,weekly_report.py,activities_template.json,requirements_bot.txt -DestinationPath bot_deploy.zip -Force

# Deploy con Azure CLI (requiere `az login` previo)
az webapp deployment source config-zip --resource-group rg-biodegradables-bot --name biodegradables-bot-app --src bot_deploy.zip
```

Verificar que arrancó:
```powershell
curl https://biodegradables-bot-app.azurewebsites.net/health
# Debe responder: {"status":"healthy"}
```

## Paso 8 — Habilitar canal Microsoft Teams en el bot

En el Azure Bot resource → **Channels**:

1. Click en el ícono de **Microsoft Teams**
2. Aceptar términos → **Apply**
3. El canal queda en estado "Running"

## Paso 9 — Subir el manifest a Teams

1. Editar `manifest.json` (en `C:\Users\Mateo\`):
   - Reemplazar los 2 `REEMPLAZAR_CON_MICROSOFT_APP_ID` con el App ID real
2. Empaquetar:
   ```powershell
   cd C:\Users\Mateo
   Compress-Archive -Path manifest.json,color.png,outline.png -DestinationPath teams_app.zip -Force
   ```
3. Abrir Microsoft Teams → **Apps** → **Manage your apps** → **Upload an app** →
   **Upload an app to your org's app catalog** (necesita Teams admin)
4. Subir `teams_app.zip`
5. Una vez aprobado, los usuarios en `BOT_ALLOWED_USERS` pueden buscar el bot en
   Apps y agregarlo a un chat 1:1

## Paso 10 — Smoke test

1. Mateo abre el bot en Teams (debería saludar con el mensaje de bienvenida)
2. Escribir `/help` → debe responder con la ayuda
3. Escribir "cuanto vendimos hoy" → debe responder con dato de Power BI

Si algo falla:
- Logs del App Service: Azure Portal → App Service → **Log stream**
- Logs del bot: Azure Bot resource → **Test in Web Chat**

---

## Después del setup

Cuando todo esté funcionando, escribirle a Mateo para:
1. Confirmar las env vars en la PC también (`MICROSOFT_APP_ID`, etc.) por si se
   quiere testear local con Bot Framework Emulator
2. Arrancar la Fase 2 del tracker: convertir comandos CLI en slash commands
   (`/done`, `/progress`, `/add`, `/status`) y la Adaptive Card del lunes
3. Conectar el sistema de despacho de logística (`dispatch.py`) con Teams

## Costos mensuales estimados

| Recurso | Tier | Costo USD/mes |
|---|---|---|
| Azure Bot | F0 Free | $0 |
| App Service Plan | Basic B1 | ~$13 |
| Storage (logs) | Standard | ~$1 |
| **Total** | | **~$14** |

Si se quiere bajar a $0: usar App Service F1 Free (la app se duerme tras 20 min
de inactividad, primer mensaje del día tarda ~10 segundos en responder).
